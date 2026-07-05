"""Backtest page (BETA): build a portfolio and simulate it over a chosen window.

Interactive backend: the builder (initial capital, backtest period, rebalance
cadence, benchmark selection, holdings + weight sliders, security picker,
presets / load-my-portfolio) drives a monthly rebalancing simulation against
one or more selected benchmarks, and the results panel renders KPIs, an equity
curve, drawdown, annual-return bars and a metrics table.

Monthly returns come from yfinance where available (cached), with a deterministic
modelled fallback so the tool always works. State lives in two dcc.Stores
(``bt-state`` for the portfolio, ``bt-picker`` for the modal).

The builder's two states (chooser / building) and the security-picker modal are
all present in the initial layout with their visibility toggled by callbacks, so
every fixed component id exists up front (no reliance on
``suppress_callback_exceptions``). Per-holding rows are pattern-matched, so they
can be added and removed dynamically.
"""

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, ALL, ctx, no_update

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5

# Backtest uses a blue accent to distinguish it from the green pages.
BLUE = "#4aa8ff"
BLUE_DK = "#04121f"
GREY = "#8a93a3"
HIDE = dict(display="none")
REBAL_OPTS = ["None", "Monthly", "Quarterly", "Annually"]
REBAL_STEP = {"None": 10 ** 9, "Monthly": 1, "Quarterly": 3, "Annually": 12}
# Backtest window: number of monthly returns simulated (None = all history
# yfinance has, capped by its "max" fetch).
PERIOD_OPTS   = ["1Y", "3Y", "5Y", "10Y", "Max"]
PERIOD_MONTHS = {"1Y": 12, "3Y": 36, "5Y": 60, "10Y": 120, "Max": None}
PERIOD_LABEL  = {"1Y": "the last year", "3Y": "the last 3 years",
                 "5Y": "the last 5 years", "10Y": "the last 10 years",
                 "Max": "all available history"}
DEFAULT_PERIOD = "5Y"

# Benchmark universe. AUD-denominated ASX-listed proxies so their monthly
# returns line up with the holdings; users compare the strategy against any
# subset. `seed`/`drift` feed the deterministic modelled fallback.
BENCH_OPTS = {
    "asx":   dict(label="ASX 200",       short="ASX",   yf="STW.AX",  color="#8a93a3", seed=444555, drift=0.0088),
    "sp500": dict(label="S&P 500",       short="S&P",   yf="IVV.AX",  color="#f0a868", seed=50231,  drift=0.0102),
    "ndx":   dict(label="Nasdaq 100",    short="NDX",   yf="NDQ.AX",  color="#b08bff", seed=70419,  drift=0.0130),
    "vgs":   dict(label="Global Shares", short="VGS",   yf="VGS.AX",  color="#34d399", seed=33127,  drift=0.0100),
    "gold":  dict(label="Gold",          short="GOLD",  yf="GOLD.AX", color="#fbbf24", seed=27781,  drift=0.0075),
    "bonds": dict(label="Aus Bonds",     short="BONDS", yf="VAF.AX",  color="#f472b6", seed=88231,  drift=0.0026),
}
DEFAULT_BENCH = ["asx"]


def _hex_rgba(color, alpha):
    hx = color.lstrip("#")
    if len(hx) == 6:
        r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return f"rgba(138,147,163,{alpha})"


# ── security catalog ──────────────────────────────────────────────────
_G = "oklch(0.80 0.13 158)"; _B = "oklch(0.76 0.13 235)"
_P = "oklch(0.70 0.13 292)"; _BD = "oklch(0.74 0.13 300)"
_PR = "oklch(0.82 0.12 78)"; _CS = "oklch(0.72 0.015 250)"
CATALOG = [
    dict(key="VAS", name="Vanguard Australian Shares", cls="Aus Shares", color=_G, yf="VAS.AX", seed=730451, drift=0.0092, vol=0.040, sBeta=1.00),
    dict(key="A200", name="Betashares Australia 200", cls="Aus Shares", color=_G, yf="A200.AX", seed=111203, drift=0.0090, vol=0.040, sBeta=1.00),
    dict(key="VGS", name="Vanguard MSCI Intl Shares", cls="Intl Shares", color=_B, yf="VGS.AX", seed=50231, drift=0.0115, vol=0.042, sBeta=0.85),
    dict(key="VGAD", name="Vanguard Intl Shares (Hedged)", cls="Intl Shares", color=_B, yf="VGAD.AX", seed=62890, drift=0.0120, vol=0.046, sBeta=0.90),
    dict(key="IVV", name="iShares S&P 500", cls="Intl Shares", color=_B, yf="IVV.AX", seed=33127, drift=0.0122, vol=0.044, sBeta=0.90),
    dict(key="NDQ", name="Betashares Nasdaq 100", cls="Tech", color=_P, yf="NDQ.AX", seed=70419, drift=0.0150, vol=0.062, sBeta=1.15),
    dict(key="VAF", name="Vanguard Aus Fixed Interest", cls="Bonds", color=_BD, yf="VAF.AX", seed=88231, drift=0.0026, vol=0.011, sBeta=-0.10),
    dict(key="VGB", name="Vanguard Aus Govt Bond", cls="Bonds", color=_BD, yf="VGB.AX", seed=90114, drift=0.0022, vol=0.010, sBeta=-0.12),
    dict(key="VAP", name="Vanguard Aus Property", cls="Property", color=_PR, yf="VAP.AX", seed=41556, drift=0.0075, vol=0.050, sBeta=1.10),
    dict(key="GOLD", name="Global X Physical Gold", cls="Commodity", color="oklch(0.83 0.12 90)", yf="GOLD.AX", seed=27781, drift=0.0080, vol=0.045, sBeta=-0.20),
    dict(key="CBA", name="Commonwealth Bank", cls="Aus Share", color="oklch(0.78 0.12 200)", yf="CBA.AX", seed=60023, drift=0.0100, vol=0.050, sBeta=1.00),
    dict(key="BHP", name="BHP Group", cls="Aus Share", color="oklch(0.75 0.14 55)", yf="BHP.AX", seed=71540, drift=0.0085, vol=0.065, sBeta=1.10),
    dict(key="CSL", name="CSL Limited", cls="Aus Share", color="oklch(0.75 0.13 350)", yf="CSL.AX", seed=82260, drift=0.0105, vol=0.055, sBeta=0.80),
    dict(key="WES", name="Wesfarmers", cls="Aus Share", color="oklch(0.80 0.13 145)", yf="WES.AX", seed=93770, drift=0.0098, vol=0.048, sBeta=0.95),
    dict(key="TLS", name="Telstra Group", cls="Aus Share", color="oklch(0.76 0.11 210)", yf="TLS.AX", seed=10480, drift=0.0060, vol=0.045, sBeta=0.70),
    dict(key="CASH", name="Cash (AUD)", cls="Cash", color=_CS, yf=None, seed=12000, drift=0.0034, vol=0.0004, sBeta=0.00),
]
CAT = {c["key"]: c for c in CATALOG}

PRESETS = [
    dict(name="Balanced", desc="60/40 growth & defensive", tag="CLASSIC", tagColor=T3,
         w=[("VAS", 30), ("VGS", 25), ("NDQ", 5), ("VAF", 30), ("CASH", 10)]),
    dict(name="Aggressive growth", desc="Equity-heavy, global tilt", tag="HIGH RISK", tagColor="#ff9b6b",
         w=[("VGS", 35), ("NDQ", 30), ("VAS", 30), ("VAF", 5)]),
    dict(name="Conservative", desc="Capital preservation", tag="LOW RISK", tagColor=ACCENT,
         w=[("VAF", 50), ("CASH", 25), ("VAS", 15), ("GOLD", 10)]),
]
MY_PORTFOLIO = [("VAS", 23), ("VGS", 21), ("CBA", 10), ("NDQ", 10), ("BHP", 8),
                ("CSL", 8), ("WES", 5), ("VAF", 5), ("CASH", 4), ("TLS", 3), ("VAP", 2)]


# ── monthly returns (yfinance, cached, with modelled fallback) ─────────

# Palette for holdings not in the catalog (e.g. your real IBKR tickers).
PALETTE = ["#4aa8ff", "#36d399", "#b08bff", "#ffce6b", "#ff8b8b", "#5ad1c8",
           "#f0883e", "#e879f9", "#60a5fa", "#a3e635", "#8a93a3"]


def _mk(key, weight):
    """Build a holding dict from the catalog (name / yfinance ticker / colour)."""
    c = CAT.get(key, {})
    return {"key": key, "weight": weight, "name": c.get("name", key),
            "yf": c.get("yf"), "color": c.get("color", GREY)}


# yfinance tickers already represented in the catalog — a live search result
# for e.g. "VAS.AX" duplicates the catalog's VAS row, so it is suppressed.
_CATALOG_YF = {c["yf"] for c in CATALOG if c.get("yf")}


def _holding_from_key(key, weight, live_meta=None):
    """Holding dict for a picked key — catalog entry or live search result.

    Live results carry their Yahoo symbol as both key and yfinance ticker, so
    the simulation fetches real prices rather than the modelled fallback.
    """
    if key in CAT:
        return _mk(key, weight)
    m = (live_meta or {}).get(key) or {}
    color = PALETTE[sum(ord(ch) for ch in key) % len(PALETTE)]
    return {"key": key, "weight": weight, "name": m.get("name", key),
            "yf": m.get("yf", key), "color": color}


# ── live security search (Yahoo Finance, cached ~10 min) ──────────────
_search_cache: dict = {}
_SEARCH_TTL = 600


def _live_search(query: str) -> list:
    """Search Yahoo Finance for any listed equity/ETF (tickers or names)."""
    import time
    q = (query or "").strip()
    if len(q) < 2:
        return []
    now = time.time()
    hit = _search_cache.get(q.lower())
    if hit and now - hit[0] < _SEARCH_TTL:
        return hit[1]
    from dashboard.pages.search_page import _yf_search
    results = _yf_search(q)
    _search_cache[q.lower()] = (now, results)
    if len(_search_cache) > 200:
        _search_cache.pop(next(iter(_search_cache)))
    return results


def _synthetic(n=66, bench_keys=None):
    import random
    bench_keys = bench_keys or list(DEFAULT_BENCH)
    # A handful of drawdown shocks spread proportionally across the window so
    # short and long backtests both contain stress periods.
    shocks = {int(n * f): d for f, d in
              ((0.12, -0.052), (0.21, -0.041), (0.45, -0.066),
               (0.67, -0.038), (0.79, -0.030))}
    end = pd.Timestamp.today().normalize().replace(day=1)
    months = [end - pd.DateOffset(months=n - 1 - i) for i in range(n)]
    rets = {}
    for c in CATALOG:
        r = random.Random(c["seed"])
        rets[c["key"]] = [c["drift"] + (r.random() - 0.5) * c["vol"] + shocks.get(i, 0) * c["sBeta"]
                          for i in range(n)]
    benches = {}
    for bk in bench_keys:
        opt = BENCH_OPTS.get(bk) or BENCH_OPTS["asx"]
        rb = random.Random(opt["seed"])
        benches[bk] = [opt["drift"] + (rb.random() - 0.5) * 0.040 + shocks.get(i, 0)
                       for i in range(n)]
    return months, rets, benches


def _month_index(s):
    """Snap a monthly close series onto a timezone-naive month-start index.

    yfinance timestamps each ``1mo`` bar in its exchange's own timezone, so the
    same calendar month resolves to a different instant per venue (ASX
    ``+1000`` vs US ``-0400``). Joining those series on their raw indexes yields
    the union — two rows per month for a mixed AUD/USD portfolio — which then
    makes ``tail(n_months + 1)`` cover only half the intended window. Collapsing
    every series to the calendar month start keeps one row per month so they
    align.
    """
    idx = s.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s = s.copy()
    s.index = idx.to_period("M").to_timestamp()
    return s[~s.index.duplicated(keep="last")]


def _fallback_series(key, n):
    """Deterministic modelled monthly returns for a ticker we can't fetch."""
    import random
    r = random.Random(sum(ord(ch) for ch in str(key)) + 17)
    return [0.008 + (r.random() - 0.5) * 0.05 for _ in range(n)]


def _synthetic_for(holdings, n=66, bench_keys=None):
    months, srets, sbenches = _synthetic(n, bench_keys)
    rets = {}
    for h in holdings:
        k = h["key"]
        if k in srets:
            rets[k] = srets[k]
        elif k == "CASH":
            rets[k] = [0.0034] * n
        else:
            rets[k] = _fallback_series(k, n)
    return months, rets, sbenches


def _monthly_returns(holdings, period=DEFAULT_PERIOD, bench_keys=None):
    """Monthly returns for the current holdings + each selected benchmark over
    the chosen backtest window.

    Fetches each holding's yfinance monthly closes (cached in market_data),
    aligns them on a shared month index and computes returns. CASH is a flat
    accrual; anything that can't be fetched gets a deterministic modelled
    series so the simulation always runs. Returns ``(months, rets, benches)``
    where ``benches`` is ``{bench_key: [monthly return, ...]}``.
    """
    from services import market_data
    bench_keys = [k for k in (bench_keys or DEFAULT_BENCH) if k in BENCH_OPTS]
    n_months = PERIOD_MONTHS.get(period, PERIOD_MONTHS[DEFAULT_PERIOD])
    fetch = "max" if n_months is None else "10y"
    print(f"[BT] _monthly_returns: period={period} (n_months={n_months}, "
          f"fetch={fetch!r}), holdings="
          f"{[(h['key'], h.get('yf')) for h in holdings]}, benchmarks={bench_keys}",
          flush=True)
    frames = {}
    for h in holdings:
        yf = h.get("yf")
        if h["key"] == "CASH" or not yf:
            print(f"[BT]   holding {h['key']}: skipped "
                  f"({'cash' if h['key'] == 'CASH' else 'no yf ticker'})", flush=True)
            continue
        try:
            df = market_data._history(yf, fetch, "1mo")
        except Exception as exc:
            print(f"[BT]   holding {h['key']} ({yf}): EXCEPTION "
                  f"{type(exc).__name__}: {exc}", flush=True)
            df = None
        rows = 0 if df is None else len(df)
        has_close = df is not None and not getattr(df, "empty", True) and "Close" in df
        if has_close:
            s = _month_index(df["Close"].dropna())
            if len(s) >= 2:
                frames[h["key"]] = s
                print(f"[BT]   holding {h['key']} ({yf}): {len(s)} monthly closes", flush=True)
            else:
                print(f"[BT]   holding {h['key']} ({yf}): only {len(s)} closes "
                      f"— using modelled fallback", flush=True)
        else:
            print(f"[BT]   holding {h['key']} ({yf}): no data ({rows} rows) "
                  f"— using modelled fallback", flush=True)
    if not frames:
        print("[BT] no holdings fetched from yfinance — FULL synthetic fallback",
              flush=True)
        return _synthetic_for(holdings, n_months or 120, bench_keys)

    # Fetch each selected benchmark; align it in the same frame as the holdings.
    bench_cols = {}
    for bk in bench_keys:
        byf = BENCH_OPTS[bk]["yf"]
        try:
            bdf = market_data._history(byf, fetch, "1mo")
        except Exception as exc:
            print(f"[BT]   benchmark {bk} ({byf}): EXCEPTION "
                  f"{type(exc).__name__}: {exc}", flush=True)
            bdf = None
        if bdf is not None and not getattr(bdf, "empty", True) and "Close" in bdf:
            s = _month_index(bdf["Close"].dropna())
            if len(s) >= 2:
                bench_cols[bk] = s
                print(f"[BT]   benchmark {bk} ({byf}): {len(s)} monthly closes", flush=True)
            else:
                print(f"[BT]   benchmark {bk} ({byf}): only {len(s)} closes "
                      f"— using modelled fallback", flush=True)
        else:
            print(f"[BT]   benchmark {bk} ({byf}): no data — using modelled fallback",
                  flush=True)

    allframes = dict(frames)
    for bk, s in bench_cols.items():
        allframes[f"__B_{bk}__"] = s
    close = pd.DataFrame(allframes).ffill()
    if n_months is not None:
        close = close.tail(n_months + 1)
    r = close.pct_change().dropna(how="all").fillna(0.0)
    months = list(r.index)
    n = len(months)
    print(f"[BT] aligned {n} monthly periods across columns {list(r.columns)}"
          + (f"  ({months[0]:%b %Y} -> {months[-1]:%b %Y})" if n else ""), flush=True)
    rets = {}
    for h in holdings:
        k = h["key"]
        if k in r.columns:
            rets[k] = [float(v) for v in r[k].values]
        elif k == "CASH":
            rets[k] = [0.0034] * n
        else:
            rets[k] = _fallback_series(k, n)
    benches = {}
    for bk in bench_keys:
        col = f"__B_{bk}__"
        benches[bk] = ([float(v) for v in r[col].values] if col in r.columns
                       else _fallback_series(f"__BENCH_{bk}__", n))
    return months, rets, benches


def _normalise_100(holdings):
    """Scale weights proportionally so they total exactly 100 (integers).

    The Run button requires a 100% total; loaded portfolios exclude cash so
    their raw market-value weights rarely sum to 100 on their own.
    """
    total = sum(h["weight"] for h in holdings)
    if not holdings or not total:
        return holdings
    scaled = [h["weight"] * 100.0 / total for h in holdings]
    ints = [int(round(s)) for s in scaled]
    # Push any rounding residue onto the largest position.
    ints[scaled.index(max(scaled))] += 100 - sum(ints)
    return [{**h, "weight": w} for h, w in zip(holdings, ints)]


def _load_my_portfolio():
    """Build backtest holdings from the live portfolio (get_data holdings).

    Weights are current market-value weights normalised to a 100% total;
    tickers map to yfinance symbols (catalog match first, else via
    currency/exchange). Falls back to a representative sample if no live
    holdings are available (e.g. demo mode).
    """
    from dashboard.data import get_data
    from services import market_data
    meta = {}
    try:
        from services.ibkr_client import get_client
        hdf = get_client().cached_snapshot.holdings
        if hdf is not None and not hdf.empty:
            for _, row in hdf.iterrows():
                meta[str(row["Symbol"]).upper()] = (
                    str(row.get("Currency") or "USD"),
                    str(row.get("Exchange") or ""))
    except Exception:
        pass

    out = []
    try:
        holdings = get_data().holdings
    except Exception:
        holdings = []
    for i, h in enumerate(holdings):
        tk = (h.get("ticker") or "").upper()
        weight = round((h.get("weight") or 0) * 100, 1)
        if not tk or tk == "CASH" or (h.get("value") or 0) <= 0 or weight <= 0:
            continue
        if tk in CAT:
            out.append(_mk(tk, weight))
        else:
            cur, exch = meta.get(tk, ("USD", ""))
            out.append({"key": tk, "weight": weight, "name": h.get("name") or tk,
                        "yf": market_data.yf_ticker(tk, cur, exch),
                        "color": PALETTE[i % len(PALETTE)]})
    if not out:
        out = [_mk(k, w) for k, w in MY_PORTFOLIO]
    return _normalise_100(out)


# ── simulation + stats ────────────────────────────────────────────────

def _simulate(holdings, capital, rebalance, months, rets):
    if not holdings:
        return [capital] * (len(months) + 1), [0.0] * len(months)
    total_w = sum(h["weight"] for h in holdings) or 1
    target = {h["key"]: h["weight"] / total_w for h in holdings}
    step = REBAL_STEP.get(rebalance, 3)
    pos = {k: capital * w for k, w in target.items()}
    eq, prets = [capital], []
    for i in range(len(months)):
        prev = sum(pos.values())
        for k in pos:
            pos[k] *= 1 + rets.get(k, [0] * len(months))[i]
        cur = sum(pos.values())
        prets.append(cur / prev - 1 if prev else 0)
        eq.append(cur)
        if step != 10 ** 9 and (i + 1) % step == 0:
            pos = {k: cur * w for k, w in target.items()}
    return eq, prets


def _equity(rets, cap):
    out, v = [cap], cap
    for r in rets:
        v *= 1 + r
        out.append(v)
    return out


def _stats(prets, eq):
    n = len(prets)
    if n < 2:
        return None
    years = n / 12
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years else 0
    mean = sum(prets) / n
    sd = (sum((x - mean) ** 2 for x in prets) / n) ** 0.5
    vol = sd * (12 ** 0.5)
    rf = 0.04
    sharpe = (cagr - rf) / vol if vol else 0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    return dict(cagr=cagr, vol=vol, sharpe=sharpe, maxDD=mdd,
                best=max(prets), worst=min(prets),
                total=eq[-1] / eq[0] - 1, final=eq[-1])


# ── formatters ────────────────────────────────────────────────────────

def _money(n, signed=False):
    s = f"{abs(round(n)):,}"
    sign = "−" if n < 0 else ("+" if signed else "")
    return f"{sign}${s}"


def _pct(n, signed=False):
    sign = "−" if n < 0 else ("+" if signed else "")
    return f"{sign}{abs(n):.1f}%"


def _parse_capital(txt):
    try:
        v = int(float(str(txt).replace(",", "").replace("$", "").strip()))
        return max(1000, min(v, 1_000_000_000))
    except Exception:
        return 100_000


def _card(children, pad="20px"):
    return html.Div(children, style=dict(background=BG_CARD,
        border=f"1px solid {BORDER}", borderRadius="14px", padding=pad))


def _label(text):
    return html.Div(text, style=dict(fontSize="11px", color=T4, fontWeight="600",
        letterSpacing="0.4px", textTransform="uppercase", marginBottom="9px"))


# ── settings ─────────────────────────────────────────────────

def _settings_card():
    return _card([
        _label("Initial capital"),
        html.Div([
            html.Span("$", style=dict(color=T3,
                fontFamily="'JetBrains Mono',monospace", fontSize="14px")),
            dcc.Input(id="bt-capital", value="100,000", type="text", debounce=True,
                style=dict(flex="1", minWidth="0", background="transparent",
                    border="none", outline="none", color=T1,
                    fontFamily="'JetBrains Mono',monospace", fontSize="15px",
                    fontWeight="600")),
        ], style=dict(display="flex", alignItems="center", gap="9px",
            padding="9px 12px", borderRadius="9px",
            border="1px solid rgba(255,255,255,0.1)", marginBottom="18px")),
        _label("Backtest period"),
        html.Div([
            html.Button(opt, id={"type": "bt-period", "index": opt}, n_clicks=0,
                style=dict(flex="1", border="none", background="transparent",
                    color=T3, fontSize="11.5px", fontWeight="600",
                    padding="7px 4px", borderRadius="7px", cursor="pointer",
                    fontFamily="inherit"))
            for opt in PERIOD_OPTS
        ], style=dict(display="flex", gap="3px",
            background="rgba(255,255,255,0.04)", borderRadius="9px",
            padding="3px", marginBottom="18px")),
        _label("Rebalance"),
        html.Div([
            html.Button(opt, id={"type": "bt-rebal", "index": opt}, n_clicks=0,
                style=dict(flex="1", border="none", background="transparent",
                    color=T3, fontSize="11.5px", fontWeight="600",
                    padding="7px 4px", borderRadius="7px", cursor="pointer",
                    fontFamily="inherit"))
            for opt in REBAL_OPTS
        ], style=dict(display="flex", gap="3px",
            background="rgba(255,255,255,0.04)", borderRadius="9px", padding="3px",
            marginBottom="18px")),
        _label("Benchmarks"),
        html.Div([
            html.Button([
                html.Span(style=dict(width="11px", height="3px", borderRadius="2px",
                    background=opt["color"], display="inline-block", flexShrink="0")),
                html.Span(opt["short"]),
            ], id={"type": "bt-bench", "index": k}, n_clicks=0,
               style=dict(display="flex", alignItems="center", gap="6px",
                   border="1px solid rgba(255,255,255,0.1)", background="transparent",
                   color=T3, fontSize="11px", fontWeight="600", padding="5px 8px",
                   borderRadius="7px", cursor="pointer", fontFamily="inherit"))
            for k, opt in BENCH_OPTS.items()
        ], style=dict(display="flex", flexWrap="wrap", gap="6px")),
    ])


# ── builder: chooser + building (both always in layout) ───────────────

def _preset_btn(i, p):
    return html.Button([
        html.Div([
            html.Div(p["name"], style=dict(fontSize="13px", fontWeight="600", color=T1)),
            html.Div(p["desc"], style=dict(fontSize="11.5px", color=T4)),
        ], style=dict(minWidth="0")),
        html.Span(p["tag"], style=dict(marginLeft="auto", fontSize="10px",
            color=p["tagColor"], fontFamily="'JetBrains Mono',monospace",
            whiteSpace="nowrap")),
    ], id={"type": "bt-preset", "index": i}, n_clicks=0, style=dict(
        display="flex", alignItems="center", gap="10px", padding="11px 13px",
        borderRadius="10px", border="1px solid rgba(255,255,255,0.08)",
        background="transparent", cursor="pointer", textAlign="left",
        fontFamily="inherit"))


def _chooser():
    return html.Div([
        html.Div(html.Div("▤", style=dict(color=BLUE, fontSize="20px")),
            style=dict(display="flex", alignItems="center", justifyContent="center",
                width="44px", height="44px", borderRadius="12px",
                background="rgba(74,168,255,0.12)", marginBottom="14px")),
        html.Div("Build a portfolio", style=dict(fontSize="15px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif")),
        html.Div("Load your current holdings, then tweak them — or start from a "
                 "blank slate and add securities yourself.",
                 style=dict(fontSize="12.5px", color=T3, marginTop="4px",
                            lineHeight="1.5")),
        html.Button([html.Span("↓ "), "Load my portfolio"], id="bt-load", n_clicks=0,
            style=dict(display="flex", alignItems="center", justifyContent="center",
                gap="9px", width="100%", marginTop="16px", padding="12px 14px",
                borderRadius="10px", border="none", background=BLUE, color=BLUE_DK,
                fontWeight="600", fontSize="13.5px", cursor="pointer",
                fontFamily="inherit")),
        html.Button([html.Span("+ "), "Start from scratch"], id="bt-scratch",
            n_clicks=0, style=dict(display="flex", alignItems="center",
                justifyContent="center", gap="9px", width="100%", marginTop="9px",
                padding="12px 14px", borderRadius="10px",
                border="1px solid rgba(255,255,255,0.12)", background="transparent",
                color=T1, fontWeight="600", fontSize="13.5px", cursor="pointer",
                fontFamily="inherit")),
        html.Div([
            html.Span(style=dict(flex="1", height="1px",
                                 background="rgba(255,255,255,0.07)")),
            "or a template",
            html.Span(style=dict(flex="1", height="1px",
                                 background="rgba(255,255,255,0.07)")),
        ], style=dict(display="flex", alignItems="center", gap="12px",
            margin="18px 0 14px", color=T5, fontSize="11px", fontWeight="600",
            letterSpacing="0.5px", textTransform="uppercase")),
        html.Div([_preset_btn(i, p) for i, p in enumerate(PRESETS)],
                 style=dict(display="flex", flexDirection="column", gap="9px")),
    ], id="bt-chooser")


# Run button: enabled (blue) only when weights total exactly 100%.
_RUN_BASE = dict(display="flex", alignItems="center", justifyContent="center",
    gap="8px", width="100%", marginTop="14px", padding="12px 14px",
    borderRadius="10px", border="none", fontWeight="600", fontSize="13.5px",
    fontFamily="inherit")
_RUN_ENABLED  = {**_RUN_BASE, "background": BLUE, "color": BLUE_DK,
                 "cursor": "pointer"}
_RUN_DISABLED = {**_RUN_BASE, "background": "rgba(255,255,255,0.05)",
                 "color": T4, "cursor": "not-allowed"}


def _building_shell():
    return html.Div([
        html.Div([
            html.Div("Securities", style=dict(fontSize="14px", fontWeight="600",
                fontFamily="'Space Grotesk',sans-serif")),
            html.Div(id="bt-count", style=dict(marginLeft="8px", fontSize="12px",
                color=T4)),
            html.Div(id="bt-sum", style=dict(marginLeft="auto",
                fontFamily="'JetBrains Mono',monospace", fontSize="12px",
                fontWeight="600")),
        ], style=dict(display="flex", alignItems="center", marginBottom="16px")),
        html.Div(id="bt-sliders"),
        html.Button([html.Span("+ "), "Add security"], id="bt-add", n_clicks=0,
            style=dict(display="flex", alignItems="center", justifyContent="center",
                gap="8px", width="100%", marginTop="16px", padding="11px",
                borderRadius="10px", border="1px dashed rgba(255,255,255,0.16)",
                background="transparent", color=T2, fontWeight="600", fontSize="13px",
                cursor="pointer", fontFamily="inherit")),
        html.Div([
            html.Button([html.Span("↓ "), "Load mine"], id="bt-load-2", n_clicks=0,
                style=dict(border="none", background="transparent", color=T3,
                    fontSize="12px", fontWeight="600", cursor="pointer",
                    fontFamily="inherit", padding="0")),
            html.Button([html.Span("🗑 "), "Clear"], id="bt-clear", n_clicks=0,
                style=dict(border="none", background="transparent", color=T3,
                    fontSize="12px", fontWeight="600", cursor="pointer",
                    fontFamily="inherit", padding="0", marginLeft="auto")),
        ], style=dict(display="flex", gap="16px", marginTop="16px",
            paddingTop="14px", borderTop="1px solid rgba(255,255,255,0.06)")),
        html.Button([html.Span("▶ "), "Run backtest"], id="bt-run", n_clicks=0,
            disabled=True, style=_RUN_DISABLED),
        html.Div(id="bt-run-hint", style=HIDE),
    ], id="bt-building", style=HIDE)


def _slider_row(h):
    c = CAT.get(h["key"], {})
    color = h.get("color") or c.get("color", GREY)
    name = h.get("name") or c.get("name", h["key"])
    return html.Div([
        html.Div([
            html.Span(style=dict(width="9px", height="9px", borderRadius="3px",
                                 flexShrink="0", background=color)),
            html.Span(h["key"], style=dict(fontFamily="'JetBrains Mono',monospace",
                fontSize="12.5px", fontWeight="600")),
            html.Span(name, style=dict(fontSize="11.5px", color=T4,
                whiteSpace="nowrap", overflow="hidden", textOverflow="ellipsis",
                flex="1")),
            html.Span(f"{h['weight']:.0f}%", style=dict(
                fontFamily="'JetBrains Mono',monospace", fontSize="13px",
                fontWeight="600", color=T1)),
            html.Button("⇄", id={"type": "bt-replace", "index": h["key"]},
                n_clicks=0, title="Replace", style=dict(display="flex",
                    alignItems="center", justifyContent="center", width="24px",
                    height="24px", borderRadius="7px", border="none",
                    background="rgba(255,255,255,0.05)", color="#8a909e",
                    cursor="pointer", flexShrink="0", fontSize="12px")),
            html.Button("−", id={"type": "bt-remove", "index": h["key"]},
                n_clicks=0, title="Remove", style=dict(display="flex",
                    alignItems="center", justifyContent="center", width="24px",
                    height="24px", borderRadius="7px", border="none",
                    background="rgba(255,255,255,0.05)", color="#8a909e",
                    cursor="pointer", flexShrink="0", fontSize="14px")),
        ], style=dict(display="flex", alignItems="center", gap="8px",
                      marginBottom="7px")),
        dcc.Input(type="range", min=0, max=100, step=1, value=h["weight"],
            id={"type": "bt-weight", "index": h["key"]},
            style={"width": "100%", "accentColor": color, "cursor": "pointer"}),
    ])


# ── security picker (static shell, toggled) ───────────────────────────

def _picker_shell():
    return html.Div([
        html.Div(id="bt-picker-scrim", n_clicks=0, style=dict(position="absolute",
            inset="0", background="rgba(6,8,11,0.62)", backdropFilter="blur(3px)")),
        html.Div([
            html.Div([
                html.Div([
                    html.Div(id="bt-picker-title", children="Add security",
                        style=dict(fontSize="15px", fontWeight="600",
                            fontFamily="'Space Grotesk',sans-serif")),
                    html.Button("✕", id="bt-picker-close", n_clicks=0, style=dict(
                        marginLeft="auto", width="28px", height="28px",
                        borderRadius="8px", border="none",
                        background="rgba(255,255,255,0.05)", color=T2,
                        cursor="pointer")),
                ], style=dict(display="flex", alignItems="center")),
                html.Div(dcc.Input(id="bt-search", value="", debounce=0.4,
                    placeholder="Search any ticker, ETF or company…", style=dict(width="100%",
                        background="transparent", border="none", outline="none",
                        color=T1, fontSize="13.5px", fontFamily="inherit")),
                    style=dict(display="flex", alignItems="center", gap="9px",
                        marginTop="12px", padding="9px 12px", borderRadius="9px",
                        border="1px solid rgba(255,255,255,0.1)",
                        background="rgba(255,255,255,0.02)")),
            ], style=dict(padding="18px 20px 14px",
                borderBottom="1px solid rgba(255,255,255,0.06)")),
            html.Div(id="bt-picker-list", style=dict(overflow="auto", padding="8px",
                display="flex", flexDirection="column", gap="2px",
                maxHeight="calc(100vh - 260px)")),
        ], style=dict(position="relative", zIndex="1", width="460px",
            maxWidth="calc(100vw - 40px)", display="flex", flexDirection="column",
            background="#14171f", border="1px solid rgba(255,255,255,0.1)",
            borderRadius="16px", boxShadow="0 24px 70px rgba(0,0,0,0.55)",
            overflow="hidden")),
    ], id="bt-picker-backdrop", style=HIDE)


# ── page ──────────────────────────────────────────────────────────────

def backtest_page():
    # Lightweight toolbar (in place of a page header): run subtitle + reset.
    toolbar = html.Div([
        html.Div(id="bt-subtitle",
                 children=f"Simulate a portfolio over {PERIOD_LABEL[DEFAULT_PERIOD]} · AUD",
                 style=dict(fontSize="12.5px", color=T4, flex="1", minWidth="0")),
        html.Button([html.Span("↻ "), "Reset"], id="bt-reset", n_clicks=0,
            style=dict(padding="8px 14px", borderRadius="9px",
                border="1px solid rgba(255,255,255,0.1)", background="transparent",
                color=T1, fontWeight="600", fontSize="13px", cursor="pointer",
                fontFamily="inherit", flexShrink="0")),
    ], style=dict(display="flex", alignItems="center", gap="14px"))
    content = html.Div([
        html.Div([_settings_card(),
                  _card([_chooser(), _building_shell()])],
                 style=dict(display="flex", flexDirection="column", gap="18px",
                            position="sticky", top="24px")),
        html.Div([toolbar,
            # Spinner overlays the results while Run backtest computes (it fetches
            # yfinance history per holding + benchmark). delay_show avoids a flash
            # on the fast no-op reruns that weight/period/benchmark edits trigger.
            dcc.Loading(id="bt-results-loading", type="circle", color=BLUE,
                delay_show=350,
                children=html.Div(id="bt-results", style=dict(display="flex",
                    flexDirection="column", gap="18px"))),
        ], style=dict(display="flex", flexDirection="column", gap="18px")),
    ], style=dict(padding="24px 28px 40px", display="grid",
                  gridTemplateColumns="320px minmax(0,1fr)", gap="18px",
                  alignItems="start"))
    return html.Div([
        dcc.Store(id="bt-state", data=dict(mode="empty", holdings=[],
                                           rebalance="Quarterly", capital=100_000,
                                           period=DEFAULT_PERIOD,
                                           benchmarks=list(DEFAULT_BENCH))),
        dcc.Store(id="bt-picker", data=dict(open=False, mode="add", target=None)),
        # Metadata (name / yfinance ticker) for the live search results currently
        # shown in the picker, keyed by symbol — read back when one is picked.
        dcc.Store(id="bt-picker-results", data={}),
        content,
        _picker_shell(),
    ], style=dict(display="flex", flexDirection="column"))


# ── results render helpers ────────────────────────────────────────────

def _line_fig(months, strat, benches):
    """``benches`` is a list of ``dict(label, color, values)`` (one per selected
    benchmark) drawn as dotted lines beneath the strategy."""
    x = list(months)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=strat, mode="lines", name="Strategy",
        line=dict(color=BLUE, width=2.4), fill="tozeroy",
        fillcolor="rgba(74,168,255,0.16)",
        hovertemplate="<b>%{y:$,.0f}</b><br>%{x|%b %Y}<extra>Strategy</extra>"))
    for b in benches:
        fig.add_trace(go.Scatter(x=x, y=b["values"], mode="lines", name=b["label"],
            line=dict(color=b["color"], width=1.6, dash="dot"),
            hovertemplate="<b>%{y:$,.0f}</b><br>%{x|%b %Y}<extra>"
                          + b["label"] + "</extra>"))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=56, t=12, b=24), showlegend=False, hovermode="x unified",
        hoverlabel=dict(bgcolor="#1b1f28", bordercolor="rgba(255,255,255,0.1)",
            font=dict(family="JetBrains Mono, monospace", color=T1, size=12)),
        xaxis=dict(showgrid=False, zeroline=False, tickformat="%b '%y",
            tickfont=dict(family="JetBrains Mono, monospace", color=T5, size=10)),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", side="right",
            nticks=4, tickformat="$~s",
            tickfont=dict(family="JetBrains Mono, monospace", color=T5, size=10)),
        font=dict(family="JetBrains Mono, monospace", color=T3))
    return fig


def _dd_fig(months, eq, benches=None):
    def _compute_dd(vals):
        peak, dd = vals[0], []
        for v in vals:
            peak = max(peak, v)
            dd.append((v / peak - 1) * 100 if peak else 0)
        return dd

    portfolio_dd = _compute_dd(eq)
    x = [months[0]] + list(months) if len(months) == len(eq) - 1 else list(range(len(eq)))
    if len(x) != len(eq):
        x = list(range(len(eq)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=portfolio_dd, mode="lines", name="Strategy",
        line=dict(color=RED, width=1.8), fill="tozeroy",
        fillcolor="rgba(255,107,107,0.18)",
        hovertemplate="<b>Strategy: %{y:.1f}%</b><extra></extra>"))
    for b in (benches or []):
        bench_dd = _compute_dd(b["values"])
        fig.add_trace(go.Scatter(x=x, y=bench_dd, mode="lines", name=b["label"],
            line=dict(color=b["color"], width=1.2, dash="dot"),
            hovertemplate=f"<b>{b['label']}: %{{y:.1f}}%</b><extra></extra>"))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=56, t=8, b=24), showlegend=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, tickformat="%b '%y",
            tickfont=dict(family="JetBrains Mono, monospace", color=T5, size=10)),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", side="right",
            nticks=3, ticksuffix="%",
            tickfont=dict(family="JetBrains Mono, monospace", color=T5, size=10)),
        font=dict(family="JetBrains Mono, monospace", color=T3))
    return fig


def _empty_results():
    return html.Div([
        html.Div(html.Div("▤", style=dict(color="#4a5060", fontSize="26px")),
            style=dict(display="flex", alignItems="center", justifyContent="center",
                width="60px", height="60px", borderRadius="16px",
                background="rgba(255,255,255,0.04)", marginBottom="18px")),
        html.Div("No simulation yet", style=dict(fontSize="16px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif")),
        html.Div("Load your portfolio or add securities on the left, set the "
                 "weights to exactly 100%, then press Run backtest to simulate "
                 "against your chosen benchmarks.",
                 style=dict(fontSize="13px", color=T4, marginTop="6px",
                            maxWidth="340px", lineHeight="1.6")),
    ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding="60px 30px", display="flex",
        flexDirection="column", alignItems="center", textAlign="center",
        minHeight="420px", justifyContent="center"))


def _result_kpi(label, value, sub, color):
    return _card([
        html.Div(label, style=dict(fontSize="12px", color=T3, fontWeight="500")),
        html.Div(value, style=dict(fontFamily="'Space Grotesk',sans-serif",
            fontSize="23px", fontWeight="600", letterSpacing="0.2px", lineHeight="1",
            marginTop="9px", color=color)),
        html.Div(sub, style=dict(fontSize="11.5px", color=T4, marginTop="8px",
            fontFamily="'JetBrains Mono',monospace")),
    ], pad="16px 18px")


def _annual_bars(months, prets):
    by_year = {}
    for i, m in enumerate(months):
        by_year.setdefault(m.year, 1.0)
        by_year[m.year] *= 1 + prets[i]
    years = sorted(by_year)
    vals = [(y, (by_year[y] - 1) * 100) for y in years]
    mx = max((abs(v) for _, v in vals), default=1) or 1
    bars = []
    for y, v in vals:
        color = ACCENT if v >= 0 else RED
        bars.append(html.Div([
            html.Div(_pct(v, True), style=dict(
                fontFamily="'JetBrains Mono',monospace", fontSize="10.5px",
                fontWeight="600", color=color)),
            html.Div(style=dict(width="100%", borderRadius="5px 5px 3px 3px",
                background=color, height=f"{abs(v) / mx * 100:.0f}%", minHeight="2px")),
            html.Div(str(y), style=dict(fontSize="10.5px", color=T4,
                fontFamily="'JetBrains Mono',monospace")),
        ], style=dict(flex="1", display="flex", flexDirection="column",
            alignItems="center", gap="8px", height="100%",
            justifyContent="flex-end")))
    return _card([
        html.Div("Annual returns", style=dict(fontSize="14px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif", marginBottom="18px")),
        html.Div(bars, style=dict(display="flex", alignItems="flex-end",
            gap="10px", height="150px")),
    ])


def _metrics_table(ss, benches):
    """``benches`` is a list of ``dict(short, color, stat)`` — one column each.
    The first benchmark is primary and colours the strategy out/underperformance.
    """
    def col(v):
        return ACCENT if v >= 0 else RED
    p = lambda v: _pct(v * 100)
    primary = benches[0]["stat"] if benches else None

    def dcolor(key):
        return col(ss[key] - primary[key]) if primary is not None else T1

    # each metric carries a formatter that maps a stats dict to its cell text.
    rows = [
        dict(label="CAGR", fmt=lambda s: p(s["cagr"]), strat=p(ss["cagr"]), color=dcolor("cagr")),
        dict(label="Total return", fmt=lambda s: p(s["total"]), strat=p(ss["total"]), color=dcolor("total")),
        dict(label="Volatility (ann)", fmt=lambda s: p(s["vol"]), strat=p(ss["vol"]), color=T1),
        dict(label="Sharpe ratio", fmt=lambda s: f"{s['sharpe']:.2f}", strat=f"{ss['sharpe']:.2f}", color=dcolor("sharpe")),
        dict(label="Max drawdown", fmt=lambda s: p(s["maxDD"]), strat=p(ss["maxDD"]), color=dcolor("maxDD")),
        dict(label="Best month", fmt=lambda s: _pct(s["best"] * 100, True), strat=_pct(ss["best"] * 100, True), color=ACCENT),
        dict(label="Worst month", fmt=lambda s: _pct(s["worst"] * 100, True), strat=_pct(ss["worst"] * 100, True), color=RED),
    ]
    grid_cols = "1fr auto" + " auto" * len(benches)
    cell_w = "74px" if len(benches) <= 1 else "62px"
    caption = ("vs " + " · ".join(b["label"] for b in benches)) if benches \
        else "strategy only"
    head = html.Div([
        html.Div("Results", style=dict(fontSize="14px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif")),
        html.Div(caption, style=dict(marginLeft="auto", fontSize="11px",
            color=T4, fontFamily="'JetBrains Mono',monospace")),
    ], style=dict(display="flex", alignItems="baseline", marginBottom="6px",
                  gap="10px"))
    col_head = html.Div([
        html.Div("Metric"),
        html.Div("Strategy", style=dict(textAlign="right", width=cell_w)),
        *[html.Div(b["short"], style=dict(textAlign="right", width=cell_w,
            color=b["color"])) for b in benches],
    ], style=dict(display="grid", gridTemplateColumns=grid_cols,
        fontSize="11px", color=T4, fontWeight="600", letterSpacing="0.4px",
        textTransform="uppercase", padding="10px 0 8px",
        borderBottom="1px solid rgba(255,255,255,0.06)"))
    body = [html.Div([
        html.Div(m["label"], style=dict(fontSize="13px", color="#c5cad3")),
        html.Div(m["strat"], style=dict(textAlign="right", width=cell_w,
            fontFamily="'JetBrains Mono',monospace", fontSize="13px",
            fontWeight="600", color=m["color"])),
        *[html.Div(m["fmt"](b["stat"]), style=dict(textAlign="right", width=cell_w,
            fontFamily="'JetBrains Mono',monospace", fontSize="13px", color=T2))
          for b in benches],
    ], style=dict(display="grid", gridTemplateColumns=grid_cols,
        alignItems="center", padding="10px 0",
        borderBottom="1px solid rgba(255,255,255,0.04)")) for m in rows]
    return _card([head, col_head, *body])


# ── render callbacks ──────────────────────────────────────────────────

@app.callback(
    Output("bt-chooser", "style"),
    Output("bt-building", "style"),
    Output("bt-sliders", "children"),
    Output("bt-count", "children"),
    Output("bt-sum", "children"),
    Output("bt-sum", "style"),
    Output("bt-run", "disabled"),
    Output("bt-run", "style"),
    Output("bt-run-hint", "children"),
    Output("bt-run-hint", "style"),
    Input("bt-state", "data"),
)
def _render_view(state):
    state = state or {}
    holdings = state.get("holdings", [])
    building = state.get("mode") == "building"
    if not building:
        return (dict(), HIDE, [], "", "", no_update,
                True, _RUN_DISABLED, "", HIDE)
    total_w = sum(h["weight"] for h in holdings)
    valid = bool(holdings) and abs(total_w - 100) < 0.5
    sum_color = ACCENT if abs(total_w - 100) < 0.5 else "#ffce6b"
    if holdings:
        rows = html.Div([_slider_row(h) for h in holdings],
                        style=dict(display="flex", flexDirection="column", gap="16px"))
    else:
        rows = html.Div(["No securities yet.", html.Br(), "Add one to begin."],
            style=dict(padding="18px 12px", textAlign="center", color=T4,
                fontSize="12.5px", lineHeight="1.5"))
    sum_style = dict(marginLeft="auto", fontFamily="'JetBrains Mono',monospace",
                     fontSize="12px", fontWeight="600", color=sum_color)
    if valid or not holdings:
        hint, hint_style = "", HIDE
    else:
        hint = f"Weights total {total_w:.0f}% — set them to exactly 100% to run."
        hint_style = dict(fontSize="11.5px", color="#ffce6b", marginTop="9px",
                          textAlign="center", lineHeight="1.5")
    return (HIDE, dict(), rows, f"{len(holdings)}", f"{total_w:.0f}%", sum_style,
            not valid, _RUN_ENABLED if valid else _RUN_DISABLED,
            hint, hint_style)


@app.callback(Output("bt-results", "children"),
              Input("bt-run", "n_clicks"),
              Input("bt-state", "data"))
def _render_results(_n, state):
    state = state or {}
    holdings = state.get("holdings", [])
    if state.get("mode") != "building" or not holdings:
        return _empty_results()
    # State changes (weights, period, capital, …) don't re-run the simulation —
    # the last results stay up until the Run button is pressed again.
    if ctx.triggered_id != "bt-run":
        return no_update
    total_w = sum(h["weight"] for h in holdings)
    if abs(total_w - 100) >= 0.5:
        return no_update  # button is disabled in this state; belt and braces

    capital = _parse_capital(state.get("capital", 100_000))
    bench_keys = [k for k in state.get("benchmarks", DEFAULT_BENCH)
                  if k in BENCH_OPTS]
    months, rets, benches = _monthly_returns(
        holdings, state.get("period", DEFAULT_PERIOD), bench_keys)
    eq, prets = _simulate(holdings, capital, state.get("rebalance", "Quarterly"),
                          months, rets)
    ss = _stats(prets, eq)
    if ss is None:
        return _empty_results()

    # One render bundle per selected benchmark: equity curve + stats + styling.
    brender = []
    for bk in bench_keys:
        breturns = benches.get(bk)
        if not breturns:
            continue
        beq = _equity(breturns, capital)
        bstat = _stats(breturns, beq)
        if bstat is None:
            continue
        opt = BENCH_OPTS[bk]
        brender.append(dict(key=bk, label=opt["label"], short=opt["short"],
                            color=opt["color"], eq=beq, stat=bstat))

    total_color = ACCENT if ss["total"] >= 0 else RED
    kpis = html.Div([
        _result_kpi("Final value", _money(ss["final"]),
                    _money(ss["final"] - capital, True), total_color),
        _result_kpi("Total return", _pct(ss["total"] * 100), "over period", total_color),
        _result_kpi("Annualised", _pct(ss["cagr"] * 100), "CAGR",
                    ACCENT if ss["cagr"] >= 0 else RED),
        _result_kpi("Volatility", _pct(ss["vol"] * 100), "annualised", T1),
        _result_kpi("Max drawdown", _pct(ss["maxDD"] * 100), "peak→trough", RED),
    ], style=dict(display="grid",
        gridTemplateColumns="repeat(auto-fit,minmax(150px,1fr))", gap="14px"))

    equity_card = _card([
        html.Div([
            html.Div([
                html.Div("Portfolio value", style=dict(fontSize="13px", color=T3,
                    fontWeight="500")),
                html.Div([
                    html.Div(_money(ss["final"]), style=dict(
                        fontFamily="'Space Grotesk',sans-serif", fontSize="24px",
                        fontWeight="600")),
                    html.Div(_pct(ss["total"] * 100, True), style=dict(
                        fontFamily="'JetBrains Mono',monospace", fontSize="13.5px",
                        fontWeight="600", color=total_color)),
                ], style=dict(display="flex", alignItems="baseline", gap="10px",
                              marginTop="5px")),
            ]),
            html.Div([
                html.Div([html.Span(style=dict(width="16px", height="3px",
                    borderRadius="2px", background=BLUE)),
                    html.Span("Strategy", style=dict(color="#c5cad3"))],
                    style=dict(display="flex", alignItems="center", gap="7px")),
                *[html.Div([html.Span(style=dict(width="16px", height="3px",
                    borderRadius="2px", background=b["color"])),
                    html.Span(b["label"], style=dict(color=T2))],
                    style=dict(display="flex", alignItems="center", gap="7px"))
                  for b in brender],
            ], style=dict(marginLeft="auto", display="flex", alignItems="center",
                          gap="16px", fontSize="12px", flexWrap="wrap",
                          justifyContent="flex-end")),
        ], style=dict(display="flex", alignItems="flex-start", gap="16px",
                      flexWrap="wrap")),
        html.Div(dcc.Graph(figure=_line_fig(months, eq[1:],
                    [dict(label=b["label"], color=b["color"], values=b["eq"][1:])
                     for b in brender]),
                           config={"displayModeBar": False},
                           style={"height": "300px"}),
                 style=dict(position="relative", marginTop="14px")),
    ], pad="20px 20px 14px")

    dd_card = _card([
        html.Div([
            html.Div("Drawdown", style=dict(fontSize="14px", fontWeight="600",
                fontFamily="'Space Grotesk',sans-serif")),
            html.Div(["Strategy max ", html.Span(_pct(ss["maxDD"] * 100), style=dict(
                fontFamily="'JetBrains Mono',monospace", color=RED,
                fontWeight="600"))], style=dict(marginLeft="auto",
                fontSize="12.5px", color=T4)),
        ], style=dict(display="flex", alignItems="baseline")),
        html.Div(dcc.Graph(figure=_dd_fig(months, eq,
                    [dict(label=b["label"], color=b["color"], values=b["eq"])
                     for b in brender]),
                           config={"displayModeBar": False},
                           style={"height": "150px"}),
                 style=dict(marginTop="10px")),
    ], pad="20px 20px 14px")

    bottom = html.Div([_annual_bars(months, prets), _metrics_table(ss, brender)],
        style=dict(display="grid",
            gridTemplateColumns="minmax(0,1.1fr) minmax(0,1fr)", gap="18px",
            alignItems="start"))
    return [kpis, equity_card, dd_card, bottom]


def _pick_row(key, symbol_label, name, tag, color):
    return html.Div([
        html.Span(style=dict(width="10px", height="10px", borderRadius="3px",
                             flexShrink="0", background=color)),
        html.Div(symbol_label, style=dict(fontFamily="'JetBrains Mono',monospace",
            fontSize="13px", fontWeight="600", minWidth="52px",
            whiteSpace="nowrap")),
        html.Div(name, style=dict(fontSize="13px", color="#c5cad3", flex="1",
            minWidth="0", whiteSpace="nowrap", overflow="hidden",
            textOverflow="ellipsis")),
        html.Span(tag, style=dict(fontSize="10.5px", color=T3,
            background="rgba(255,255,255,0.05)", padding="3px 8px",
            borderRadius="6px", whiteSpace="nowrap")),
    ], id={"type": "bt-pick", "index": key}, n_clicks=0, style=dict(
        display="flex", alignItems="center", gap="12px", padding="10px 12px",
        borderRadius="10px", cursor="pointer"))


def _pick_section_label(text):
    return html.Div(text, style=dict(fontSize="10px", fontWeight="700", color=T5,
        letterSpacing="0.7px", textTransform="uppercase", padding="10px 12px 4px"))


@app.callback(
    Output("bt-picker-backdrop", "style"),
    Output("bt-picker-title", "children"),
    Output("bt-picker-list", "children"),
    Output("bt-picker-results", "data"),
    Input("bt-picker", "data"),
    Input("bt-search", "value"),
    State("bt-state", "data"),
)
def _render_picker(pk, search, state):
    pk = pk or {}
    if not pk.get("open"):
        return HIDE, no_update, no_update, no_update
    query = (search or "").strip()
    search_uc = query.upper()
    held = {h["key"] for h in (state or {}).get("holdings", [])}
    mode = pk.get("mode", "add")

    cat_rows = []
    for c in CATALOG:
        if mode == "add" and c["key"] in held:
            continue
        if search_uc and search_uc not in c["key"] and search_uc not in c["name"].upper():
            continue
        cat_rows.append(_pick_row(c["key"], c["key"], c["name"], c["cls"], c["color"]))

    # Live search across everything Yahoo Finance lists, deduped against the
    # catalog (both by key and by underlying yfinance ticker) and holdings.
    live_rows = []
    live_meta = {}
    if len(query) >= 2:
        for r in _live_search(query):
            sym = (r.get("symbol") or "").upper()
            if not sym or sym in CAT or sym in _CATALOG_YF or sym in held:
                continue
            live_meta[sym] = {"name": r.get("name") or sym,
                              "yf": r.get("symbol")}
            color = PALETTE[sum(ord(ch) for ch in sym) % len(PALETTE)]
            live_rows.append(_pick_row(sym, sym, r.get("name") or sym,
                                       r.get("exchange") or "Yahoo", color))

    if cat_rows and live_rows:
        rows = ([_pick_section_label("Catalogue"), *cat_rows,
                 _pick_section_label("Search results"), *live_rows])
    else:
        rows = cat_rows + live_rows
    if not rows:
        hint = ("No securities match your search."
                if len(query) >= 2 else
                "Type at least two characters to search all listed securities.")
        rows = [html.Div(hint, style=dict(padding="28px 12px",
            textAlign="center", color=T4, fontSize="13px"))]
    show = dict(position="fixed", inset="0", zIndex="60", display="flex",
        alignItems="flex-start", justifyContent="center", paddingTop="96px")
    title = "Replace security" if mode == "replace" else "Add security"
    return show, title, rows, live_meta


def _pill_styles(options, current):
    return [dict(flex="1", border="none",
        background="rgba(255,255,255,0.08)" if o == current else "transparent",
        color=T1 if o == current else T3, fontSize="11.5px", fontWeight="600",
        padding="7px 4px", borderRadius="7px", cursor="pointer",
        fontFamily="inherit") for o in options]


@app.callback(
    [Output({"type": "bt-rebal", "index": o}, "style") for o in REBAL_OPTS],
    Input("bt-state", "data"),
)
def _rebal_styles(state):
    return _pill_styles(REBAL_OPTS, (state or {}).get("rebalance", "Quarterly"))


@app.callback(
    [Output({"type": "bt-bench", "index": k}, "style") for k in BENCH_OPTS],
    Input("bt-state", "data"),
)
def _bench_styles(state):
    active = set((state or {}).get("benchmarks", DEFAULT_BENCH))
    out = []
    for k, opt in BENCH_OPTS.items():
        on = k in active
        out.append(dict(display="flex", alignItems="center", gap="6px",
            border=f"1px solid {opt['color'] if on else 'rgba(255,255,255,0.1)'}",
            background=_hex_rgba(opt["color"], 0.14) if on else "transparent",
            color=T1 if on else T3, fontSize="11px", fontWeight="600",
            padding="5px 8px", borderRadius="7px", cursor="pointer",
            fontFamily="inherit"))
    return out


@app.callback(
    [Output({"type": "bt-period", "index": o}, "style") for o in PERIOD_OPTS],
    Output("bt-subtitle", "children"),
    Input("bt-state", "data"),
)
def _period_styles(state):
    cur = (state or {}).get("period", DEFAULT_PERIOD)
    subtitle = f"Simulate a portfolio over {PERIOD_LABEL.get(cur, PERIOD_LABEL[DEFAULT_PERIOD])} · AUD"
    return *_pill_styles(PERIOD_OPTS, cur), subtitle


# ── state mutations ───────────────────────────────────────────────────

@app.callback(Output("bt-state", "data", allow_duplicate=True),
              Input("bt-capital", "value"), State("bt-state", "data"),
              prevent_initial_call=True)
def _set_capital(val, state):
    state = dict(state or {})
    state["capital"] = _parse_capital(val)
    return state


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              [Input({"type": "bt-rebal", "index": o}, "n_clicks") for o in REBAL_OPTS],
              State("bt-state", "data"), prevent_initial_call=True)
def _set_rebal(*args):
    state = dict(args[-1] or {})
    if not ctx.triggered_id:
        return no_update
    state["rebalance"] = ctx.triggered_id["index"]
    return state


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              [Input({"type": "bt-period", "index": o}, "n_clicks") for o in PERIOD_OPTS],
              State("bt-state", "data"), prevent_initial_call=True)
def _set_period(*args):
    state = dict(args[-1] or {})
    if not ctx.triggered_id:
        return no_update
    state["period"] = ctx.triggered_id["index"]
    return state


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              [Input({"type": "bt-bench", "index": k}, "n_clicks") for k in BENCH_OPTS],
              State("bt-state", "data"), prevent_initial_call=True)
def _set_bench(*args):
    state = dict(args[-1] or {})
    tid = ctx.triggered_id
    if not tid:
        return no_update
    k = tid["index"]
    cur = list(state.get("benchmarks", DEFAULT_BENCH))
    state["benchmarks"] = [x for x in cur if x != k] if k in cur else cur + [k]
    return state


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              Input("bt-load", "n_clicks"), Input("bt-load-2", "n_clicks"),
              Input("bt-scratch", "n_clicks"), Input("bt-clear", "n_clicks"),
              Input("bt-reset", "n_clicks"),
              Input({"type": "bt-preset", "index": ALL}, "n_clicks"),
              State("bt-state", "data"), prevent_initial_call=True)
def _lifecycle(_l, _l2, _scratch, _clear, _reset, _presets, state):
    state = dict(state or {})
    tid = ctx.triggered_id
    if tid in ("bt-load", "bt-load-2"):
        state["holdings"] = _load_my_portfolio()
        state["mode"] = "building"
    elif tid == "bt-scratch":
        state["holdings"] = []
        state["mode"] = "building"
    elif tid == "bt-clear":
        state["holdings"] = []
        state["mode"] = "building"
    elif tid == "bt-reset":
        state["holdings"] = []
        state["mode"] = "empty"
    elif isinstance(tid, dict) and tid.get("type") == "bt-preset":
        p = PRESETS[tid["index"]]
        state["holdings"] = [_mk(k, w) for k, w in p["w"]]
        state["mode"] = "building"
    else:
        return no_update
    return state


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              Input({"type": "bt-remove", "index": ALL}, "n_clicks"),
              State("bt-state", "data"), prevent_initial_call=True)
def _remove(clicks, state):
    tid = ctx.triggered_id
    if not isinstance(tid, dict) or not any(clicks):
        return no_update
    state = dict(state or {})
    state["holdings"] = [h for h in state.get("holdings", [])
                         if h["key"] != tid["index"]]
    return state


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              Input({"type": "bt-weight", "index": ALL}, "value"),
              State("bt-state", "data"), prevent_initial_call=True)
def _weights(values, state):
    state = dict(state or {})
    keys = [p["id"]["index"] for p in ctx.inputs_list[0]]
    wmap = {}
    for k, v in zip(keys, values):
        try:
            wmap[k] = float(v)
        except (TypeError, ValueError):
            wmap[k] = 0.0
    new = [{**h, "weight": wmap.get(h["key"], h["weight"])}
           for h in state.get("holdings", [])]
    if new == state.get("holdings", []):
        return no_update
    state["holdings"] = new
    return state


# ── picker open / close / pick ────────────────────────────────────────

@app.callback(Output("bt-picker", "data", allow_duplicate=True),
              Output("bt-search", "value"),
              Input("bt-add", "n_clicks"),
              Input({"type": "bt-replace", "index": ALL}, "n_clicks"),
              prevent_initial_call=True)
def _open_picker(_add, _replace):
    tid = ctx.triggered_id
    if tid == "bt-add":
        return dict(open=True, mode="add", target=None), ""
    if isinstance(tid, dict) and tid.get("type") == "bt-replace":
        if not any(_replace):
            return no_update, no_update
        return dict(open=True, mode="replace", target=tid["index"]), ""
    return no_update, no_update


@app.callback(Output("bt-picker", "data", allow_duplicate=True),
              Input("bt-picker-close", "n_clicks"),
              Input("bt-picker-scrim", "n_clicks"),
              prevent_initial_call=True)
def _close_picker(_c, _s):
    return dict(open=False, mode="add", target=None)


@app.callback(Output("bt-state", "data", allow_duplicate=True),
              Output("bt-picker", "data", allow_duplicate=True),
              Input({"type": "bt-pick", "index": ALL}, "n_clicks"),
              State("bt-state", "data"), State("bt-picker", "data"),
              State("bt-picker-results", "data"),
              prevent_initial_call=True)
def _pick(clicks, state, pk, live_meta):
    tid = ctx.triggered_id
    if not isinstance(tid, dict) or not any(clicks):
        return no_update, no_update
    key = tid["index"]
    state = dict(state or {})
    holdings = list(state.get("holdings", []))
    pk = pk or {}
    if pk.get("mode") == "replace" and pk.get("target"):
        target = pk["target"]
        if any(h["key"] == key for h in holdings):
            holdings = [h for h in holdings if h["key"] != target]
        else:
            holdings = [_holding_from_key(key, h["weight"], live_meta)
                        if h["key"] == target else h for h in holdings]
    else:
        if not any(h["key"] == key for h in holdings):
            holdings = holdings + [_holding_from_key(key, 10, live_meta)]
    state["holdings"] = holdings
    state["mode"] = "building"
    return state, dict(open=False, mode="add", target=None)
