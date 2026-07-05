"""The data layer: demo + live IBKR snapshots, news, country performance,
portfolio/benchmark history (fetched on background threads) and income.

Module-level caches and background threads live here and are shared across
Dash worker threads.
"""

import os
import time
import threading
import contextlib

import pandas as pd

from dashboard.theme import DEMO_MODE, HOLD_CLR, T2, _DEMO_RAW, MARKET_DATA_SOURCE
from services.ibkr_client import get_client
from services import market_data


# --- Perf instrumentation --------------------------------------------
# Set DASH_PERF=0 to silence. Prints wall-clock timings for the blocking data
# paths (IBKR refresh, yfinance batches, per-ticker .info fetches) so it's clear
# which call dominates the load. All timings are wall-clock ms.
_PERF = os.environ.get("DASH_PERF", "1") != "0"

@contextlib.contextmanager
def _timed(label: str):
    if not _PERF:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        print(f"[PERF] {label}: {(time.perf_counter() - t0) * 1000:.0f}ms", flush=True)


# --- RNG / series ----------------------------------------------------

def _rng(seed):
    s = [seed & 0xFFFFFFFF]
    def f():
        s[0] = (s[0] + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((s[0] ^ (s[0] >> 15)) * (1 | s[0])) & 0xFFFFFFFF
        t = (t ^ (t >> 7)) & 0xFFFFFFFF
        t = ((t ^ t) * (61 | t)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4_294_967_296
    return f

def _port_series(total, N=1300):
    r, ser, pv = _rng(20240611), [], 1.0
    for i in range(N):
        sh = -0.06 if i in (280,760,1040) else 0
        pv *= 1 + 0.00055 + (r()-0.5)*0.014 + sh
        ser.append(pv)
    sc = total / ser[-1]
    return [x*sc for x in ser]

def _bench_series(seed, drift, vol, shock, N=1300):
    r, out, v = _rng(seed), [], 1.0
    for i in range(N):
        sh = shock if i in (280,760,1040) else 0
        v *= 1 + drift + (r()-0.5)*vol + sh
        out.append(v)
    return out

def _hold_spark(ticker, ret_pct, n=24):
    seed = sum(ord(c) for c in ticker) + 7
    r = _rng(seed)
    arr = []
    # --- regime-based realistic price action ---
    total_drift = ret_pct / 100  # e.g. -5 -> -0.05
    regime_len = max(3, n // 4)  # each regime lasts ~25% of chart
    regimes = []
    remaining = n
    while remaining > 0:
        seg = min(regime_len + int(r() * 3 - 1), remaining)
        regimes.append(seg)
        remaining -= seg
    # distribute total return across regimes with noise
    drift_per = total_drift / max(len(regimes), 1)
    vol_base = 0.012 + abs(total_drift) * 0.15  # more volatile if big move
    v = 100.0
    regime_i = 0
    for seg in regimes:
        # regime drift: mostly follows overall direction but with noise
        seg_drift = drift_per * (0.6 + 0.8 * r()) + (r() - 0.5) * vol_base * 2
        seg_vol = vol_base * (0.5 + r() * 1.0)
        # mean-reversion target drifts toward the overall trend
        target = 100.0 * (1 + total_drift * (regime_i / len(regimes)))
        for j in range(seg):
            mean_rev = (target - v) / (v * 40)
            shock = (r() - 0.5) * seg_vol
            v *= 1 + seg_drift / seg + shock + mean_rev
            v = max(v, 10)  # floor
            arr.append(v)
        regime_i += 1
    # ensure final value roughly matches total return
    if arr:
        actual_end = arr[-1]
        target_end = 100.0 * (1 + total_drift)
        scale = target_end / actual_end if actual_end else 1.0
        # blend: 70% original shape, 30% forced to hit target
        arr = [a * (0.7 + 0.3 * (scale ** ((i + 1) / len(arr)))) for i, a in enumerate(arr)]
    return arr


# --- Data layer ------------------------------------------------------

# Module-level cache for portfolio history (shared across Dash worker threads).
# Populated by a background thread to avoid blocking the 10s refresh callback.
_hist_cache: dict = {"data": None, "ts": 0.0}
_hist_lock = threading.Lock()
_HIST_TTL = 4 * 3600  # seconds

# Module-level cache for benchmark price series (SPY, QQQ, STW).
_bench_cache: dict = {"data": None, "ts": 0.0}
_bench_lock = threading.Lock()

# Module-level cache for dividend data from Flex Query.
_div_cache: dict = {"data": None, "ts": 0.0}

# Module-level cache for the trades DataFrame from Flex Query (buy/sell history).
_trades_cache: dict = {"data": None, "ts": 0.0}

# Module-level cache for latest news headlines.
_news_cache: dict = {"data": [], "ts": 0.0}
_news_sym_cache: dict = {}  # {symbol: {"data": [...], "ts": float}}
_NEWS_TTL = 300  # seconds

# Module-level cache for country performance (world map).
_country_cache: dict = {"data": None, "ts": 0.0}
_yf_country_cache: dict = {"data": None, "ts": 0.0}
_COUNTRY_TTL = 3600  # seconds (matches IBKR client cache)

def _fetch_news():
    """Return up to 10 latest financial news headlines (cached)."""
    import xml.etree.ElementTree as ET
    now = time.time()
    cached = _news_cache.get("data")
    if cached and now - _news_cache.get("ts", 0) < _NEWS_TTL:
        return cached
    try:
        import requests
        with _timed("_fetch_news: yahoo RSS GET [network]"):
            r = requests.get(
                "https://finance.yahoo.com/news/rssindex",
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36"},
            )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "")
            pub = item.findtext("pubDate", "")
            link = item.findtext("link", "")
            if title:
                clean = title.rsplit("  ", 1)[0] if "  " in title else title
                items.append({"title": clean, "date": pub[:16] if pub else "", "link": link})
            if len(items) >= 10:
                break
        _news_cache["data"] = items
        _news_cache["ts"] = now
        return items
    except Exception as e:
        print(f"[NEWS] fetch failed: {e}")
        return cached or []


def _fetch_symbol_news(symbol: str):
    """Fetch news specifically for a given ticker from Yahoo Finance RSS."""
    import xml.etree.ElementTree as ET
    symbol = symbol.upper().strip()
    now = time.time()
    cached = _news_sym_cache.get(symbol)
    if cached and now - cached.get("ts", 0) < _NEWS_TTL:
        return cached.get("data", [])
    try:
        import requests
        r = requests.get(
            f"https://finance.yahoo.com/rss/headline?s={symbol}",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36"},
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "")
            pub = item.findtext("pubDate", "")
            link = item.findtext("link", "")
            if title:
                clean = title.rsplit("  ", 1)[0] if "  " in title else title
                items.append({"title": clean, "date": pub[:16] if pub else "", "link": link})
            if len(items) >= 10:
                break
        _news_sym_cache[symbol] = {"data": items, "ts": now}
        return items
    except Exception:
        return cached.get("data", []) if cached else []


def _merge_country_perf(ibkr_data: list, yf_data: list) -> list:
    """Merge IBKR + yfinance country rows, deduped by country code.

    IBKR is the primary source (live broker data) and wins on any overlap;
    yfinance fills in the frontier/emerging markets IBKR does not cover.
    """
    merged = list(ibkr_data)
    seen = {r["code"] for r in ibkr_data}
    for r in yf_data:
        if r["code"] not in seen:
            merged.append(r)
            seen.add(r["code"])
    return merged


def _get_country_perf() -> list:
    """Get country performance data for world map (cached).

    Merges IBKR data (21 developed/large markets) + yfinance data
    (~20 frontier/emerging markets). A partial result — returned while the
    IBKR background fetch is still warming up — is served but NOT frozen into
    the cache, so the next render picks up the IBKR ETFs once they land instead
    of being stuck on the yfinance-only set for a full TTL.
    """
    now = time.time()
    cached = _country_cache.get("data")
    if cached and now - _country_cache.get("ts", 0) < _COUNTRY_TTL:
        return cached
    if DEMO_MODE:
        return _demo_country_data()

    if MARKET_DATA_SOURCE == "yfinance":
        # yfinance covers the full country set (developed + emerging), so the
        # map is complete without the IBKR-sourced markets.
        data = market_data.get_country_performance()
        if data:
            _country_cache["data"] = data
            _country_cache["ts"]   = now
            return data
        return cached or []

    ibkr_data = []
    yf_data = []
    ibkr_connected = False

    def _fetch_ibkr():
        nonlocal ibkr_data, ibkr_connected
        try:
            client = get_client()
            if client.is_connected:
                ibkr_connected = True
                for _ in range(20):
                    d = client.get_country_performance()
                    if d:
                        ibkr_data = d
                        return
                    time.sleep(0.5)
        except Exception:
            pass

    def _fetch_yf():
        nonlocal yf_data
        try:
            yf_data = _fetch_yf_country_perf()
        except Exception:
            pass

    t1 = threading.Thread(target=_fetch_ibkr, daemon=True)
    t2 = threading.Thread(target=_fetch_yf, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=12)
    t2.join(timeout=12)

    merged = _merge_country_perf(ibkr_data, yf_data)

    # Only lock the result into the cache once the IBKR set is complete — i.e.
    # IBKR reported data, or it isn't connected so nothing more is coming.
    # If IBKR is connected but its background fetch hasn't finished, serve the
    # partial merge without caching (preferring any richer stale cache) so a
    # later render re-attempts and fills in the IBKR markets.
    ibkr_complete = bool(ibkr_data) or not ibkr_connected
    if merged and ibkr_complete:
        _country_cache["data"] = merged
        _country_cache["ts"] = now
        return merged
    return cached or merged or []


def _demo_country_data() -> list:
    """Demo country performance data for world map."""
    import random
    codes = [
        ("USA","United States","SPY"),("AUS","Australia","STW"),
        ("JPN","Japan","EWJ"),("GBR","United Kingdom","EWU"),
        ("DEU","Germany","EWG"),("FRA","France","EWQ"),
        ("CAN","Canada","EWC"),("CHN","China","FXI"),
        ("BRA","Brazil","EWZ"),("IND","India","INDA"),
        ("KOR","South Korea","EWY"),("HKG","Hong Kong","EWH"),
        ("TWN","Taiwan","EWT"),("CHE","Switzerland","EWL"),
        ("SWE","Sweden","EWD"),("NLD","Netherlands","EWN"),
        ("ITA","Italy","EWI"),("ESP","Spain","EWP"),
        ("SGP","Singapore","EWS"),("MEX","Mexico","EWW"),
        ("ZAF","South Africa","EZA"),
        ("THA","Thailand","THD"),("IDN","Indonesia","EIDO"),
        ("PHL","Philippines","EPHE"),("VNM","Vietnam","VNM"),
        ("MYS","Malaysia","EWM"),("POL","Poland","EPOL"),
        ("TUR","Turkey","TUR"),("NOR","Norway","NORW"),
        ("DNK","Denmark","EDEN"),("FIN","Finland","EFNL"),
        ("AUT","Austria","EWO"),("SAU","Saudi Arabia","KSA"),
        ("ARE","UAE","UAE"),("CHL","Chile","ECH"),
        ("COL","Colombia","COLX"),("PER","Peru","EPU"),
        ("ARG","Argentina","ARGT"),("NGA","Nigeria","NGE"),
        ("EGY","Egypt","EGPT"),("NZL","New Zealand","ENZL"),
    ]
    random.seed(int(time.time()) // 300)
    results = []
    for code, name, etf in codes:
        chg = round(random.uniform(-3.0, 3.0), 2)
        results.append({
            "code": code, "country": name, "etf": etf,
            "close": round(random.uniform(20, 500), 2),
            "prev_close": round(random.uniform(20, 500), 2),
            "change_pct": chg,
        })
    return results


# (country_code, country_name, etf_symbol) — fetched via yfinance
_YF_COUNTRY_ETFS = [
    ("THA", "Thailand",      "THD"),
    ("IDN", "Indonesia",     "EIDO"),
    ("PHL", "Philippines",   "EPHE"),
    ("VNM", "Vietnam",       "VNM"),
    ("MYS", "Malaysia",      "EWM"),
    ("POL", "Poland",        "EPOL"),
    ("TUR", "Turkey",        "TUR"),
    ("NOR", "Norway",        "NORW"),
    ("DNK", "Denmark",       "EDEN"),
    ("FIN", "Finland",       "EFNL"),
    ("AUT", "Austria",       "EWO"),
    ("SAU", "Saudi Arabia",  "KSA"),
    ("ARE", "UAE",           "UAE"),
    ("CHL", "Chile",         "ECH"),
    ("COL", "Colombia",      "GXG"),
    ("PER", "Peru",          "EPU"),
    ("ARG", "Argentina",     "ARGT"),
    ("NZL", "New Zealand",   "ENZL"),
]
# Removed: NGE (Global X Nigeria) and EGPT (VanEck Egypt) — both delisted, no
# price data. COLX was never a valid symbol; the Colombia ETF is GXG.


def _fetch_yf_country_perf() -> list:
    """Fetch 5-day performance for additional country ETFs via yfinance."""
    import logging
    import yfinance as yf
    # A delisted/renamed ETF makes yfinance log a noisy ERROR per symbol even
    # though we handle the missing data below. Silence its logger — a failed
    # symbol is simply skipped and the map falls back to the cached set.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    now = time.time()
    cached = _yf_country_cache.get("data")
    if cached and now - _yf_country_cache.get("ts", 0) < _COUNTRY_TTL:
        return cached

    tickers_str = " ".join(etf for _, _, etf in _YF_COUNTRY_ETFS)
    try:
        data = yf.download(tickers_str, period="5d", interval="1d",
                           progress=False, group_by="ticker", threads=True)
    except Exception:
        return cached or []

    results = []
    for code, name, etf in _YF_COUNTRY_ETFS:
        try:
            if len(_YF_COUNTRY_ETFS) == 1:
                closes = data["Close"].dropna().values
            else:
                closes = data[etf]["Close"].dropna().values
            if len(closes) < 2:
                continue
            prev_close = float(closes[-2])
            close = float(closes[-1])
            if prev_close <= 0:
                continue
            change_pct = (close / prev_close - 1.0) * 100
            change_pct = max(-10, min(10, change_pct))
            results.append({
                "code": code,
                "country": name,
                "etf": etf,
                "close": round(close, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
            })
        except Exception:
            continue

    if results:
        _yf_country_cache["data"] = results
        _yf_country_cache["ts"] = now
    return results or cached or []


def _fetch_history_bg() -> None:
    """Background thread: try Flex Query equity summary, then IBKR API reconstruction."""
    import datetime as _dt

    # Optional start-date clip applied to both sources.
    _start_raw = os.environ.get("IBKR_PORTFOLIO_START", "").strip()
    try:
        _min_date = _dt.date.fromisoformat(_start_raw) if _start_raw else None
    except ValueError:
        _min_date = None
        print(f"[WARN] IBKR_PORTFOLIO_START={_start_raw!r} is not a valid YYYY-MM-DD date, ignoring.")

    def _clip(dates, values):
        """Return (dates, values) clipped to _min_date if set."""
        if _min_date is None:
            return dates, values
        pairs = [(d, v) for d, v in zip(dates, values)
                 if (d if isinstance(d, _dt.date) else _dt.date.fromisoformat(str(d)[:10])) >= _min_date]
        if not pairs:
            return dates, values  # don't clip to empty
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def _time_weighted_values(dates, values, cash_flows):
        """Build a flow-adjusted performance series from daily equity values."""
        if len(dates) < 2:
            return list(values)

        # Normalise all dates to datetime.date objects
        norm_dates = []
        for d in dates:
            if isinstance(d, _dt.date):
                norm_dates.append(d)
            else:
                norm_dates.append(_dt.date.fromisoformat(str(d)[:10]))

        flows_by_date = {}
        n_skipped = 0
        if cash_flows is not None and not cash_flows.empty:
            for _, row in cash_flows.iterrows():
                dt = row.get("datetime")
                if pd.isna(dt):
                    n_skipped += 1
                    continue
                flow_date = dt.date() if hasattr(dt, "date") else _dt.date.fromisoformat(str(dt)[:10])
                flows_by_date[flow_date] = flows_by_date.get(flow_date, 0.0) + float(row.get("amount") or 0.0)

        total_rows = len(cash_flows) if cash_flows is not None and not cash_flows.empty else 0
        print(f"[TWR] cash flows: {len(flows_by_date)} unique dates used, "
              f"{n_skipped}/{total_rows} rows skipped (unparseable datetime)")
        for flow_date, flow_amt in sorted(flows_by_date.items()):
            print(f"  {flow_date}  ${flow_amt:+,.2f}")

        # Look-back approach with T+1 look-ahead:
        # For each equity date i, scan all flow dates in (prev_date, next_date]
        # where next_date is the *following equity date* (or a safe default for
        # the last day).  This handles:
        #   - flows on weekends/holidays (applied to first trading day that
        #     includes them in equity)
        #   - flows dated T+1 that affected the prior trading day's equity
        #     (IBKR dating quirk)
        sorted_flow_dates = sorted(flows_by_date.keys())
        consumed = set()

        # values[0] already embeds all opening deposits — pre-consume any flow
        # on dates[0] so the loop does not subtract it from the day-1 return.
        if norm_dates[0] in flows_by_date:
            consumed.add(norm_dates[0])
        # IBKR T+1 quirk: an opening deposit is sometimes dated to the next
        # trading day even though it is already reflected in values[0].
        # Pre-consume flows within 5 calendar days of dates[0] that land on
        # or before dates[1] so they are not double-counted.
        if len(norm_dates) >= 2:
            for fd in flows_by_date:
                if norm_dates[0] < fd <= norm_dates[1] and (fd - norm_dates[0]).days <= 5:
                    consumed.add(fd)

        out = [float(values[0])]

        nfd = len(sorted_flow_dates)
        print(f"[TWR] equity dates: {norm_dates[0]} … {norm_dates[-1]}  ({len(norm_dates)} days)")
        print(f"[TWR] flow dates in range: {sorted_flow_dates[0] if nfd else 'none'} … "
              f"{sorted_flow_dates[-1] if nfd else 'none'}  ({nfd} unique dates)")
        print(f"[TWR] anchor  #{0} {norm_dates[0]}  equity=${values[0]:>10,.2f}  twr=${out[0]:>10,.2f}")
        if consumed:
            print(f"[TWR] pre-consumed inception flows ({len(consumed)} date(s)):")
            for fd in sorted(consumed):
                print(f"  {fd}  ${flows_by_date.get(fd, 0.0):+,.2f}")

        for i in range(1, len(values)):
            prev = float(values[i - 1])
            curr = float(values[i])
            date_key = norm_dates[i]
            prev_date = norm_dates[i - 1]

            # Pass 1: same-period flows (up to and including date_key)
            flow = 0.0
            matched_dates = []
            for fd in sorted_flow_dates:
                if fd in consumed:
                    continue
                if fd > date_key:
                    break
                if fd > prev_date:
                    flow += flows_by_date[fd]
                    consumed.add(fd)
                    matched_dates.append(fd)

            # Pass 2: T+1 look-ahead — IBKR sometimes dates a cash event to
            # the next equity date even though it is already reflected in curr.
            # Only apply if the look-ahead flow is in the same direction as the
            # equity move; that confirms the flow explains the move rather than
            # a market event on the other side.
            if i + 1 < len(norm_dates):
                next_equity = norm_dates[i + 1]
                la_flow, la_dates = 0.0, []
                for fd in sorted_flow_dates:
                    if fd in consumed:
                        continue
                    if fd > next_equity:
                        break
                    if fd > date_key:
                        la_flow += flows_by_date[fd]
                        la_dates.append(fd)
                if la_flow != 0.0:
                    equity_move = (curr - flow) - prev
                    if (la_flow > 0) == (equity_move > 0):
                        flow += la_flow
                        matched_dates.extend(la_dates)
                        for fd in la_dates:
                            consumed.add(fd)

            daily_ret = ((curr - flow) / prev - 1.0) if prev else 0.0
            raw_ret = (curr / prev - 1.0) if prev else 0.0

            sharp = abs(daily_ret) > 0.05
            always_log = (
                i <= 5 or i > len(values) - 5 or
                flow != 0.0 or
                abs(daily_ret) > 0.01 or
                abs(raw_ret) > 0.01
            )
            if always_log or sharp:
                flow_detail = ""
                if matched_dates:
                    flow_detail = f"  flows_matched={' '.join(str(d) for d in matched_dates)}"
                flag = "  *** SHARP DRAWDOWN ***" if daily_ret < -0.05 else ""
                print(f"  [TWR] #{i} {date_key}  prev=${prev:>10,.2f}  curr=${curr:>10,.2f}  "
                      f"flow=${flow:>+9,.2f}  raw={raw_ret:>+7.2%}  adj={daily_ret:>+7.2%}  "
                      f"cumul=${out[-1]:>10,.2f}{flow_detail}{flag}")

            out.append(out[-1] * (1.0 + daily_ret))

        print(f"[TWR] series range: ${out[0]:,.2f} → ${out[-1]:,.2f}  "
              f"(raw: ${values[0]:,.2f} → ${values[-1]:,.2f})")
        return out

    try:
        flex_token  = os.environ.get("IBKR_FLEX_TOKEN", "")
        flex_qid    = os.environ.get("IBKR_FLEX_QUERY_ID", "")
        print(f"[HIST] flex config: token={'set' if flex_token else 'UNSET'}, "
              f"query_id={'set' if flex_qid else 'UNSET'}")

        if flex_token and flex_qid:
            try:
                from services.flex_query import load_flex_data
                fd = load_flex_data(flex_token, flex_qid)
                eq = fd.equity_summary
                print(f"[HIST] flex loaded: equity_summary={len(eq)} rows, "
                      f"dividends={len(fd.dividends)}, trades={len(fd.trades)}, "
                      f"deposits/withdrawals={len(fd.deposits_withdrawals)}")
                if not eq.empty:
                    dates  = list(eq["date"])
                    values = [float(v) for v in eq["value"]]
                    dw = fd.deposits_withdrawals
                    # Cache dividend data for income calculation
                    divs = fd.dividends
                    if not divs.empty:
                        _div_cache["data"] = divs
                        _div_cache["ts"]   = time.time()
                    # Cache trades for per-security buy/sell markers
                    trades = fd.trades
                    if not trades.empty:
                        _trades_cache["data"] = trades
                        _trades_cache["ts"]   = time.time()
                    print(f"[TWR] equity summary: {len(dates)} days  "
                          f"${values[0]:,.2f} → ${values[-1]:,.2f}")
                    print(f"[TWR] deposits_withdrawals DataFrame: {len(dw)} rows, "
                          f"{dw['datetime'].isna().sum() if not dw.empty else 0} with NaT datetime")
                    tw_values = _time_weighted_values(dates, values, dw)
                    dates, values = _clip(dates, values)
                    tw_dates, tw_values = _clip(list(eq["date"]), tw_values)
                    result = {"money": (dates, values), "time": (tw_dates, tw_values),
                              "cash_flows": dw}
                    _hist_cache["data"] = result
                    _hist_cache["ts"]   = time.time()
                    return
                print("[HIST] flex equity_summary is EMPTY - enable 'Equity "
                      "Summary by Report Date' in the Flex Query config. "
                      "Falling back to IBKR API reconstruction.")
            except Exception as e:
                print(f"[WARN] Flex Query failed ({type(e).__name__}: {e}), "
                      f"falling back to IBKR API")

        print("[HIST] using IBKR API portfolio-history reconstruction")
        client = get_client()
        df = client.get_portfolio_history()
        print(f"[HIST] IBKR API history: {len(df)} rows")
        if not df.empty:
            dates  = list(df["date"])
            values = [float(v) for v in df["value"]]
            dates, values = _clip(dates, values)
            result = {"money": (dates, values), "time": (dates, values)}
            _hist_cache["data"] = result
            _hist_cache["ts"]   = time.time()
    finally:
        with _hist_lock:
            _hist_cache["fetching"] = False



def _get_port_history() -> dict:
    """Non-blocking. Returns cached money/time history series.

    First call triggers a background fetch; subsequent calls return the cache.
    Automatically re-fetches after _HIST_TTL seconds.
    """
    now    = time.time()
    cached = _hist_cache.get("data")
    if cached is not None and now - _hist_cache.get("ts", 0) < _HIST_TTL:
        if isinstance(cached, tuple):
            return {"money": cached, "time": cached}
        return cached

    with _hist_lock:
        if not _hist_cache.get("fetching"):
            _hist_cache["fetching"] = True
            threading.Thread(target=_fetch_history_bg, daemon=True, name="hist-bg").start()

    if isinstance(cached, tuple):
        return {"money": cached, "time": cached}
    return cached if cached is not None else {"money": ([], []), "time": ([], [])}


def _get_symbol_trades(symbol: str) -> list:
    """Return the portfolio's BUY/SELL trades for a symbol, from Flex data.

    Each item: {"date": "YYYY-MM-DD", "action": "BUY"|"SELL",
                "quantity": float, "price": float}.
    Kicks off the Flex/history load if it hasn't run yet and returns [] until
    the trade data is available (so the chart simply renders without markers).
    """
    df = _trades_cache.get("data")
    if df is None:
        # Trigger the background Flex load (populates _trades_cache as a side
        # effect); markers appear on a later render once it completes.
        _get_port_history()
        df = _trades_cache.get("data")
    if df is None or df.empty:
        return []

    sym = (symbol or "").upper().strip()
    try:
        sub = df[df["symbol"].astype(str).str.upper() == sym]
    except Exception:
        return []

    out = []
    for _, r in sub.iterrows():
        action = str(r.get("action") or "").upper()
        if action not in ("BUY", "SELL"):
            continue
        d = r.get("date")
        try:
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        except Exception:
            continue
        price = r.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0 or price != price:  # skip non-positive / NaN
            continue
        out.append({
            "date": date_str,
            "action": action,
            "quantity": abs(float(r.get("quantity") or 0)),
            "price": price,
        })
    return out


def _fetch_bench_bg() -> None:
    """Background thread: fetch SPY/QQQ/STW closes and cache them.

    Source is yfinance or IBKR depending on MARKET_DATA_SOURCE.
    """
    try:
        if MARKET_DATA_SOURCE == "yfinance":
            result = market_data.get_benchmark_history()
            if result:
                _bench_cache["data"] = result
                _bench_cache["ts"]   = time.time()
            return
        client = get_client()
        df = client.get_benchmark_history()
        if df is not None and not df.empty:
            result = {}
            for key in ("sp500", "ndx", "asx"):
                if key in df.columns:
                    col = df[["date", key]].dropna()
                    result[key] = {
                        "dates":  list(col["date"]),
                        "values": [float(v) for v in col[key]],
                    }
            if result:
                _bench_cache["data"] = result
                _bench_cache["ts"]   = time.time()
    finally:
        with _bench_lock:
            _bench_cache["fetching"] = False


def _get_bench_history():
    """Non-blocking. Returns {key: {"dates": [...], "values": [...]}} or None."""
    now    = time.time()
    cached = _bench_cache.get("data")
    if cached is not None and now - _bench_cache.get("ts", 0) < _HIST_TTL:
        return cached
    with _bench_lock:
        if not _bench_cache.get("fetching"):
            _bench_cache["fetching"] = True
            threading.Thread(target=_fetch_bench_bg, daemon=True, name="bench-bg").start()
    return cached


def _calc_income():
    """Calculate 12-month trailing income from cached Flex Query dividend data."""
    divs = _div_cache.get("data")
    if divs is None or (hasattr(divs, "empty") and divs.empty):
        return 0.0, 0.0
    import datetime as _dt
    cutoff = _dt.datetime.now() - _dt.timedelta(days=365)
    if "datetime" in divs.columns:
        recent = divs[divs["datetime"] >= cutoff]
    elif "date" in divs.columns:
        recent = divs[pd.to_datetime(divs["date"], errors="coerce") >= cutoff]
    else:
        return 0.0, 0.0
    if recent.empty or "amount" not in recent.columns:
        return 0.0, 0.0
    income = float(recent["amount"].sum())
    return income, 0.0


def _calc_symbol_dividends():
    """Return dict mapping ticker -> 12-month trailing dividend amount."""
    divs = _div_cache.get("data")
    if divs is None or (hasattr(divs, "empty") and divs.empty):
        return {}
    import datetime as _dt
    cutoff = _dt.datetime.now() - _dt.timedelta(days=365)
    if "datetime" in divs.columns:
        recent = divs[divs["datetime"] >= cutoff]
    elif "date" in divs.columns:
        recent = divs[pd.to_datetime(divs["date"], errors="coerce") >= cutoff]
    else:
        return {}
    if recent.empty or "amount" not in recent.columns or "symbol" not in recent.columns:
        return {}
    grouped = recent.groupby("symbol")["amount"].sum()
    return grouped.to_dict()


class DashData:
    total=0.0; day_dollar=0.0; day_pct=0.0
    total_return=0.0; total_return_pct=0.0
    unreal=0.0; unreal_pct=0.0; cost_basis=0.0
    income=0.0; yield_pct=0.0
    account_id=""; connected=False; status_msg=""
    holdings=[]; allocation=[]; port_series=[]; port_dates=[]
    port_series_time=[]; port_dates_time=[]
    bench_data=None  # {key: {"dates": [...], "values": [...]}} from IBKR, or None
    cash_flows=None  # deposits/withdrawals DataFrame from Flex Query


def _demo_data() -> DashData:
    d = DashData()
    holdings, total = [], sum(v for _,_,_,v,_,_ in _DEMO_RAW)
    _DEMO_YIELDS = {"VAS":3.8,"VGS":1.7,"VDHG":2.9,"VAF":3.2,"VAP":3.5,"CBA":4.1,"MQG":3.9}
    for ticker,name,cls,value,cost,day in _DEMO_RAW:
        ret = (value-cost)/cost if cost else 0
        holdings.append({
            "ticker":ticker,"name":name,"cls":cls,"value":value,"cost":cost,
            "day_pct":day,"day_dollar":value*day/100,
            "ret":ret,"ret_dollar":value-cost,"weight":value/total,
            "color":HOLD_CLR.get(cls,T2),"series":_hold_spark(ticker,ret*100),
            "div_yield":_DEMO_YIELDS.get(ticker,0.0),
        })
    d.holdings = sorted(holdings, key=lambda h: h["value"], reverse=True)
    d.total = total
    d.day_dollar = sum(h["day_dollar"] for h in d.holdings)
    d.day_pct = d.day_dollar/(total-d.day_dollar)*100 if total else 0
    nc = [h for h in d.holdings if h["ticker"]!="CASH"]
    cost_nc = sum(h["cost"] for h in nc); val_nc = sum(h["value"] for h in nc)
    d.unreal = val_nc - cost_nc
    d.unreal_pct = d.unreal/cost_nc*100 if cost_nc else 0
    d.cost_basis = cost_nc
    contrib = 300000
    d.total_return = total - contrib
    d.total_return_pct = d.total_return/contrib*100
    d.income = 12840; d.yield_pct = d.income/val_nc*100 if val_nc else 0
    cls_map = {}
    for h in d.holdings:
        cls_map.setdefault(h["cls"],{"name":h["cls"],"value":0})
        cls_map[h["cls"]]["value"] += h["value"]
    order = ["Australian Shares","International Shares","Bonds","Property","Cash"]
    d.allocation = [cls_map[c] for c in order if c in cls_map]
    d.port_series = _port_series(total)
    d.port_series_time = d.port_series
    d.account_id="Demo account"; d.connected=True; d.status_msg="Demo mode"
    return d


def _live_data() -> DashData:
    _t_live = time.perf_counter()
    d = DashData()
    try:
        client = get_client()
        with _timed("_live_data: client.refresh() [IBKR]"):
            snap   = client.refresh()
        d.connected  = client.is_connected
        d.account_id = snap.account_id
        d.status_msg = (f"Connected · {snap.account_id}" if d.connected
                        else (client.error or "Disconnected"))
        d.total       = snap.net_liquidation
        d.day_dollar  = snap.day_pnl
        d.day_pct     = snap.day_pnl/(d.total-snap.day_pnl)*100 if d.total else 0
        d.unreal      = snap.unrealized_pnl
        d.cost_basis  = snap.gross_position_value - snap.unrealized_pnl
        d.total_return = snap.realized_pnl + snap.unrealized_pnl
        d.total_return_pct = d.total_return/d.cost_basis*100 if d.cost_basis else 0
        d.unreal_pct   = d.unreal/d.cost_basis*100 if d.cost_basis else 0
        d.income, d.yield_pct = _calc_income()
        if d.income and d.cost_basis:
            d.yield_pct = d.income / d.cost_basis * 100


        hdf = snap.holdings if isinstance(snap.holdings, pd.DataFrame) else pd.DataFrame()
        holdings, total_mv = [], hdf["Market Value"].abs().sum() if not hdf.empty else 0
        use_yf = MARKET_DATA_SOURCE == "yfinance"
        if use_yf:
            # Source 30-day sparklines + daily % change from yfinance.
            hold_keys = [
                (str(r["Symbol"]), str(r.get("Currency") or "USD"),
                 str(r.get("Exchange") or ""))
                for _, r in (hdf.iterrows() if not hdf.empty else [])
                if str(r.get("Type", "STK")) not in ("CASH", "CMDTY")
            ]
            with _timed(f"_live_data: get_holdings_market_data [yfinance, {len(hold_keys)} syms]"):
                md = market_data.get_holdings_market_data(hold_keys)
            sparklines = {}
        else:
            md = {}
            with _timed("_live_data: client.get_hold_sparklines [IBKR]"):
                sparklines = client.get_hold_sparklines()
        sym_divs = _calc_symbol_dividends()
        for _, row in (hdf.iterrows() if not hdf.empty else []):
            sym  = row["Symbol"]; value = float(row["Market Value"])
            desc = str(row.get("Description") or sym)
            cost = abs(float(row["Avg Cost"])*float(row["Position"]))
            upnl = float(row["Unrealized P&L"]); ret = upnl/cost if cost else 0
            if use_yf:
                info = md.get(sym, {})
                day_pct    = info.get("day_pct", 0.0)
                day_dollar = value * day_pct / 100
                series     = info.get("closes") or _hold_spark(sym, ret*100)
            else:
                day_pct    = float(row.get("Day Pct") or 0.0)
                day_dollar = float(row.get("Day P&L") or 0.0)
                series     = sparklines.get(sym) or _hold_spark(sym, ret*100)
            ann_div = sym_divs.get(sym, 0.0)
            div_yield = (ann_div / abs(value) * 100) if value else 0.0
            holdings.append({
                "ticker":sym,"name":desc,"cls":"Equities","value":value,"cost":cost,
                "day_pct":day_pct,"day_dollar":day_dollar,"ret":ret,"ret_dollar":upnl,
                "weight":abs(value)/total_mv if total_mv else 0,
                "color":T2,"series":series,
                "div_yield":div_yield,
            })
        d.holdings = sorted(holdings, key=lambda h: abs(h["value"]), reverse=True)
        d.allocation = [{"name":"Securities","value":snap.gross_position_value},
                        {"name":"Cash","value":snap.total_cash}]
        hist = _get_port_history()
        dates, values = hist.get("money", ([], []))
        time_dates, time_values = hist.get("time", (dates, values))
        if values:
            # Always pin the last data point to the current live net_liquidation
            # so the chart tip matches the KPI card. The history cache can be up
            # to 4 hours stale, but the live value updates every refresh tick.
            import datetime as _dt
            today = _dt.date.today()
            values = list(values)  # don't mutate the cached list
            dates  = list(dates)
            time_values = list(time_values)
            time_dates  = list(time_dates)


            if dates and d.total:
                last_date = dates[-1]
                # Convert to date object if it's a string or datetime
                if isinstance(last_date, str):
                    last_date = _dt.date.fromisoformat(last_date[:10])
                elif hasattr(last_date, "date"):
                    last_date = last_date.date()
                if last_date == today:
                    values[-1] = d.total
                else:
                    dates.append(today)
                    values.append(d.total)
            if time_dates and time_values and d.total:
                last_time_date = time_dates[-1]
                if isinstance(last_time_date, str):
                    last_time_date = _dt.date.fromisoformat(last_time_date[:10])
                elif hasattr(last_time_date, "date"):
                    last_time_date = last_time_date.date()
                if last_time_date < today:
                    time_dates.append(today)
                    time_values.append(time_values[-1])
            d.port_series = values
            d.port_dates  = dates
            d.port_series_time = time_values
            d.port_dates_time  = time_dates
        d.bench_data = _get_bench_history()
        d.cash_flows = hist.get("cash_flows")
    except Exception as e:
        d.status_msg = f"Error: {e}"
    if _PERF:
        print(f"[PERF] _live_data TOTAL: {(time.perf_counter() - _t_live) * 1000:.0f}ms", flush=True)
    return d


# Short-TTL cache for the assembled snapshot. Several callbacks fire on the same
# ``refresh`` tick (the main dashboard rebuild, the metrics card, the world map),
# and each used to call ``get_data`` independently — meaning 2+ blocking IBKR
# ``client.refresh()`` + yfinance fetches per 10s tick, serialised ahead of the
# navigation callback. A few seconds of caching collapses those into one fetch
# so a tick no longer blocks tab switching. The window is far shorter than the
# refresh interval, so live values stay current.
_data_cache: dict = {"data": None, "ts": 0.0}
_DATA_TTL = 5.0  # seconds

def get_data() -> DashData:
    if DEMO_MODE:
        return _demo_data()
    now = time.time()
    cached = _data_cache.get("data")
    if cached is not None and now - _data_cache.get("ts", 0.0) < _DATA_TTL:
        if _PERF:
            print(f"[PERF] get_data: CACHE HIT (age {now - _data_cache['ts']:.1f}s)", flush=True)
        return cached
    if _PERF:
        print("[PERF] get_data: CACHE MISS -> _live_data()", flush=True)
    d = _live_data()
    _data_cache["data"] = d
    _data_cache["ts"]   = now
    return d


# ── Portfolio fundamentals via yfinance ───────────────────────────────

_yf_info_cache: dict = {}
_YF_INFO_TTL   = 6 * 3600

def _fetch_yf_info(ticker: str) -> dict:
    now    = time.time()
    cached = _yf_info_cache.get(ticker)
    if cached and now - cached["ts"] < _YF_INFO_TTL:
        return cached["data"]
    try:
        import yfinance as yf
        with _timed(f"_fetch_yf_info: {ticker}.info [yfinance]"):
            info = yf.Ticker(ticker).info or {}
        _yf_info_cache[ticker] = {"data": info, "ts": now}
        return info
    except Exception:
        return {}


# (yf_key, display_label, format_type, section)
# format types: ratio → Nx  |  beta → 2dp  |  div_yield → already %  |  pct → decimal×100
_PORTFOLIO_METRIC_DEFS = [
    ("trailingPE",                   "P/E (TTM)",     "ratio",     "valuation"),
    ("forwardPE",                    "Forward P/E",   "ratio",     "valuation"),
    ("priceToBook",                  "P/B",           "ratio",     "valuation"),
    ("priceToSalesTrailing12Months", "P/S",           "ratio",     "valuation"),
    ("enterpriseToEbitda",           "EV/EBITDA",     "ratio",     "valuation"),
    ("grossMargins",                 "Gross Margin",  "pct",       "quality"),
    ("profitMargins",                "Net Margin",    "pct",       "quality"),
    ("returnOnEquity",               "ROE",           "pct",       "quality"),
    ("beta",                         "Beta",          "beta",      "quality"),
    ("dividendYield",                "Div Yield",     "div_yield", "quality"),
]


def get_portfolio_metrics(holdings: list) -> dict:
    """Return market-cap-weighted fundamental metrics for equity holdings.

    Returns {"valuation": [...], "quality": [...]} where each list contains
    (label, formatted_value, coverage_pct) tuples.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    equity = [h for h in holdings if h.get("ticker") and h.get("value", 0) > 0]
    if not equity:
        return {}
    total_value = sum(h["value"] for h in equity)
    if total_value <= 0:
        return {}

    info_map: dict = {}
    with _timed(f"get_portfolio_metrics: yfinance .info x{len(equity)} [pooled]"), \
            ThreadPoolExecutor(max_workers=min(len(equity), 8)) as pool:
        futs = {pool.submit(_fetch_yf_info, h["ticker"]): h["ticker"] for h in equity}
        try:
            for fut in as_completed(futs, timeout=25):
                t = futs[fut]
                try:
                    info_map[t] = fut.result()
                except Exception:
                    info_map[t] = {}
        except Exception:
            for t in futs.values():
                if t not in info_map:
                    info_map[t] = {}

    sections: dict = {}
    for yf_key, label, fmt, section in _PORTFOLIO_METRIC_DEFS:
        w_sum  = 0.0
        w_covd = 0.0
        for h in equity:
            w   = h["value"] / total_value
            val = info_map.get(h["ticker"], {}).get(yf_key)
            if val is None:
                continue
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if f != f or f <= 0:
                continue
            w_sum  += w * f
            w_covd += w

        if w_covd < 0.05:
            entry = (label, "—", 0)
        else:
            raw = w_sum / w_covd
            if fmt == "ratio":
                display = f"{raw:.1f}x"
            elif fmt == "beta":
                display = f"{raw:.2f}"
            elif fmt == "div_yield":
                display = f"{raw:.2f}%"
            elif fmt == "pct":
                display = f"{raw * 100:.1f}%"
            else:
                display = f"{raw:.2f}"
            entry = (label, display, int(round(w_covd * 100)))

        sections.setdefault(section, []).append(entry)

    return sections


def get_portfolio_risk_metrics() -> list:
    """Calculate statistical risk/performance metrics from portfolio return history.

    Uses up to 252 trading days of time-weighted data (falls back to
    money-weighted). Returns list of (label, formatted_value) tuples.
    """
    import math

    RF_ANNUAL = 0.0435  # approx. RBA cash rate
    RF_DAILY  = RF_ANNUAL / 252

    hist = _get_port_history()
    if not hist:
        return []

    series = hist.get("time") or hist.get("money")
    if not series:
        return []
    _, values = series
    if not values or len(values) < 20:
        return []

    vals = [float(v) for v in values if v is not None]
    vals = vals[-253:]
    if len(vals) < 10:
        return []

    rets = [(vals[i] - vals[i - 1]) / vals[i - 1]
            for i in range(1, len(vals)) if vals[i - 1] > 0]
    if len(rets) < 10:
        return []

    n        = len(rets)
    mean_ret = sum(rets) / n
    years    = n / 252
    total_rt = vals[-1] / vals[0] - 1
    ann_ret  = (1 + total_rt) ** (1 / years) - 1 if years > 1e-6 else total_rt

    variance = sum((r - mean_ret) ** 2 for r in rets) / max(n - 1, 1)
    vol_ann  = math.sqrt(variance * 252)

    sharpe = (ann_ret - RF_ANNUAL) / vol_ann if vol_ann > 1e-10 else 0.0

    neg     = [r - RF_DAILY for r in rets if r < RF_DAILY]
    sortino = None
    if neg:
        down_vol = math.sqrt(sum(x ** 2 for x in neg) / n * 252)
        if down_vol > 1e-10:
            sortino = (ann_ret - RF_ANNUAL) / down_vol

    peak   = vals[0]
    max_dd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd

    calmar   = ann_ret / abs(max_dd) if max_dd < -1e-6 else None
    wins     = sum(1 for r in rets if r > 0)
    win_rate = wins / n * 100

    return [
        ("Annualised Return",  f"{ann_ret * 100:+.1f}%"),
        ("Volatility (Ann.)",  f"{vol_ann * 100:.1f}%"),
        ("Sharpe Ratio",       f"{sharpe:.2f}"),
        ("Sortino Ratio",      f"{sortino:.2f}" if sortino is not None else "—"),
        ("Max Drawdown",       f"{max_dd * 100:.1f}%"),
        ("Calmar Ratio",       f"{calmar:.2f}" if calmar is not None else "—"),
        ("Win Rate (Daily)",   f"{win_rate:.1f}%"),
    ]
