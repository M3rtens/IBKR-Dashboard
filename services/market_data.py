"""yfinance-backed market-data provider.

Active when ``MARKET_DATA_SOURCE=yfinance`` (the default). Supplies the
market-data layer — per-holding 30-day sparklines and daily % change, benchmark
price history, world-map country performance and security detail — without
needing an IBKR market-data subscription. Set ``MARKET_DATA_SOURCE=ibkr`` to use
the IBKR API paths instead.

All network calls are cached and safe to call from Dash worker threads. This
module has no dashboard dependencies, so it can be imported from any layer.
"""

import logging
import math
import os
import threading
import time

import pandas as pd

logger = logging.getLogger(__name__)

# yfinance logs an ERROR per delisted/renamed symbol; we handle missing data
# ourselves, so keep it quiet.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Lightweight debug tracing for the yfinance fetch paths. On by default so
# fetch failures (otherwise swallowed) are visible in the console; set
# MD_DEBUG=0 to silence.
_DEBUG = os.environ.get("MD_DEBUG", "1") == "1"


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[MD] {msg}", flush=True)


# --- IBKR -> yfinance ticker mapping ---------------------------------

def yf_ticker(symbol: str, currency: str = "USD", exchange: str = "") -> str:
    """Best-effort map an IBKR (symbol, currency, exchange) to a yfinance ticker.

    US listings keep their bare symbol; other venues take yfinance's exchange
    suffix (``.AX`` for ASX, ``.HK`` for Hong Kong, etc.). Symbols that already
    carry a suffix are left untouched.
    """
    s = (symbol or "").upper().strip().replace(" ", "-")
    if not s or "." in s:
        return s
    cur = (currency or "").upper()
    exch = (exchange or "").upper()
    if cur == "AUD" or exch in ("ASX", "SNFE"):
        return f"{s}.AX"
    if cur == "HKD" or exch in ("SEHK", "HKFE"):
        digits = "".join(ch for ch in s if ch.isdigit())
        return f"{int(digits):04d}.HK" if digits else f"{s}.HK"
    if cur == "JPY" or exch in ("TSEJ", "TSE", "JPX"):
        return f"{s}.T"
    if cur in ("GBP", "GBX") or exch in ("LSE", "LSEETF"):
        return f"{s}.L"
    if cur == "CAD" or exch in ("TSX", "VENTURE"):
        return f"{s}.TO"
    if cur == "EUR" and exch in ("IBIS", "IBIS2", "XETRA", "FWB"):
        return f"{s}.DE"
    if cur == "EUR" and exch in ("SBF", "EURONEXT", "ENEXT", "Euronext"):
        return f"{s}.PA"
    if cur == "EUR" and exch in ("AEB", "AMS"):
        return f"{s}.AS"
    if cur == "EUR" and exch in ("BVME", "MIL"):
        return f"{s}.MI"
    if cur == "EUR" and exch in ("EBR", "BRU"):
        return f"{s}.BR"
    return s  # USD / US default


# --- Caches ----------------------------------------------------------

_lock = threading.Lock()
# Per-(ticker, period, interval) OHLCV DataFrame cache.
_hist_cache: dict = {}
# Per-(ticker, period, interval) list-of-closes cache (holdings/country).
_closes_cache: dict = {}
# Keys another thread is currently downloading. Waiters block on the Event
# instead of firing a duplicate download — at app startup several callbacks
# request overlapping ticker sets at once, and duplicate concurrent batch
# downloads trip Yahoo's rate limiting.
_closes_inflight: dict = {}
_TTL = 30 * 60  # seconds
# Short negative-cache TTL for tickers that came back empty (yfinance drops some
# symbols intermittently, and a few never resolve). Without this they would be
# re-downloaded on every poll; a brief hold lets the board settle and relax its
# refresh cadence, while still recovering quickly once the symbol returns.
_EMPTY_TTL = 120  # seconds


def _history(ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Cached single-ticker OHLCV DataFrame via yfinance (empty on failure)."""
    key = (ticker, period, interval)
    now = time.time()
    with _lock:
        c = _hist_cache.get(key)
        if c and now - c["ts"] < (_TTL if not c["data"].empty else _EMPTY_TTL):
            _dbg(f"_history {ticker} {period}/{interval}: cache hit "
                 f"({len(c['data'])} rows)")
            return c["data"]
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval=interval,
                                       auto_adjust=True)
        _dbg(f"_history {ticker} {period}/{interval}: fetched {len(df)} rows"
             + ("" if df is not None and not df.empty else "  <-- EMPTY"))
    except Exception as exc:
        logger.debug("yfinance history failed for %s: %s", ticker, exc)
        _dbg(f"_history {ticker} {period}/{interval}: EXCEPTION "
             f"{type(exc).__name__}: {exc}")
        df = pd.DataFrame()
    with _lock:
        _hist_cache[key] = {"data": df, "ts": now}
    return df


def _extract_closes(data, ticker: str, n_tickers: int,
                    include_prior_year_close: bool = False) -> list:
    """Pull a clean list of closes for ``ticker`` from a yf.download result."""
    try:
        if data is None or len(data) == 0:
            return []
        # group_by="ticker" returns ticker-first MultiIndex columns even when
        # only one symbol is requested.
        closes = data[ticker]["Close"].dropna()
        if include_prior_year_close and not closes.empty:
            idx = pd.DatetimeIndex(closes.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            cutoff = pd.Timestamp(year=pd.Timestamp.today().year, month=1, day=1)
            prior = closes[idx < cutoff].tail(1)
            current = closes[idx >= cutoff]
            closes = pd.concat([prior, current])
        return [float(v) for v in closes.values]
    except Exception:
        return []


def get_closes(tickers: list, period: str = "3mo",
               interval: str = "1d") -> dict:
    """Return ``{ticker: [close, ...]}`` for tickers (cached ~30 min).

    Uncached tickers are fetched together in a single batched ``yf.download``.
    Tickers another thread is already downloading are waited on rather than
    re-fetched, so concurrent callers never issue duplicate batch downloads.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]  # dedupe, keep order
    now = time.time()
    result: dict = {}
    missing: list = []
    waiting: list = []
    with _lock:
        for t in tickers:
            key = (t, period, interval)
            c = _closes_cache.get(key)
            # Successful fetches live for _TTL; known-empty results are held only
            # briefly (_EMPTY_TTL) so a transiently-dropped ticker recovers soon.
            if c and now - c["ts"] < (_TTL if c["data"] else _EMPTY_TTL):
                result[t] = c["data"]
            elif key in _closes_inflight:
                waiting.append((t, _closes_inflight[key]))
            else:
                _closes_inflight[key] = threading.Event()
                missing.append(t)

    if result:
        _dbg(f"get_closes {period}/{interval}: {len(tickers)} requested, "
             f"{len(result)} cache hits, {len(missing)} to fetch, "
             f"{len(waiting)} awaiting other thread")
    if missing:
        _dbg(f"get_closes: batch download {len(missing)} tickers "
             f"{period}/{interval}: {missing}")
        try:
            try:
                import yfinance as yf
                download_period = "1y" if period == "ytd" else period
                data = yf.download(" ".join(missing), period=download_period,
                                   interval=interval, progress=False,
                                   group_by="ticker", threads=True,
                                   auto_adjust=True, timeout=20)
            except Exception as exc:
                logger.debug("yfinance batch download failed: %s", exc)
                _dbg(f"get_closes: batch download EXCEPTION "
                     f"{type(exc).__name__}: {exc}")
                data = None
            got, empty = [], []
            for t in missing:
                closes = _extract_closes(
                    data, t, len(missing), include_prior_year_close=(period == "ytd"))
                with _lock:
                    if closes:
                        # Only cache a successful fetch. yfinance's batch download
                        # intermittently drops tickers; caching the empty result
                        # would freeze that ticker out for the whole TTL.
                        _closes_cache[(t, period, interval)] = {"data": closes, "ts": now}
                        result[t] = closes
                        got.append(t)
                    else:
                        prev = _closes_cache.get((t, period, interval))
                        if prev and prev["data"]:
                            # Keep the last good series (and its TTL) rather than
                            # replacing it with an empty on a transient drop.
                            result[t] = prev["data"]
                        else:
                            # Negative-cache the empty so it isn't re-downloaded
                            # on every poll for the next _EMPTY_TTL seconds.
                            _closes_cache[(t, period, interval)] = {"data": [], "ts": now}
                            result[t] = []
                        empty.append(t)
            _dbg(f"get_closes: batch got {len(got)}/{len(missing)}"
                 + (f", EMPTY: {empty}" if empty else ""))
        finally:
            with _lock:
                for t in missing:
                    ev = _closes_inflight.pop((t, period, interval), None)
                    if ev:
                        ev.set()

    for t, ev in waiting:
        ev.wait(timeout=25)
        with _lock:
            c = _closes_cache.get((t, period, interval))
        result[t] = c["data"] if c else []

    return result


# --- Holdings: sparklines + daily % change ---------------------------

_yield_cache: dict = {}  # yf_ticker -> {"data": trailing-12m yield %, "ts": epoch}


def _dividend_yields(tickers: list) -> dict:
    """Return ``{yf_ticker: trailing-12m dividend yield in %}`` (cached ~30 min).

    One batched ``yf.download`` with ``actions=True``; the per-ticker Dividends
    column is summed over the last year and divided by the latest close. Used as
    a fallback yield for positions that have not paid a dividend yet.
    """
    now = time.time()
    out: dict = {}
    missing: list = []
    with _lock:
        for t in tickers:
            c = _yield_cache.get(t)
            if c and now - c["ts"] < _TTL:
                out[t] = c["data"]
            else:
                missing.append(t)
    if not missing:
        return out
    _dbg(f"_dividend_yields: batch download {len(missing)} tickers: {missing}")
    try:
        import yfinance as yf
        data = yf.download(" ".join(missing), period="1y", interval="1d",
                           progress=False, group_by="ticker", threads=True,
                           auto_adjust=False, actions=True, timeout=20)
    except Exception as exc:
        logger.debug("yfinance dividend download failed: %s", exc)
        _dbg(f"_dividend_yields: batch EXCEPTION {type(exc).__name__}: {exc}")
        data = None
    for t in missing:
        y = 0.0
        try:
            sub = data[t]
            closes = sub["Close"].dropna()
            divs = sub["Dividends"].dropna() if "Dividends" in sub else None
            if not closes.empty and closes.iloc[-1]:
                y = (float(divs.sum()) if divs is not None else 0.0) \
                    / float(closes.iloc[-1]) * 100
        except Exception:
            y = 0.0
        with _lock:
            _yield_cache[t] = {"data": y, "ts": now}
        out[t] = y
    return out


def get_holdings_market_data(holdings: list) -> dict:
    """Return ``{ibkr_symbol: {"closes": [...], "day_pct": float, "div_yield": float}}``.

    ``holdings`` is an iterable of ``(symbol, currency, exchange)`` tuples. The
    30-day close series feeds the sparklines; the last two closes give the daily
    % move (which also drives the "today's movers" panel). ``div_yield`` is the
    trailing-12-month dividend yield in %, used as a fallback for positions that
    have not paid a dividend yet.
    """
    mapping: dict = {}  # yf_ticker -> [ibkr symbols]
    for sym, cur, exch in holdings:
        t = yf_ticker(sym, cur, exch)
        if t:
            mapping.setdefault(t, []).append(sym)
    closes_by_t = get_closes(list(mapping), period="3mo")
    yields = _dividend_yields(list(mapping))
    out: dict = {}
    for t, symbols in mapping.items():
        closes = closes_by_t.get(t) or []
        day_pct = 0.0
        if len(closes) >= 2 and closes[-2]:
            day_pct = (closes[-1] / closes[-2] - 1) * 100
        div_yield = round(yields.get(t, 0.0), 2)
        for sym in symbols:
            out[sym] = {"closes": closes[-30:], "day_pct": round(day_pct, 2),
                        "div_yield": div_yield}
    return out


# --- Benchmarks ------------------------------------------------------

_BENCH_YF = {"sp500": "SPY", "ndx": "QQQ", "asx": "STW.AX"}


def get_benchmark_history():
    """Return ``{key: {"dates": [...], "values": [...]}}`` for sp500/ndx/asx."""
    out: dict = {}
    for key, ticker in _BENCH_YF.items():
        df = _history(ticker, "3y", "1d")
        if df is None or df.empty or "Close" not in df:
            continue
        closes = df["Close"].dropna()
        if closes.empty:
            continue
        dates = [d.date() if hasattr(d, "date") else d for d in closes.index]
        out[key] = {"dates": dates, "values": [float(v) for v in closes.values]}
    return out or None


# --- World-map country performance -----------------------------------

# (country_code, country_name, etf_symbol) — full developed + emerging set.
_COUNTRY_ETFS = [
    ("USA", "United States", "SPY"),  ("AUS", "Australia", "EWA"),
    ("JPN", "Japan", "EWJ"),          ("GBR", "United Kingdom", "EWU"),
    ("DEU", "Germany", "EWG"),        ("FRA", "France", "EWQ"),
    ("CAN", "Canada", "EWC"),         ("CHN", "China", "FXI"),
    ("BRA", "Brazil", "EWZ"),         ("IND", "India", "INDA"),
    ("KOR", "South Korea", "EWY"),    ("HKG", "Hong Kong", "EWH"),
    ("TWN", "Taiwan", "EWT"),         ("CHE", "Switzerland", "EWL"),
    ("SWE", "Sweden", "EWD"),         ("NLD", "Netherlands", "EWN"),
    ("ITA", "Italy", "EWI"),          ("ESP", "Spain", "EWP"),
    ("SGP", "Singapore", "EWS"),      ("MEX", "Mexico", "EWW"),
    ("ZAF", "South Africa", "EZA"),   ("THA", "Thailand", "THD"),
    ("IDN", "Indonesia", "EIDO"),     ("PHL", "Philippines", "EPHE"),
    ("VNM", "Vietnam", "VNM"),        ("MYS", "Malaysia", "EWM"),
    ("POL", "Poland", "EPOL"),        ("TUR", "Turkey", "TUR"),
    ("NOR", "Norway", "NORW"),        ("DNK", "Denmark", "EDEN"),
    ("FIN", "Finland", "EFNL"),       ("AUT", "Austria", "EWO"),
    ("SAU", "Saudi Arabia", "KSA"),   ("ARE", "UAE", "UAE"),
    ("CHL", "Chile", "ECH"),          ("COL", "Colombia", "GXG"),
    ("PER", "Peru", "EPU"),           ("ARG", "Argentina", "ARGT"),
    ("NZL", "New Zealand", "ENZL"),
]


# Latest good row per country ETF, so a transient fetch miss doesn't make a
# country blink in and out of the map between refreshes.
_country_last: dict = {}


def get_country_performance() -> list:
    """Return world-map rows for every covered country via yfinance.

    Uses a 1-month window (always yields >= 2 daily closes, unlike a 5-day one
    across holidays) and falls back to a per-ticker fetch for any ETF the batch
    download dropped — yfinance's batch endpoint intermittently omits tickers.
    Countries seen at least once persist with their latest good value.
    """
    tickers = [etf for _, _, etf in _COUNTRY_ETFS]
    closes_by_t = get_closes(tickers, period="1mo")
    for code, name, etf in _COUNTRY_ETFS:
        closes = closes_by_t.get(etf) or []
        if len(closes) < 2:
            # Per-ticker fallback for a ticker the batch dropped.
            df = _history(etf, "1mo", "1d")
            if df is not None and not df.empty and "Close" in df:
                closes = [float(v) for v in df["Close"].dropna().values]
        if len(closes) < 2 or not closes[-2]:
            continue
        change_pct = max(-10, min(10, (closes[-1] / closes[-2] - 1.0) * 100))
        _country_last[etf] = {
            "code": code, "country": name, "etf": etf,
            "close": round(closes[-1], 2),
            "prev_close": round(closes[-2], 2),
            "change_pct": round(change_pct, 2),
        }
    return [_country_last[etf] for _, _, etf in _COUNTRY_ETFS if etf in _country_last]


# --- Security detail -------------------------------------------------

def _bars_from_history(df: pd.DataFrame, daily: bool) -> list:
    """Convert a yfinance OHLCV DataFrame to the bar-dict list the UI expects."""
    if df is None or df.empty:
        return []
    rows = []
    for idx, r in df.iterrows():
        try:
            close = float(r["Close"])
        except (TypeError, ValueError, KeyError):
            continue
        if close != close:  # NaN
            continue
        date_str = str(idx.date()) if (daily and hasattr(idx, "date")) else str(idx)
        def _finite(value, fallback):
            try:
                value = float(value)
                return value if math.isfinite(value) else fallback
            except (TypeError, ValueError):
                return fallback

        rows.append({
            "date": date_str,
            "open": _finite(r.get("Open"), close),
            "high": _finite(r.get("High"), close),
            "low": _finite(r.get("Low"), close),
            "close": close,
            "volume": _finite(r.get("Volume"), 0.0),
            "wap": close,
            "barCount": 0,
        })
    return rows


def get_security_detail(symbol: str, position: dict = None) -> dict:
    """yfinance-backed equivalent of IBKRClient.get_security_detail.

    Returns a dict with keys: info, price, daily_1y, daily_3m, hourly_5d,
    min5_1d, fundamentals, position — matching what the security page consumes.
    Returns None if the symbol resolves to no data at all.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return None

    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}

    result = {"info": {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "exchange": info.get("exchange", "") or "",
        "currency": info.get("currency", "USD") or "USD",
        "sector": info.get("sector", "") or "",
    }}

    specs = {
        "daily_5y":  ("5y",  "1d", True),
        "daily_1y":  ("1y",  "1d", True),
        "daily_3m":  ("3mo", "1d", True),
        "hourly_5d": ("5d",  "1h", False),
        "min5_1d":   ("1d",  "5m", False),
    }
    for key, (period, interval, daily) in specs.items():
        result[key] = _bars_from_history(_history(symbol, period, interval), daily)

    daily_bars = result.get("daily_1y", [])
    if daily_bars:
        last = daily_bars[-1]
        prev = daily_bars[-2] if len(daily_bars) > 1 else last
        highs = [b["high"] for b in daily_bars]
        lows = [b["low"] for b in daily_bars if b["low"] > 0]
        prev_close = prev["close"] or last["close"]
        result["price"] = {
            "last": last["close"],
            "open": last["open"],
            "high": last["high"],
            "low": last["low"],
            "close": last["close"],
            "volume": last["volume"],
            "change": last["close"] - prev["close"],
            "changePct": ((last["close"] - prev["close"]) / prev_close * 100
                          if prev_close else 0),
            "yearHigh": max(highs) if highs else 0,
            "yearLow": min(lows) if lows else 0,
            "wap": last["close"],
            "barCount": 0,
        }
    else:
        result["price"] = {}

    # If nothing came back at all, signal "no data" so callers can fall back.
    if not result["price"] and not any(result[k] for k in specs):
        return None

    result["fundamentals"] = {}
    result["position"] = position
    return result
