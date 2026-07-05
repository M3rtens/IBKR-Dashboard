"""Daily market summary page.

A single at-a-glance read on global markets: major equity indices, rates and
volatility, commodities, currencies and crypto. Each group is a data table with
the latest level, multi-horizon returns (1D / 1W / 1M / 3M / YTD / 1Y), the
position within the 52-week range and a one-month trend spark.

Data comes from the shared yfinance market-data layer (batched and cached
~30 min), so this page adds no new data dependency. Two batched close-series
downloads back the whole board — a 1-year series (drives every horizon except
YTD, plus the 52-week range and spark) and a year-to-date series. All figures
are close-based and delayed.

The refresh callback adapts its own interval: while instruments are still
missing data (Yahoo intermittently rate-limits the batch download, especially
during app startup when several callbacks fetch at once) it retries every few
seconds, then settles to a five-minute cadence once the board is populated —
so the page fills itself in without a manual browser refresh.
"""

import datetime

from dash import dcc, html, Input, Output, State, ALL, ctx, no_update

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5
from dashboard.formatters import pct_fmt
from dashboard.icons import _svg_b64
from services import market_data


# ── Refresh cadence (ms) ────────────────────────────────────────────────────
_INTERVAL_RETRY   = 8_000    # nothing loaded yet — retry fast
_INTERVAL_PARTIAL = 30_000   # some tickers still missing — retry soon
_INTERVAL_FULL    = 300_000  # board fully populated — normal cadence
_INTERVAL_IDLE    = 600_000  # tab not visible — near-dormant (see refresh_markets)


# ── Instrument registry ─────────────────────────────────────────────────────
# Each group is (title, [(yfinance ticker, label, kind), ...]). `kind` selects
# how the latest level is formatted for display.
_GROUPS = [
    ("Equity Indices", [
        ("^GSPC",     "S&P 500",       "index"),
        ("^IXIC",     "Nasdaq Comp",   "index"),
        ("^DJI",      "Dow Jones",     "index"),
        ("^RUT",      "Russell 2000",  "index"),
        ("^AXJO",     "ASX 200",       "index"),
        ("^AORD",     "ASX All Ords",  "index"),
        ("^FTSE",     "FTSE 100",      "index"),
        ("^GDAXI",    "DAX",           "index"),
        ("^FCHI",     "CAC 40",        "index"),
        ("^STOXX50E", "Euro Stoxx 50", "index"),
        ("^SSMI",     "SMI",           "index"),
        ("^N225",     "Nikkei 225",    "index"),
        ("^HSI",      "Hang Seng",     "index"),
        ("000001.SS", "Shanghai Comp", "index"),
        ("^KS11",     "KOSPI",         "index"),
        ("^BSESN",    "Sensex",        "index"),
        ("^GSPTSE",   "TSX Comp",      "index"),
        ("^BVSP",     "Bovespa",       "index"),
    ]),
    ("Rates & Volatility", [
        ("^IRX", "US 13W Bill",  "rate"),
        ("^FVX", "US 5Y Yield",  "rate"),
        ("^TNX", "US 10Y Yield", "rate"),
        ("^TYX", "US 30Y Yield", "rate"),
        ("^VIX", "VIX",          "level"),
        ("^VXN", "VXN (Nasdaq)", "level"),
    ]),
    ("Commodities", [
        ("GC=F", "Gold",        "usd2"),
        ("SI=F", "Silver",      "usd2"),
        ("PL=F", "Platinum",    "usd2"),
        ("PA=F", "Palladium",   "usd2"),
        ("HG=F", "Copper",      "usd2"),
        ("CL=F", "WTI Crude",   "usd2"),
        ("BZ=F", "Brent Crude", "usd2"),
        ("NG=F", "Nat Gas",     "usd2"),
        ("RB=F", "Gasoline",    "usd2"),
        ("HO=F", "Heating Oil", "usd2"),
        ("ZC=F", "Corn",        "px2"),
        ("ZW=F", "Wheat",       "px2"),
        ("ZS=F", "Soybeans",    "px2"),
        ("SB=F", "Sugar",       "px2"),
        ("KC=F", "Coffee",      "px2"),
        ("CT=F", "Cotton",      "px2"),
        ("CC=F", "Cocoa",       "px2"),
        ("LE=F", "Live Cattle", "px2"),
    ]),
    ("Currencies", [
        ("DX-Y.NYB", "US Dollar Index", "level"),
        ("AUDUSD=X", "AUD/USD",         "fx4"),
        ("EURUSD=X", "EUR/USD",         "fx4"),
        ("GBPUSD=X", "GBP/USD",         "fx4"),
        ("NZDUSD=X", "NZD/USD",         "fx4"),
        ("USDJPY=X", "USD/JPY",         "fx2"),
        ("USDCAD=X", "USD/CAD",         "fx4"),
        ("USDCHF=X", "USD/CHF",         "fx4"),
        ("USDCNY=X", "USD/CNY",         "fx4"),
        ("EURGBP=X", "EUR/GBP",         "fx4"),
        ("EURJPY=X", "EUR/JPY",         "fx2"),
        ("AUDJPY=X", "AUD/JPY",         "fx2"),
    ]),
    ("Crypto", [
        ("BTC-USD",  "Bitcoin",   "usdc"),
        ("ETH-USD",  "Ethereum",  "usdc"),
        ("BNB-USD",  "BNB",       "usd2"),
        ("SOL-USD",  "Solana",    "usd2"),
        ("XRP-USD",  "XRP",       "usd4"),
        ("ADA-USD",  "Cardano",   "usd4"),
        ("AVAX-USD", "Avalanche", "usd2"),
        ("LINK-USD", "Chainlink", "usd2"),
        ("LTC-USD",  "Litecoin",  "usd2"),
        ("DOGE-USD", "Dogecoin",  "usd4"),
    ]),
]

# Table geometry, shared by the header row and every data row.
_COLS = ("minmax(140px,1.3fr) 92px 62px 62px 62px 62px 66px 62px "
         "132px 92px")
_HEADERS = ["Instrument", "Last", "1D", "1W", "1M", "3M", "YTD", "1Y",
            "52W Range", "1M Trend"]
_ALIGNS = ["left", "right", "right", "right", "right", "right", "right",
           "right", "center", "right"]
# Sort key per column (None = not sortable). Keys match the record dict built
# in _instrument_rec; "label" sorts alphabetically, the rest numerically.
_SORT_KEYS = ["label", "last", "d1", "w", "m", "q", "ytd", "y1", None, None]


# ── Formatting ──────────────────────────────────────────────────────────────
def _fmt_level(value: float, kind: str) -> str:
    if kind in ("index", "level"):
        return f"{value:,.2f}"
    if kind == "rate":
        return f"{value:.2f}%"
    if kind == "usd2":
        return f"${value:,.2f}"
    if kind == "usd4":
        return f"${value:,.4f}"
    if kind == "usdc":
        return f"${value:,.0f}"
    if kind == "px2":
        return f"{value:,.2f}"
    if kind == "fx4":
        return f"{value:.4f}"
    if kind == "fx2":
        return f"{value:.2f}"
    return f"{value:,.2f}"


def _hex_rgba(color: str, alpha: float) -> str:
    hx = color.lstrip("#")
    if len(hx) == 6:
        r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return f"rgba(255,255,255,{alpha})"


def _chg_at(series: list, offset: int):
    """% change from ``offset`` sessions ago to the latest close, or None.

    Offsets that run past the start of the series clamp to the first close, so
    a horizon longer than the available history reports the full-history move
    rather than dropping out.
    """
    n = len(series)
    if n < 2:
        return None
    i = max(0, n - 1 - offset)
    base = series[i]
    if i == n - 1 or not base:
        return None
    return (series[-1] / base - 1) * 100


# ── Cell / spark builders ─────────────────────────────────────────────────────
def _trend_chart(series: list, color: str, h: int = 26):
    """Fixed-width one-month area spark (responsive SVG, constant 2px line)."""
    if not series or len(series) < 2:
        return html.Div(style=dict(
            width="100%", height=f"{h}px", borderRadius="5px",
            background="rgba(255,255,255,0.03)"))
    vw, vh, p = 200.0, 56.0, 3.0
    lo, hi = min(series), max(series)
    rn = hi - lo or 1
    n = len(series)
    pts = [(i / (n - 1) * vw, p + (1 - (v - lo) / rn) * (vh - 2 * p))
           for i, v in enumerate(series)]
    dl = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}"
                  for i, (x, y) in enumerate(pts))
    da = f"{dl} L{vw:.1f} {vh} L0 {vh} Z"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw:.0f} {vh:.0f}" '
        f'preserveAspectRatio="none">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity="0.24"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<path d="{da}" fill="url(#g)"/>'
        f'<path d="{dl}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" '
        f'vector-effect="non-scaling-stroke"/>'
        f'</svg>'
    )
    return html.Img(src=_svg_b64(svg), style=dict(
        display="block", width="100%", height=f"{h}px"))


def _pct_cell(v):
    if v is None:
        return html.Div("—", style=dict(
            textAlign="right", color=T5, fontSize="12px",
            fontFamily="'JetBrains Mono',monospace"))
    color = ACCENT if v > 0.03 else RED if v < -0.03 else T3
    return html.Div(pct_fmt(v, signed=True), style=dict(
        textAlign="right", color=color, fontWeight="600", fontSize="12px",
        fontFamily="'JetBrains Mono',monospace", whiteSpace="nowrap"))


def _range_cell(lo, hi, last, kind, loaded):
    """A 52-week min/max track with a marker at the latest close."""
    if not loaded or hi <= lo:
        return html.Div(style=dict(
            height="6px", borderRadius="3px", alignSelf="center",
            background="rgba(255,255,255,0.05)"))
    pos = max(0.0, min(1.0, (last - lo) / (hi - lo)))
    dot = html.Div(style=dict(
        position="absolute", left=f"{pos * 100:.0f}%", top="50%",
        transform="translate(-50%,-50%)", width="9px", height="9px",
        borderRadius="50%", background=T1,
        boxShadow=f"0 0 0 2px {BG_CARD}"))
    return html.Div(dot,
        title=f"52W range: {_fmt_level(lo, kind)} – {_fmt_level(hi, kind)}",
        style=dict(
            position="relative", height="6px", borderRadius="3px",
            alignSelf="center",
            background=("linear-gradient(90deg,rgba(255,107,107,0.45),"
                        "rgba(255,255,255,0.09),rgba(54,211,153,0.45))")))


def _table_row(label: str, kind: str, closes: list, ytd_closes: list):
    loaded = len(closes) >= 2 and closes[-2]
    if loaded:
        last = closes[-1]
        val_str = _fmt_level(last, kind)
        d1 = _chg_at(closes, 1)
        lo, hi = min(closes), max(closes)
    else:
        last, val_str, d1, lo, hi = None, "—", None, 0.0, 0.0

    w  = _chg_at(closes, 5)
    m  = _chg_at(closes, 22)
    q  = _chg_at(closes, 66)
    y1 = _chg_at(closes, 252)
    ytd = None
    if len(ytd_closes) >= 2 and ytd_closes[0]:
        ytd = (ytd_closes[-1] / ytd_closes[0] - 1) * 100

    tint = ACCENT if (d1 or 0) > 0.03 else RED if (d1 or 0) < -0.03 else T3
    spark = closes[-22:] if loaded else []

    cells = [
        html.Div(label, style=dict(
            fontSize="12.5px", fontWeight="600", color=T1 if loaded else T4,
            whiteSpace="nowrap", overflow="hidden", textOverflow="ellipsis",
            minWidth="0")),
        html.Div(val_str, style=dict(
            textAlign="right", fontFamily="'JetBrains Mono',monospace",
            fontSize="13px", fontWeight="700", color=T1 if loaded else T4,
            whiteSpace="nowrap")),
        _pct_cell(d1), _pct_cell(w), _pct_cell(m), _pct_cell(q),
        _pct_cell(ytd), _pct_cell(y1),
        _range_cell(lo, hi, last, kind, loaded),
        html.Div(_trend_chart(spark, tint), style=dict(width="92px")),
    ]
    return html.Div(cells, style=dict(
        display="grid", gridTemplateColumns=_COLS, gap="12px",
        alignItems="center", padding="9px 4px",
        borderBottom="1px solid rgba(255,255,255,0.04)"))


def _header_row(title: str, spec: dict = None):
    """Column header row. Sortable columns are clickable and show a ▲/▼ arrow on
    the active sort column; ``spec`` is this group's {"col","asc"} sort state."""
    col_key = (spec or {}).get("col")
    asc     = (spec or {}).get("asc", False)
    cells = []
    for h, a, key in zip(_HEADERS, _ALIGNS, _SORT_KEYS):
        active = key is not None and key == col_key
        style = dict(
            fontSize="9.5px", fontWeight="700",
            color=T2 if active else T5, letterSpacing="0.5px",
            textTransform="uppercase", textAlign=a)
        if key is None:
            cells.append(html.Div(h, style=style))
            continue
        style["cursor"] = "pointer"
        style["userSelect"] = "none"
        arrow = (" ▲" if asc else " ▼") if active else ""
        cells.append(html.Div(
            h + arrow, id={"type": "mkt-hdr", "group": title, "col": key},
            n_clicks=0, style=style))
    return html.Div(cells, style=dict(
        display="grid", gridTemplateColumns=_COLS, gap="12px",
        padding="0 4px 10px", borderBottom=f"1px solid {BORDER}"))


_MONO = "'JetBrains Mono',monospace"


# ── Per-group narrative ─────────────────────────────────────────────────────
# A one-line risk read tailored to each asset class, mirroring the equities
# pulse banner but rendered above every group's table. Tuple is
# (avg-move threshold %, plural noun, up message, down message, flat message).
# The threshold reflects each class's typical daily range so "broadly higher"
# only fires on a genuine move (crypto swings far more than FX or equities).
_GROUP_NARRATIVE = {
    "Equity Indices": (
        0.15, "indices",
        "Risk-on — global equities broadly higher",
        "Risk-off — global equities broadly lower",
        "Mixed — equities little changed"),
    "Rates & Volatility": (
        0.50, "instruments",
        "Yields and volatility pushing higher",
        "Yields and volatility easing lower",
        "Rates and volatility steady"),
    "Commodities": (
        0.40, "commodities",
        "Risk-on — commodities broadly bid",
        "Risk-off — commodities broadly lower",
        "Commodities mixed"),
    "Currencies": (
        0.20, "pairs",
        "Majors broadly firmer on the day",
        "Majors broadly softer on the day",
        "Currencies range-bound"),
    "Crypto": (
        1.00, "coins",
        "Risk-on — crypto broadly higher",
        "Risk-off — crypto broadly lower",
        "Crypto little changed"),
}


def _group_narrative(title: str, metrics: list):
    """Return ``(tone, msg, sub)`` — a one-line risk read for one asset class.

    ``metrics`` is the same ``[dict(label, d1)]`` list the stat strip uses.
    Falls back to generic wording for any group not in ``_GROUP_NARRATIVE``.
    """
    thr, noun, up_msg, down_msg, flat_msg = _GROUP_NARRATIVE.get(
        title, (0.20, "instruments", "Broadly higher on the day",
                "Broadly lower on the day", "Little changed on the day"))

    moves = [m["d1"] for m in metrics if m["d1"] is not None]
    n     = len(moves)
    total = len(metrics)
    if not n:
        return T3, "Waiting for market data…", ""

    up   = sum(1 for v in moves if v > 0.03)
    down = sum(1 for v in moves if v < -0.03)
    avg  = sum(moves) / n

    if avg > thr:
        tone, msg = ACCENT, up_msg
        sub = f"{up} of {total} {noun} higher · avg move {pct_fmt(avg, signed=True)}"
    elif avg < -thr:
        tone, msg = RED, down_msg
        sub = f"{down} of {total} {noun} lower · avg move {pct_fmt(avg, signed=True)}"
    else:
        tone, msg = T2, flat_msg
        sub = f"{up} of {total} {noun} higher · avg move {pct_fmt(avg, signed=True)}"
    if n < total:
        sub += f" · {total - n} still loading"
    return tone, msg, sub


def _group_summary(metrics: list) -> html.Div:
    """Compact stat strip for one asset class: average day move, advance/decline
    breadth, and the day's leader and laggard within the group.

    ``metrics`` is a list of ``dict(label, d1)`` for the group's instruments
    (``d1`` is the 1-day % move, or None if not yet loaded).
    """
    moves = [(m["label"], m["d1"]) for m in metrics if m["d1"] is not None]
    n = len(moves)

    def tile(label, body):
        return html.Div([
            html.Div(label, style=dict(fontSize="9.5px", fontWeight="700",
                color=T5, letterSpacing="0.6px", textTransform="uppercase",
                marginBottom="7px")),
            body,
        ], style=dict(flex="1", minWidth="130px"))

    panel = dict(display="flex", alignItems="flex-start", gap="14px 34px",
                 background="rgba(255,255,255,0.02)",
                 border="1px solid rgba(255,255,255,0.05)", borderRadius="12px",
                 padding="14px 18px", marginBottom="16px", flexWrap="wrap")

    if not n:
        return html.Div(tile("Summary", html.Div("Waiting for data…",
            style=dict(fontSize="12px", color=T4))), style=panel)

    up   = sum(1 for _, v in moves if v > 0.03)
    down = sum(1 for _, v in moves if v < -0.03)
    flat = n - up - down
    avg  = sum(v for _, v in moves) / n
    best = max(moves, key=lambda kv: kv[1])
    worst = min(moves, key=lambda kv: kv[1])
    avg_c = ACCENT if avg > 0.03 else RED if avg < -0.03 else T3

    avg_body = html.Div(pct_fmt(avg, signed=True), style=dict(
        fontFamily=_MONO, fontSize="20px", fontWeight="700", color=avg_c))

    seg = [html.Div(style=dict(flex=str(c), height="6px", background=col,
                               borderRadius="3px"))
           for c, col in ((up, ACCENT), (flat, "rgba(255,255,255,0.14)"),
                          (down, RED)) if c]
    breadth = html.Div([
        html.Div(seg, style=dict(display="flex", gap="3px", marginBottom="8px",
                                 minWidth="120px")),
        html.Div([
            html.Span(f"▲ {up}", style=dict(color=ACCENT)),
            html.Span(f"■ {flat}", style=dict(color=T4)),
            html.Span(f"▼ {down}", style=dict(color=RED)),
        ], style=dict(display="flex", gap="12px", fontSize="11px",
                      fontWeight="600", fontFamily=_MONO)),
    ])

    def mover(name, v):
        c = ACCENT if v >= 0 else RED
        return html.Div([
            html.Span(name, style=dict(fontSize="12.5px", color=T2,
                fontWeight="600", whiteSpace="nowrap", overflow="hidden",
                textOverflow="ellipsis", minWidth="0")),
            html.Span(pct_fmt(v, signed=True), style=dict(marginLeft="10px",
                color=c, fontFamily=_MONO, fontWeight="700", fontSize="12.5px",
                flexShrink="0")),
        ], style=dict(display="flex", alignItems="baseline",
                      justifyContent="space-between", gap="8px"))

    return html.Div([
        tile("Avg 1D move", avg_body),
        tile("Breadth", breadth),
        tile("Leader", mover(*best)),
        tile("Laggard", mover(*worst)),
    ], style=panel)


def _group_banner(title: str, metrics: list) -> html.Div:
    """Standalone narrative card for one asset class, shown above its table.

    Mirrors the top-of-page equities pulse banner — a tone-coloured one-line
    risk read plus a supporting stat line — but in its own card so each asset
    class gets its own summary above the data.
    """
    tone, msg, sub = _group_narrative(title, metrics)
    return html.Div([
        html.Div(msg, style=dict(fontSize="16px", fontWeight="600", color=tone)),
        html.Div(sub, style=dict(fontSize="12px", color=T4, marginTop="4px")),
    ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                  borderLeft=f"3px solid {tone}", borderRadius="14px",
                  padding="16px 20px", marginBottom="12px"))


def _group_table(title: str, rows: list, metrics: list,
                 spec: dict = None) -> html.Div:
    return html.Div([
        html.Div([
            html.Span(title, style=dict(
                fontSize="11px", fontWeight="700", color=T3,
                letterSpacing="1px", textTransform="uppercase")),
            html.Span(str(len(rows)), style=dict(
                fontSize="10px", fontWeight="700", color=T4,
                fontFamily="'JetBrains Mono',monospace",
                background="rgba(255,255,255,0.05)",
                padding="1px 7px", borderRadius="20px")),
        ], style=dict(display="flex", alignItems="center", gap="9px",
                      marginBottom="14px")),
        _group_summary(metrics),
        html.Div([_header_row(title, spec), *rows], style=dict(minWidth="840px")),
    ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                  borderRadius="14px", padding="18px 20px",
                  marginBottom="18px", overflowX="auto"))


def _instrument_rec(label: str, kind: str, closes: list, ytd_closes: list) -> dict:
    """Build the per-instrument record used for both sorting and row rendering:
    label, kind, the raw close series, and every sortable numeric value."""
    loaded = len(closes) >= 2 and closes[-2]
    ytd = None
    if len(ytd_closes) >= 2 and ytd_closes[0]:
        ytd = (ytd_closes[-1] / ytd_closes[0] - 1) * 100
    return dict(
        label=label, kind=kind, closes=closes, ytd_closes=ytd_closes,
        last=(closes[-1] if loaded else None),
        d1=_chg_at(closes, 1) if loaded else None,
        w=_chg_at(closes, 5), m=_chg_at(closes, 22),
        q=_chg_at(closes, 66), y1=_chg_at(closes, 252),
        ytd=ytd,
    )


def _sort_recs(recs: list, spec: dict) -> list:
    """Sort instrument records by ``spec`` {"col","asc"}. Unloaded values (None)
    always sink to the bottom regardless of direction; no spec keeps registry
    order."""
    col = (spec or {}).get("col")
    if not col:
        return recs
    asc = (spec or {}).get("asc", False)
    if col == "label":
        return sorted(recs, key=lambda r: (r.get("label") or "").upper(),
                      reverse=not asc)
    have = [r for r in recs if r.get(col) is not None]
    none = [r for r in recs if r.get(col) is None]
    have.sort(key=lambda r: r[col], reverse=not asc)
    return have + none


def _next_sort(spec: dict, col: str) -> dict:
    """Toggle logic for a header click: same column flips direction; a new
    column starts ascending for names, descending (biggest first) for numbers."""
    if spec and spec.get("col") == col:
        return {"col": col, "asc": not spec.get("asc", False)}
    return {"col": col, "asc": col == "label"}


def _build_summary(sort_state: dict = None):
    """Fetch every instrument (two batched calls) and build the page body.

    ``sort_state`` maps a group title to its {"col","asc"} sort spec; groups
    without an entry keep registry order. Returns (blocks, n_loaded, n_total)
    so the callback can decide how aggressively to retry. The 1-year series
    drives every horizon bar the YTD one; a second year-to-date series supplies
    the YTD column.
    """
    sort_state = sort_state or {}
    all_tickers = [t for _, rows in _GROUPS for t, _, _ in rows]
    closes_by_t = market_data.get_closes(all_tickers, period="1y")
    try:
        ytd_by_t = market_data.get_closes(all_tickers, period="ytd")
    except Exception:
        ytd_by_t = {}
    n_total  = len(all_tickers)
    n_loaded = sum(1 for t in all_tickers
                   if len(closes_by_t.get(t) or []) >= 2)

    blocks = []
    for title, rows in _GROUPS:
        recs = [_instrument_rec(label, kind,
                                closes_by_t.get(ticker) or [],
                                ytd_by_t.get(ticker) or [])
                for ticker, label, kind in rows]
        spec = sort_state.get(title)
        recs = _sort_recs(recs, spec)
        table_rows = [_table_row(r["label"], r["kind"], r["closes"], r["ytd_closes"])
                      for r in recs]
        metrics = [dict(label=r["label"], d1=r["d1"]) for r in recs]
        # Each asset class gets its own narrative summary card directly above
        # its data table.
        blocks.append(_group_banner(title, metrics))
        blocks.append(_group_table(title, table_rows, metrics, spec))

    return blocks, n_loaded, n_total


# ── Layout ────────────────────────────────────────────────────────────────────
def markets_page() -> html.Div:
    today = datetime.date.today().strftime("%A, %d %B %Y")
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Daily Market Summary", style=dict(
                    fontSize="19px", fontWeight="700", color=T1)),
                html.Div(today, style=dict(
                    fontSize="12px", color=T5, marginTop="3px")),
            ]),
            html.Span("Source: yfinance · close-based, delayed",
                      style=dict(marginLeft="auto", fontSize="11px", color=T5,
                                 background="rgba(255,255,255,0.04)",
                                 border=f"1px solid {BORDER}",
                                 padding="5px 10px", borderRadius="7px")),
        ], style=dict(display="flex", alignItems="center",
                      gap="14px", marginBottom="20px")),

        # Instrument tables. delay_show stops the spinner flashing over the
        # existing tables on the fast self-healing retries.
        dcc.Loading(
            id="markets-loading", type="circle", color=ACCENT,
            delay_show=600,
            children=html.Div(id="markets-body"),
        ),

        # Per-group column sort state: {group_title: {"col","asc"}}.
        dcc.Store(id="markets-sort", data={}),

        # Interval starts fast; the callback stretches it to 5 min once the
        # board is fully populated.
        dcc.Interval(id="markets-refresh", interval=_INTERVAL_RETRY,
                     n_intervals=0),
    ], style=dict(padding="26px 32px 48px", maxWidth="1560px",
                  margin="0 auto"))


# ── Callbacks ───────────────────────────────────────────────────────────────

# A column-header click updates that group's sort spec. The rebuilt headers
# reset n_clicks to 0, so ignore firings whose click value is falsy (those come
# from the periodic rebuild recreating the header components, not a real click).
@app.callback(
    Output("markets-sort", "data"),
    Input({"type": "mkt-hdr", "group": ALL, "col": ALL}, "n_clicks"),
    State("markets-sort", "data"),
    prevent_initial_call=True,
)
def _update_markets_sort(_clicks, state):
    tid = ctx.triggered_id
    trig = ctx.triggered[0] if ctx.triggered else None
    if not tid or not trig or not trig.get("value"):
        return no_update
    state = dict(state or {})
    state[tid["group"]] = _next_sort(state.get(tid["group"]), tid["col"])
    return state


@app.callback(
    Output("markets-body", "children"),
    Output("markets-refresh", "interval"),
    Input("markets-refresh", "n_intervals"),
    Input("markets-sort", "data"),
    Input("page-store", "data"),
)
def refresh_markets(_n, sort_state, page):
    # Only fetch while the Daily Market Summary tab is actually visible. The page
    # is always mounted (hidden), so without this the 64-instrument yfinance
    # batch would keep polling every ~8-30s on every tab and drag the whole app.
    # Off-tab we skip the build and idle the interval; page-store is an Input, so
    # navigating to the tab fires an immediate build regardless of the interval.
    if page != "nav-markets":
        return no_update, _INTERVAL_IDLE
    try:
        blocks, n_loaded, n_total = _build_summary(sort_state)
    except Exception as exc:
        print(f"[MARKETS] summary build failed: {type(exc).__name__}: {exc}")
        note = html.Div(
            "Market data temporarily unavailable — retrying…",
            style=dict(color=T4, fontSize="13px", padding="60px 0",
                       textAlign="center"))
        return note, _INTERVAL_RETRY

    if n_loaded == 0:
        interval = _INTERVAL_RETRY
    elif n_loaded < n_total:
        interval = _INTERVAL_PARTIAL
    else:
        interval = _INTERVAL_FULL
    return blocks, interval
