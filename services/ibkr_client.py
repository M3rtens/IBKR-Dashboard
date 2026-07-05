"""
IBKR API client wrapper using ib_insync.
Connects to TWS or IB Gateway and fetches portfolio/account data.

ib_insync is built on asyncio and requires an event loop bound to the thread
that uses the IB object. A Dash/Flask app serves requests on a pool of worker
threads, none of which owns an event loop -- calling IB.connect() from there
raises "There is no current event loop in thread ...".

To stay safe, this client runs ALL IB operations on a single dedicated
background thread that owns one event loop. The connection is opened there and
the portfolio is refreshed on a timer. Dash callbacks never touch the IB object
directly; they only read the thread-safe cached snapshot.
"""

import asyncio
import math
import os
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from ib_insync import IB, Contract, util

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set IBKR_DEBUG=1 to see per-request historical-data tracing (which contract
# is fetched, how long it took, how many bars came back).
if os.environ.get("IBKR_DEBUG", "").strip().lower() in ("1", "true", "yes"):
    logger.setLevel(logging.DEBUG)

# Suppress ib_insync verbose logging
util.logToConsole(logging.WARNING)

@dataclass
class ConnectionConfig:
    # API ports: 7497=TWS paper | 7496=TWS live | 4002=IBG paper | 4001=IBG live
    # Defaults can be overridden via IBKR_HOST / IBKR_PORT / IBKR_CLIENT_ID env vars.
    host: str = field(default_factory=lambda: os.environ.get("IBKR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("IBKR_PORT", "4001")))
    client_id: int = field(default_factory=lambda: int(os.environ.get("IBKR_CLIENT_ID", "10")))
    timeout: int = 10
    refresh_seconds: float = 5.0  # how often the worker re-reads the portfolio


@dataclass
class AccountSnapshot:
    net_liquidation: float = 0.0
    total_cash: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    day_pnl: float = 0.0
    gross_position_value: float = 0.0
    buying_power: float = 0.0
    currency: str = "USD"
    account_id: str = ""
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)


_EMPTY_HOLDINGS_COLUMNS = [
    "Symbol", "Type", "Exchange", "Currency", "Description", "Position",
    "Avg Cost", "Market Price", "Market Value", "% Port",
    "Unrealized P&L", "Realized P&L", "P&L %", "Day P&L", "Day Pct",
]


class IBKRClient:
    """Thread-safe IBKR client.

    All IB I/O happens on a single dedicated worker thread that owns one
    asyncio event loop. Consumers (Dash callbacks) only ever read the cached
    snapshot / connection status, which are guarded by a lock.
    """

    def __init__(self, config: Optional[ConnectionConfig] = None):
        self.config = config or ConnectionConfig()
        self._lock = threading.Lock()
        self._snapshot = AccountSnapshot()
        self._connected = False
        self._last_update: Optional[float] = None
        self._error: Optional[str] = None

        # The IB object and the event loop are created/owned by the worker
        # thread only. Never touch them from another thread.
        self._ib: Optional[IB] = None
        self._pnl = None          # account-level PnL subscription (reqPnL)
        self._account: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()

        # Per-symbol daily price change {symbol: (day_dollar, day_pct)}
        self._daily_changes: dict = {}
        self._daily_chg_ts: float = 0.0
        self._daily_chg_fetching: bool = False

        # Per-symbol 30-day close series {symbol: [close, ...]} for sparklines
        self._spark_data: dict = {}
        self._spark_ts: float = 0.0
        self._spark_fetching: bool = False

        # Per-contract locks for historical-data requests. IBKR cancels
        # concurrent/duplicate requests for the SAME contract (Error 162 /
        # timeouts) — which is what happened when several fetch paths (daily
        # changes, sparklines, benchmark, history rebuild) requested e.g. BND or
        # SPY at once. Every request goes through _req_historical(), which holds
        # the lock for that one contract only, so unrelated contracts still run
        # in parallel. The dict is only touched from coroutines on the worker
        # loop, so it needs no threading lock.
        self._hist_locks: dict = {}
        # Global cap on simultaneous historical requests. IBKR only services a
        # limited number at once (~50) and paces hard beyond that; firing every
        # holding + benchmark + country ETF at startup floods it and the excess
        # requests simply never respond. This throttles without fully
        # serialising. Overridable via env.
        self._hist_sem = asyncio.Semaphore(
            int(os.environ.get("IBKR_HIST_CONCURRENCY", "5")))
        # Upper bound on a single historical request; a contract with no data or
        # no market-data permission returns nothing and would otherwise hold its
        # slot until ib_insync's own (long) timeout. Overridable via env.
        self._hist_req_timeout = float(os.environ.get("IBKR_HIST_TIMEOUT", "20"))

        # Historical portfolio value cache
        self._hist_df: Optional[pd.DataFrame] = None
        self._hist_ts: float = 0.0
        self._hist_fetching: bool = False

        # Benchmark price series cache (SPY, QQQ, STW)
        self._bench_df: Optional[pd.DataFrame] = None
        self._bench_ts: float = 0.0
        self._bench_fetching: bool = False

        # Security search cache {symbol: dict}
        self._sec_cache: dict = {}
        self._sec_cache_ts: dict = {}
        # Security detail cache {symbol: dict} — full detail including price/chart
        self._sec_detail_cache: dict = {}
        self._sec_detail_ts: dict = {}
        self._sec_fetching: dict = {}

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ibkr-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker thread to disconnect and exit."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.config.timeout + 2)

    def _run(self) -> None:
        """Body of the worker thread: own an event loop and drive IB."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ib = IB()
        self._started.set()
        try:
            loop.run_until_complete(self._worker_main())
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("IBKR worker crashed: %s", exc)
            with self._lock:
                self._error = str(exc)
                self._connected = False
        finally:
            try:
                if self._ib is not None and self._ib.isConnected():
                    self._ib.disconnect()
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            logger.info("IBKR worker stopped")

    async def _worker_main(self) -> None:
        """Connect (with reconnect) and refresh on a timer until stopped."""
        while not self._stop.is_set():
            if not self._ib.isConnected():
                await self._connect_async()
                if not self._ib.isConnected():
                    # Back off before retrying a failed connection.
                    await asyncio.sleep(min(self.config.timeout, 5))
                    continue
            try:
                await self._refresh_async()
            except Exception as exc:
                logger.error("Error refreshing data: %s", exc)
                with self._lock:
                    self._error = str(exc)
            # Per-symbol daily changes + sparklines come from the API only when
            # IBKR is the chosen market-data source; in yfinance mode these just
            # time out (no market-data subscription), so skip them entirely.
            _ibkr_md = os.environ.get(
                "MARKET_DATA_SOURCE", "yfinance").strip().lower() == "ibkr"
            # Refresh per-symbol daily changes every 5 minutes
            if (_ibkr_md and time.time() - self._daily_chg_ts > 300
                    and not self._daily_chg_fetching):
                self._daily_chg_ts = time.time()
                self._daily_chg_fetching = True
                asyncio.ensure_future(self._fetch_daily_changes_async())
            # Refresh per-symbol 30-day sparkline data every 10 minutes
            if (_ibkr_md and time.time() - self._spark_ts > 600
                    and not self._spark_fetching):
                self._spark_ts = time.time()
                self._spark_fetching = True
                asyncio.ensure_future(self._fetch_sparklines_async())
            await asyncio.sleep(self.config.refresh_seconds)

    async def _connect_async(self) -> None:
        """Open the IB connection on the worker's event loop."""
        try:
            await self._ib.connectAsync(
                self.config.host,
                self.config.port,
                clientId=self.config.client_id,
                timeout=self.config.timeout,
                readonly=True,   # read-only; no accidental orders
            )
            with self._lock:
                self._connected = True
                self._error = None
            logger.info(
                "Connected to IBKR at %s:%d", self.config.host, self.config.port
            )

            # Without a real-time market-data subscription, TRADES historical
            # requests hang with no response. Setting delayed mode lets
            # reqHistoricalData return delayed bars instead of timing out.
            # 1=live (needs subscription), 2=frozen, 3=delayed, 4=delayed-frozen.
            try:
                md_type = int(os.environ.get("IBKR_MARKET_DATA_TYPE", "3"))
                self._ib.reqMarketDataType(md_type)
                logger.info("Market data type set to %d "
                            "(1=live, 2=frozen, 3=delayed, 4=delayed-frozen)",
                            md_type)
            except Exception as exc:
                logger.warning("reqMarketDataType failed: %s: %s",
                               type(exc).__name__, exc)

            # Subscribe to account-level P&L (daily / unrealized / realized).
            # These are NOT available as accountValues tags, so reqPnL is the
            # canonical source for Day P&L.
            managed = self._ib.managedAccounts()
            self._account = managed[0] if managed else ""
            if self._account:
                self._pnl = self._ib.reqPnL(self._account)
        except Exception as exc:
            self._pnl = None
            with self._lock:
                self._connected = False
                self._error = str(exc)
            logger.error("IBKR connection failed: %s", exc)

    # ------------------------------------------------------------------
    # Data fetching (runs on the worker thread / event loop)
    # ------------------------------------------------------------------

    async def _refresh_async(self) -> None:
        """Fetch fresh portfolio + account data and cache it."""
        snapshot = AccountSnapshot()

        # -- Account summary values -----------------------------------
        account_values = self._ib.accountValues()
        managed = self._ib.managedAccounts()
        snapshot.account_id = managed[0] if managed else ""

        # The account's base currency is whatever the consolidated
        # NetLiquidation entry is denominated in (e.g. AUD, USD, EUR).
        # Hardcoding "USD" silently drops every monetary tag for non-USD
        # accounts, so detect it instead.
        base_currency = next(
            (av.currency for av in account_values
             if av.tag == "NetLiquidation" and av.currency),
            "",
        )
        snapshot.currency = base_currency or "USD"
        accepted = {base_currency, "BASE", ""}

        av_map: dict[str, str] = {}
        for av in account_values:
            if av.currency in accepted:
                av_map[av.tag] = av.value

        def _float(key: str) -> float:
            try:
                return float(av_map.get(key, 0.0))
            except (ValueError, TypeError):
                return 0.0

        snapshot.net_liquidation      = _float("NetLiquidation")
        snapshot.total_cash           = _float("TotalCashValue")
        snapshot.gross_position_value = _float("GrossPositionValue")
        snapshot.buying_power         = _float("BuyingPower")

        # -- Portfolio positions --------------------------------------
        portfolio_items = self._ib.portfolio()
        rows = []
        sum_unrealized = 0.0
        sum_realized = 0.0
        for item in portfolio_items:
            contract = item.contract
            symbol   = contract.symbol
            sec_type = contract.secType
            exchange = contract.primaryExchange or contract.exchange

            avg_cost        = item.averageCost
            position        = item.position
            market_price    = item.marketPrice
            market_value    = item.marketValue
            unrealized_pnl  = item.unrealizedPNL or 0.0
            realized_pnl    = item.realizedPNL or 0.0
            sum_unrealized += unrealized_pnl
            sum_realized   += realized_pnl

            # Percentage P&L
            cost_basis = abs(avg_cost * position) if position != 0 else 0
            pnl_pct    = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0

            day_chg = self._daily_changes.get(symbol, (0.0, 0.0))
            rows.append({
                "Symbol":           symbol,
                "Type":             sec_type,
                "Exchange":         exchange,
                "Currency":         contract.currency,
                "Description":      contract.description or contract.localSymbol or symbol,
                "Position":         position,
                "Avg Cost":         round(avg_cost, 4),
                "Market Price":     round(market_price, 4),
                "Market Value":     round(market_value, 2),
                "Unrealized P&L":   round(unrealized_pnl, 2),
                "Realized P&L":     round(realized_pnl, 2),
                "P&L %":            round(pnl_pct, 2),
                "Day P&L":          day_chg[0],
                "Day Pct":          day_chg[1],
            })

        if rows:
            holdings = pd.DataFrame(rows)
            # Each position's share of total market value (by absolute value,
            # so shorts contribute their magnitude rather than reducing it).
            total_mv = holdings["Market Value"].abs().sum()
            holdings["% Port"] = (
                (holdings["Market Value"].abs() / total_mv * 100).round(2)
                if total_mv else 0.0
            )
            holdings = (
                holdings[_EMPTY_HOLDINGS_COLUMNS]
                .sort_values("Market Value", ascending=False)
                .reset_index(drop=True)
            )
        else:
            holdings = pd.DataFrame(columns=_EMPTY_HOLDINGS_COLUMNS)
        snapshot.holdings = holdings

        # -- P&L totals -----------------------------------------------
        # accountValues has no usable UnrealizedPnL/RealizedPnL/DailyPnL tags
        # on this account, so prefer the account-level reqPnL subscription and
        # fall back to summing per-position values.
        def _valid(x) -> bool:
            return x is not None and not (isinstance(x, float) and math.isnan(x))

        pnl = self._pnl
        snapshot.unrealized_pnl = (
            pnl.unrealizedPnL if pnl and _valid(pnl.unrealizedPnL) else sum_unrealized
        )
        snapshot.realized_pnl = (
            pnl.realizedPnL if pnl and _valid(pnl.realizedPnL) else sum_realized
        )
        snapshot.day_pnl = pnl.dailyPnL if pnl and _valid(pnl.dailyPnL) else 0.0

        with self._lock:
            self._snapshot = snapshot
            self._last_update = time.time()
            self._error = None

    # ------------------------------------------------------------------
    # Historical portfolio value (called from Dash callbacks / any thread)
    # ------------------------------------------------------------------

    def get_portfolio_history(self) -> pd.DataFrame:
        """Non-blocking. Returns a DataFrame(date, value) of daily portfolio values.

        First call triggers a background fetch and returns an empty DataFrame.
        Subsequent calls return the cached result. Cache TTL is 4 hours.
        Values are reconstructed from current position quantities multiplied by
        historical daily closing prices, summed across all holdings, plus cash.
        Note: does not account for past position changes — gives an approximation.
        """
        if self._hist_df is not None and time.time() - self._hist_ts < 4 * 3600:
            return self._hist_df
        if not self._connected or self._loop is None:
            return pd.DataFrame()
        if not self._hist_fetching:
            self._hist_fetching = True
            t = threading.Thread(
                target=self._fetch_hist_thread, daemon=True, name="ibkr-hist"
            )
            t.start()
        return self._hist_df if self._hist_df is not None else pd.DataFrame()

    def _fetch_hist_thread(self) -> None:
        """Thread worker: submits _build_hist_series to the IB event loop and caches."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._build_hist_series(), self._loop
            )
            df = future.result(timeout=120)
            if df is not None and not df.empty:
                with self._lock:
                    self._hist_df = df
                    self._hist_ts = time.time()
                logger.info("Portfolio history cached: %d trading days", len(df))
        except Exception as exc:
            logger.error("Portfolio history fetch failed: %s: %s",
                         type(exc).__name__, exc)
        finally:
            self._hist_fetching = False

    async def _build_hist_series(self) -> pd.DataFrame:
        """Fetch 3Y of daily closes for each holding; reconstruct portfolio values."""
        import datetime as _dt

        snap = self.cached_snapshot
        if snap.holdings.empty:
            return pd.DataFrame()

        cash = snap.total_cash
        symbol_series: dict = {}

        for _, row in snap.holdings.iterrows():
            sym = str(row["Symbol"])
            sec_type = str(row.get("Type", "STK"))
            exchange = str(row.get("Exchange") or "")
            position = float(row["Position"])

            if abs(position) < 0.001 or sec_type in ("CASH", "CMDTY"):
                continue

            contract = Contract(
                symbol=sym,
                secType=sec_type,
                exchange="SMART",
                primaryExchange=exchange if exchange not in ("", "SMART") else "",
                currency=str(row.get("Currency") or "USD"),
            )

            try:
                bars = await self._req_historical(
                    contract,
                    endDateTime="",
                    durationStr="3 Y",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                )
                daily = {}
                for bar in bars:
                    d = bar.date
                    if hasattr(d, "date"):
                        d = d.date()
                    elif isinstance(d, str):
                        d = _dt.date.fromisoformat(d[:10])
                    daily[d] = bar.close * position
                if daily:
                    symbol_series[sym] = daily
                await asyncio.sleep(0.3)  # respect IBKR pacing limits
            except Exception as exc:
                logger.warning("Historical data unavailable for %s: %s", sym, exc)


        if not symbol_series:
            return pd.DataFrame()

        # Honour account open date so fabricated pre-account history is excluded.
        _start_env = os.environ.get("IBKR_PORTFOLIO_START", "").strip()
        try:
            _min_date = _dt.date.fromisoformat(_start_env) if _start_env else None
        except ValueError:
            _min_date = None

        all_dates = sorted(set().union(*[s.keys() for s in symbol_series.values()]))
        if _min_date:
            all_dates = [d for d in all_dates if d >= _min_date]
        if not all_dates:
            return pd.DataFrame()

        # Build a wide DataFrame (date × symbol) then forward-fill gaps so that
        # exchange holidays / missing bars don't zero-out a position's contribution.
        df_sym = pd.DataFrame(symbol_series, index=all_dates)
        df_sym = df_sym.ffill().fillna(0.0)
        values = (cash + df_sym.sum(axis=1)).tolist()

        # ── Convert native-currency series to account base currency (AUD) ──────
        # Historical bars are in each stock's native currency (USD, EUR, …) so
        # the raw sum is in mixed/USD terms.  net_liquidation is in the account's
        # base currency (AUD).  Scale the entire series by the ratio of the live
        # AUD total to the raw reconstructed total so the chart is expressed in
        # AUD throughout.  This applies the *current* FX rate uniformly — an
        # approximation, but far better than leaving the series in USD.
        raw_last = values[-1] if values else 0
        live_total = snap.net_liquidation
        if raw_last > 0 and live_total > 0:
            # Scale positions only; cash is already in base currency.
            raw_positions_last = raw_last - cash
            live_positions = live_total - cash
            if raw_positions_last > 0:
                fx_scale = live_positions / raw_positions_last
            else:
                fx_scale = live_total / raw_last
            values = [cash + (v - cash) * fx_scale for v in values]

        # ── Pin the most-recent data point to the exact live net liquidation ────
        # After scaling, the last reconstructed value already ≈ net_liquidation.
        # Pinning it exactly removes any residual rounding / intraday drift so
        # the chart tip always matches the KPI card.
        today = _dt.date.today()
        if live_total and live_total > 0:
            if all_dates and all_dates[-1] == today:
                values[-1] = live_total
            elif all_dates and all_dates[-1] < today:
                all_dates.append(today)
                values.append(live_total)

        return pd.DataFrame({"date": all_dates, "value": values})

    async def _req_historical(self, contract, **kwargs):
        """Per-contract-serialised, timeout-bounded reqHistoricalDataAsync.

        Serialises requests for the SAME contract (IBKR cancels concurrent
        duplicates with Error 162), lets unrelated contracts run in parallel,
        and bounds each request so a contract that never returns data can't
        stall its queue. Returns [] on timeout/error so callers fall back
        gracefully.
        """
        # Global override of whatToShow. Accounts without a last-sale/TRADES
        # market-data subscription get no response for whatToShow="TRADES";
        # setting IBKR_WHAT_TO_SHOW=MIDPOINT (bid/ask-derived) often returns data
        # instead. Applied here so every call site is covered from one place.
        override = os.environ.get("IBKR_WHAT_TO_SHOW", "").strip()
        if override:
            kwargs["whatToShow"] = override
        sym = getattr(contract, "symbol", "?")
        dur = kwargs.get("durationStr", "?")
        wts = kwargs.get("whatToShow", "?")
        key = (sym, getattr(contract, "secType", ""),
               getattr(contract, "currency", ""), getattr(contract, "exchange", ""))
        lock = self._hist_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._hist_locks[key] = lock

        async with lock, self._hist_sem:
            t0 = time.time()
            logger.debug("histreq start: %s dur=%s show=%s", sym, dur, wts)
            try:
                bars = await asyncio.wait_for(
                    self._ib.reqHistoricalDataAsync(contract, **kwargs),
                    timeout=self._hist_req_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "histreq timeout: %s dur=%s show=%s got no response in %.0fs "
                    "(likely no data / no market-data permission) - skipping",
                    sym, dur, wts, self._hist_req_timeout)
                return []
            except Exception as exc:
                logger.warning("histreq error: %s dur=%s show=%s: %s: %s",
                               sym, dur, wts, type(exc).__name__, exc)
                return []
            logger.debug("histreq done: %s dur=%s -> %d bars in %.2fs",
                         sym, dur, len(bars) if bars else 0, time.time() - t0)
            return bars

    async def _fetch_daily_changes_async(self) -> None:
        """Fetch the last 2 trading days of closes per position to compute daily change."""
        import datetime as _dt
        try:
            snap = self.cached_snapshot
            if snap.holdings.empty:
                return
            changes = {}
            for _, row in snap.holdings.iterrows():
                sym      = str(row["Symbol"])
                sec_type = str(row.get("Type", "STK"))
                exchange = str(row.get("Exchange") or "")
                position = float(row["Position"])
                mv       = float(row["Market Value"])
                if abs(position) < 0.001 or sec_type in ("CASH", "CMDTY"):
                    continue
                contract = Contract(
                    symbol=sym, secType=sec_type, exchange="SMART",
                    primaryExchange=exchange if exchange not in ("", "SMART") else "",
                    currency=str(row.get("Currency") or "USD"),
                )
                try:
                    bars = await self._req_historical(
                        contract, endDateTime="", durationStr="2 D",
                        barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
                    )
                    if len(bars) >= 2:
                        prev_close = bars[-2].close
                        curr_close = bars[-1].close
                        if prev_close and prev_close > 0:
                            pct    = (curr_close - prev_close) / prev_close * 100
                            dollar = abs(mv) * pct / 100
                            changes[sym] = (round(dollar, 2), round(pct, 2))
                    await asyncio.sleep(0.15)
                except Exception as exc:
                    logger.debug("Daily change fetch failed for %s: %s", sym, exc)
            self._daily_changes = changes
            logger.info("Daily changes updated: %d symbols", len(changes))
        except Exception as exc:
            logger.error("_fetch_daily_changes_async error: %s", exc)
        finally:
            self._daily_chg_fetching = False

    # ------------------------------------------------------------------
    # Per-symbol 30-day sparkline data
    # ------------------------------------------------------------------

    def get_hold_sparklines(self) -> dict:
        """Non-blocking. Returns {symbol: [close, ...]} of last ~30 trading days.

        First call triggers a background fetch and returns empty dict.
        Subsequent calls return the cached result. Cache TTL is 10 minutes.
        """
        if self._spark_data and time.time() - self._spark_ts < 600:
            return self._spark_data
        if not self._connected or self._loop is None:
            return {}
        if not self._spark_fetching:
            self._spark_ts = time.time()
            self._spark_fetching = True
            threading.Thread(
                target=self._fetch_spark_thread, daemon=True, name="ibkr-spark"
            ).start()
        return self._spark_data

    def _fetch_spark_thread(self) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._fetch_sparklines_async(), self._loop
            )
            future.result(timeout=120)
        except Exception as exc:
            logger.error("Sparkline fetch failed: %s", exc)
        finally:
            self._spark_fetching = False

    async def _fetch_sparklines_async(self) -> None:
        """Fetch last ~30 trading days of closes per position for sparklines."""
        try:
            snap = self.cached_snapshot
            if snap.holdings.empty:
                return
            sparklines = {}
            for _, row in snap.holdings.iterrows():
                sym      = str(row["Symbol"])
                sec_type = str(row.get("Type", "STK"))
                exchange = str(row.get("Exchange") or "")
                position = float(row["Position"])
                if abs(position) < 0.001 or sec_type in ("CASH", "CMDTY"):
                    continue
                contract = Contract(
                    symbol=sym, secType=sec_type, exchange="SMART",
                    primaryExchange=exchange if exchange not in ("", "SMART") else "",
                    currency=str(row.get("Currency") or "USD"),
                )
                try:
                    bars = await self._req_historical(
                        contract, endDateTime="", durationStr="30 D",
                        barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
                    )
                    if bars:
                        closes = [bar.close for bar in bars]
                        sparklines[sym] = closes
                    await asyncio.sleep(0.15)
                except Exception as exc:
                    logger.debug("Sparkline fetch failed for %s: %s", sym, exc)
            with self._lock:
                self._spark_data = sparklines
            logger.info("Sparkline data updated: %d symbols", len(sparklines))
        except Exception as exc:
            logger.error("_fetch_sparklines_async error: %s", exc)
        finally:
            self._spark_fetching = False

    # ------------------------------------------------------------------
    # Benchmark price history (SPY / QQQ / STW)
    # ------------------------------------------------------------------

    # Contracts for the three benchmarks shown in the UI
    _BENCH_CONTRACTS = {
        "sp500": Contract(symbol="SPY", secType="STK", exchange="SMART",
                          primaryExchange="ARCA",   currency="USD"),
        "ndx":   Contract(symbol="QQQ", secType="STK", exchange="SMART",
                          primaryExchange="NASDAQ", currency="USD"),
        # STW = SPDR S&P/ASX 200 ETF — best liquid proxy for the ASX 200
        "asx":   Contract(symbol="STW", secType="STK", exchange="ASX",
                          currency="AUD"),
    }

    def get_benchmark_history(self) -> Optional[pd.DataFrame]:
        """Non-blocking. Returns a DataFrame(date, sp500, ndx, asx) of raw closes.

        Prices are NOT normalised — callers rescale to the portfolio start value.
        Returns None when data is not yet available or not connected.
        """
        if self._bench_df is not None and time.time() - self._bench_ts < 4 * 3600:
            return self._bench_df
        if not self._connected or self._loop is None:
            return None
        if not self._bench_fetching:
            self._bench_fetching = True
            threading.Thread(
                target=self._fetch_bench_thread, daemon=True, name="ibkr-bench"
            ).start()
        return self._bench_df

    def _fetch_bench_thread(self) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._build_bench_series(), self._loop
            )
            df = future.result(timeout=120)
            if df is not None and not df.empty:
                with self._lock:
                    self._bench_df = df
                    self._bench_ts = time.time()
                logger.info("Benchmark history cached: %d trading days", len(df))
        except Exception as exc:
            logger.error("Benchmark history fetch failed: %s: %s",
                         type(exc).__name__, exc)
        finally:
            self._bench_fetching = False

    async def _build_bench_series(self) -> Optional[pd.DataFrame]:
        """Fetch 3Y of daily closes for SPY, QQQ, and STW."""
        import datetime as _dt

        series: dict = {}
        for key, contract in self._BENCH_CONTRACTS.items():
            try:
                bars = await self._req_historical(
                    contract,
                    endDateTime="",
                    durationStr="3 Y",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                )
                daily = {}
                for bar in bars:
                    d = bar.date
                    if hasattr(d, "date"):
                        d = d.date()
                    elif isinstance(d, str):
                        d = _dt.date.fromisoformat(d[:10])
                    daily[d] = bar.close
                if daily:
                    series[key] = daily
                    logger.info("Benchmark %s: %d bars", key, len(daily))
                await asyncio.sleep(0.3)
            except Exception as exc:
                logger.warning("Benchmark data unavailable for %s: %s", key, exc)

        if not series:
            return None

        _start_env = os.environ.get("IBKR_PORTFOLIO_START", "").strip()
        try:
            _min_date = _dt.date.fromisoformat(_start_env) if _start_env else None
        except ValueError:
            _min_date = None

        all_dates = sorted(set().union(*[s.keys() for s in series.values()]))
        if _min_date:
            all_dates = [d for d in all_dates if d >= _min_date]
        if not all_dates:
            return None

        df = pd.DataFrame(series, index=all_dates)
        df = df.ffill().bfill()
        return df.reset_index().rename(columns={"index": "date"})

    # ------------------------------------------------------------------
    # Country ETF performance (world map data)
    # ------------------------------------------------------------------

    # (country_code, country_name, etf_symbol, exchange, currency)
    _COUNTRY_ETFS = [
        ("USA",    "United States",   "SPY",  "SMART",    "USD"),
        ("AUS",    "Australia",       "STW",  "ASX",      "AUD"),
        ("JPN",    "Japan",           "EWJ",  "SMART",    "USD"),
        ("GBR",    "United Kingdom",  "EWU",  "SMART",    "USD"),
        ("DEU",    "Germany",         "EWG",  "SMART",    "USD"),
        ("FRA",    "France",          "EWQ",  "SMART",    "USD"),
        ("CAN",    "Canada",          "EWC",  "SMART",    "USD"),
        ("CHN",    "China",           "FXI",  "SMART",    "USD"),
        ("BRA",    "Brazil",          "EWZ",  "SMART",    "USD"),
        ("IND",    "India",           "INDA", "SMART",    "USD"),
        ("KOR",    "South Korea",     "EWY",  "SMART",    "USD"),
        ("HKG",    "Hong Kong",       "EWH",  "SMART",    "USD"),
        ("TWN",    "Taiwan",          "EWT",  "SMART",    "USD"),
        ("CHE",    "Switzerland",     "EWL",  "SMART",    "USD"),
        ("SWE",    "Sweden",          "EWD",  "SMART",    "USD"),
        ("NLD",    "Netherlands",     "EWN",  "SMART",    "USD"),
        ("ITA",    "Italy",           "EWI",  "SMART",    "USD"),
        ("ESP",    "Spain",           "EWP",  "SMART",    "USD"),
        ("SGP",    "Singapore",       "EWS",  "SMART",    "USD"),
        ("MEX",    "Mexico",          "EWW",  "SMART",    "USD"),
        ("ZAF",    "South Africa",    "EZA",  "SMART",    "USD"),
    ]

    _country_perf_cache: Optional[list] = None
    _country_perf_ts: float = 0.0
    _country_perf_fetching: bool = False

    def get_country_performance(self) -> Optional[list]:
        """Non-blocking. Returns list of dicts for world-map choropleth.

        Each dict: {country, code, etf, change_pct, close, prev_close, color}
        Returns cached data if fresh (< 4 hours), else triggers background
        fetch and returns cached (or None on first call).
        """
        now = time.time()
        if self._country_perf_cache is not None and now - self._country_perf_ts < 4 * 3600:
            return self._country_perf_cache
        if not self._connected or self._loop is None:
            return self._country_perf_cache
        if not self._country_perf_fetching:
            self._country_perf_fetching = True
            threading.Thread(
                target=self._fetch_country_perf_thread, daemon=True, name="ibkr-country"
            ).start()
        return self._country_perf_cache

    def _fetch_country_perf_thread(self) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._build_country_perf(), self._loop
            )
            result = future.result(timeout=120)
            if result:
                with self._lock:
                    self._country_perf_cache = result
                    self._country_perf_ts = time.time()
                logger.info("Country performance cached: %d countries", len(result))
        except Exception as exc:
            logger.error("Country performance fetch failed: %s: %s",
                         type(exc).__name__, exc)
        finally:
            self._country_perf_fetching = False

    async def _build_country_perf(self) -> Optional[list]:
        """Fetch 5D daily closes for each country ETF, compute daily change %."""
        import datetime as _dt
        results = []
        for code, name, symbol, exchange, currency in self._COUNTRY_ETFS:
            try:
                contract = Contract(symbol=symbol, secType="STK",
                                    exchange=exchange, currency=currency)
                bars = await self._req_historical(
                    contract, endDateTime="",
                    durationStr="5 D", barSizeSetting="1 day",
                    whatToShow="TRADES", useRTH=True)
                if len(bars) < 2:
                    await asyncio.sleep(0.15)
                    continue
                prev_close = bars[-2].close
                close = bars[-1].close
                if prev_close <= 0:
                    await asyncio.sleep(0.15)
                    continue
                change_pct = (close / prev_close - 1.0) * 100
                # Clamp extreme values for color mapping
                change_pct = max(-10, min(10, change_pct))
                results.append({
                    "code": code,
                    "country": name,
                    "etf": symbol,
                    "close": round(close, 2),
                    "prev_close": round(prev_close, 2),
                    "change_pct": round(change_pct, 2),
                })
                await asyncio.sleep(0.15)
            except Exception as exc:
                logger.warning("Country ETF data unavailable for %s (%s): %s", symbol, name, exc)
                await asyncio.sleep(0.15)
        return results if results else None

    # ------------------------------------------------------------------
    # Security search & detail (symbol lookup via IBKR)
    # ------------------------------------------------------------------

    def lookup_security(self, symbol: str) -> Optional[dict]:
        """Non-blocking. Returns cached or fresh basic info for a symbol.

        Returns dict with keys: symbol, name, exchange, currency, secType,
        sector, category, conId — or None if not found / still loading.
        """
        symbol = symbol.upper().strip()
        if not symbol:
            return None
        # Return fresh cache
        if (symbol in self._sec_cache
                and time.time() - self._sec_cache_ts.get(symbol, 0) < 300):
            return self._sec_cache[symbol]
        # Kick off background fetch if not already running
        if (self._connected and self._loop is not None
                and not self._sec_fetching.get(f"q:{symbol}", False)):
            self._sec_fetching[f"q:{symbol}"] = True
            threading.Thread(
                target=self._fetch_lookup_thread, args=(symbol,),
                daemon=True, name=f"ibkr-lookup-{symbol}"
            ).start()
        return self._sec_cache.get(symbol)

    def _fetch_lookup_thread(self, symbol: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._lookup_async(symbol), self._loop
            )
            result = future.result(timeout=30)
            if result:
                with self._lock:
                    self._sec_cache[symbol] = result
                    self._sec_cache_ts[symbol] = time.time()
        except Exception as exc:
            logger.error("Security lookup failed for %s: %s", symbol, exc)
        finally:
            self._sec_fetching[f"q:{symbol}"] = False

    async def _lookup_async(self, symbol: str) -> Optional[dict]:
        """Worker thread: look up a contract and return basic details."""
        contract = Contract(symbol=symbol, secType="STK", exchange="SMART")
        try:
            details = await self._ib.reqContractDetailsAsync(contract)
            if not details:
                # Try listing on specific exchanges
                for exch in ("NASDAQ", "NYSE", "ARCA", "AMEX"):
                    contract2 = Contract(symbol=symbol, secType="STK",
                                        exchange=exch)
                    details = await self._ib.reqContractDetailsAsync(contract2)
                    if details:
                        break
                    await asyncio.sleep(0.15)
            if not details:
                return None
            d = details[0]
            return {
                "symbol": d.contract.symbol,
                "name": d.longName or d.contract.symbol,
                "exchange": d.contract.primaryExchange or d.exchange,
                "currency": d.contract.currency or "USD",
                "secType": d.contract.secType,
                "sector": getattr(d, "industry", "") or "",
                "category": getattr(d, "category", "") or "",
                "conId": d.contract.conId,
            }
        except Exception as exc:
            logger.warning("Lookup error for %s: %s", symbol, exc)
            return None

    def get_security_detail(self, symbol: str) -> Optional[dict]:
        """Non-blocking. Returns full security detail including price data.

        Returns dict with keys: info, price, chart_dates, chart_closes,
        chart_volumes, fundamentals — or None if still loading.
        """
        symbol = symbol.upper().strip()
        if not symbol:
            return None
        if (symbol in self._sec_detail_cache
                and time.time() - self._sec_detail_ts.get(symbol, 0) < 120):
            return self._sec_detail_cache[symbol]
        if (self._connected and self._loop is not None
                and not self._sec_fetching.get(f"d:{symbol}", False)):
            self._sec_fetching[f"d:{symbol}"] = True
            threading.Thread(
                target=self._fetch_detail_thread, args=(symbol,),
                daemon=True, name=f"ibkr-detail-{symbol}"
            ).start()
        return self._sec_detail_cache.get(symbol)

    def _fetch_detail_thread(self, symbol: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._detail_async(symbol), self._loop
            )
            result = future.result(timeout=60)
            if result:
                with self._lock:
                    self._sec_detail_cache[symbol] = result
                    self._sec_detail_ts[symbol] = time.time()
        except Exception as exc:
            logger.error("Security detail failed for %s: %s: %s",
                         symbol, type(exc).__name__, exc)
        finally:
            self._sec_fetching[f"d:{symbol}"] = False

    async def _detail_async(self, symbol: str) -> Optional[dict]:
        """Worker thread: fetch full detail for a security."""
        import datetime as _dt
        import math

        # 1. Contract details
        info = await self._lookup_async(symbol)
        if not info:
            return None
        con_id = info["conId"]
        currency = info["currency"]
        contract = Contract(conId=con_id, secType="STK",
                            exchange="SMART", currency=currency)

        result = {"info": info}

        def _safe(val, default=0.0):
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return default
            return val

        def _parse_bars(bars):
            rows = []
            for bar in bars:
                d = bar.date
                if hasattr(d, "date"):
                    d = d.date()
                elif isinstance(d, str):
                    d = _dt.date.fromisoformat(d[:10])
                rows.append({
                    "date": str(d),
                    "open": _safe(bar.open),
                    "high": _safe(bar.high),
                    "low": _safe(bar.low),
                    "close": _safe(bar.close),
                    "volume": _safe(bar.volume),
                    "wap": _safe(getattr(bar, "average", None) or getattr(bar, "wap", None)),
                    "barCount": int(_safe(getattr(bar, "barCount", None))),
                })
            return rows

        # 2. Multiple timeframe bars (all free, no subscription)
        timeframe_specs = [
            ("daily_5y",  "5 Y",  "1 day"),
            ("daily_1y",  "1 Y",  "1 day"),
            ("daily_3m",  "3 M",  "1 day"),
            ("hourly_5d", "5 D",  "1 hour"),
            ("min5_1d",   "1 D",  "5 mins"),
        ]
        for key, duration, bar_size in timeframe_specs:
            try:
                bars = await self._req_historical(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow="TRADES",
                    useRTH=True,
                )
                result[key] = _parse_bars(bars)
                await asyncio.sleep(0.15)
            except Exception as exc:
                logger.warning("Historical %s failed for %s: %s", key, symbol, exc)
                result[key] = []

        # Derive price data from latest daily bar
        daily = result.get("daily_1y", [])
        if daily:
            last = daily[-1]
            prev = daily[-2] if len(daily) > 1 else last
            closes = [b["close"] for b in daily]
            highs = [b["high"] for b in daily]
            lows = [b["low"] for b in daily]
            result["price"] = {
                "last": last["close"],
                "open": last["open"],
                "high": last["high"],
                "low": last["low"],
                "close": last["close"],
                "volume": last["volume"],
                "change": last["close"] - prev["close"],
                "changePct": ((last["close"] - prev["close"]) / prev["close"] * 100
                              if prev["close"] != 0 else 0),
                "yearHigh": max(highs) if highs else 0,
                "yearLow": min(l for l in lows if l > 0) if lows else 0,
                "wap": last["wap"],
                "barCount": last["barCount"],
            }
        else:
            result["price"] = {}

        # Fundamentals not available without subscription
        result["fundamentals"] = {}

        # 5. Position data (if held)
        try:
            portfolio = self._ib.portfolio()
            for item in portfolio:
                if item.contract.symbol == symbol:
                    result["position"] = {
                        "shares": item.position,
                        "avgCost": item.averageCost,
                        "marketValue": item.marketValue,
                        "unrealisedPnl": item.unrealizedPNL or 0.0,
                        "realisedPnl": item.realizedPNL or 0.0,
                    }
                    break
            else:
                result["position"] = None
        except Exception:
            result["position"] = None

        return result

    @staticmethod
    def _parse_fundamental_xml(xml_str: str) -> dict:
        """Parse IBKR fundamental XML into a flat dict of key ratios."""
        import xml.etree.ElementTree as ET
        ratios = {}
        try:
            root = ET.fromstring(xml_str)
            # Try common tag patterns
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                text = (elem.text or "").strip()
                if text and tag and len(tag) < 40:
                    try:
                        ratios[tag] = float(text)
                    except ValueError:
                        ratios[tag] = text
        except Exception:
            pass
        # Map common IBKR fields to friendly names
        friendly = {}
        key_map = {
            "peRatio": "P/E", "priceToBook": "P/B",
            "marketCap": "Market Cap", "dividendYield": "Dividend Yield",
            "earningsPerShare": "EPS", "bookValuePerShare": "Book Value/Share",
            "priceToSales": "P/S", "debtToEquity": "D/E",
            "returnOnEquity": "ROE", "returnOnAssets": "ROA",
            "fiftyTwoWeekHigh": "52W High", "fiftyTwoWeekLow": "52W Low",
            "beta": "Beta", "avgVolume": "Avg Volume",
            "sharesOutstanding": "Shares Out",
        }
        for raw, friendly_name in key_map.items():
            if raw in ratios:
                friendly[friendly_name] = ratios[raw]
        # If no mapped fields found, return raw (first 15 keys)
        return friendly if friendly else dict(list(ratios.items())[:15])

    # ------------------------------------------------------------------
    # Thread-safe accessors (called from Dash callbacks / any thread)
    # ------------------------------------------------------------------

    def refresh(self) -> AccountSnapshot:
        """Ensure the worker is running and return the latest cached snapshot.

        This is non-blocking: the actual IB I/O is performed continuously on
        the worker thread. Callers get whatever was most recently cached.
        """
        self.start()
        return self.cached_snapshot

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    @property
    def cached_snapshot(self) -> AccountSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def last_update(self) -> Optional[float]:
        with self._lock:
            return self._last_update


# Singleton client used by the Dash app
_client: Optional[IBKRClient] = None
_client_lock = threading.Lock()


def get_client(config: Optional[ConnectionConfig] = None) -> IBKRClient:
    """Return the module-level singleton IBKRClient (and start its worker)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = IBKRClient(config)
        _client.start()
    return _client
