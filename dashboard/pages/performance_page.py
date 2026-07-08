"""Performance page: time-weighted returns overview, wired to live data.

Layout is built with stable element ids; a callback (driven by the global
``refresh`` interval plus the page's own range / benchmark stores) fills in the
KPIs, growth chart, monthly-returns heatmap, risk/return table, drawdown chart
and return attribution from :func:`dashboard.data.get_data`.

The growth chart reuses :func:`dashboard.charts.build_main_chart`; the requested
range is applied by pre-slicing the daily series so the shared builder needs no
changes.
"""

import datetime as _dt

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, ALL, ctx, no_update

from dashboard.app_instance import app
from dashboard.theme import (BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5,
                             _BENCH, ALLOC_HEX)
from dashboard.charts import build_main_chart, _align_bench
from dashboard.data import get_data, _bench_series

# ── palette / config ──────────────────────────────────────────────────
POS = ACCENT
NEG = RED
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

PERF_RANGES = ["1Y", "3Y", "ALL"]
PERF_RANGE_DAYS = {"1Y": 252, "3Y": 756, "ALL": 10 ** 9}
PERF_RANGE_LABEL = {"1Y": "year", "3Y": "3 years", "ALL": "full history"}
PERF_BENCH = ["asx", "sp500", "ndx"]


# ── formatters ────────────────────────────────────────────────────────

def _money(n, signed=False):
    # Signed values are performance figures (P&L, day change) — 1 dp. Unsigned
    # values are levels (growth of $X) — whole dollars.
    s = f"{abs(n):,.1f}" if signed else f"{abs(round(n)):,}"
    sign = "−" if n < 0 else ("+" if signed else "")
    return f"{sign}${s}"


def _pct(n, signed=False):
    sign = "−" if n < 0 else ("+" if signed else "")
    return f"{sign}{abs(n):.1f}%"


def _card(children, pad="20px"):
    return html.Div(children, style=dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding=pad))


def _cell_bg(r):
    cap = 0.08
    t = max(-1, min(1, r / cap))
    if abs(r) < 0.0005:
        return "rgba(255,255,255,0.03)"
    if t >= 0:
        return f"rgba(54,211,153,{0.12 + 0.5 * t:.3f})"
    return f"rgba(255,107,107,{0.12 + 0.5 * abs(t):.3f})"


def _cell_fg(r):
    if abs(r) > 0.045:
        return "#f2f4f7"
    return "#a9edd0" if r >= 0 else "#ffc2c2"


# ── stats on a monthly-returns list ───────────────────────────────────

def _monthly(dates, values):
    """Resample a daily (dates, values) series to month-end % returns.

    Returns a pandas Series indexed by month-end Timestamp, or None.
    """
    if not values or len(values) < 2:
        return None
    try:
        s = pd.Series(list(values), index=pd.to_datetime(list(dates)))
        m = s.resample("ME").last().dropna()
        r = m.pct_change().dropna()
        return r if len(r) else None
    except Exception:
        return None


def _stats(r):
    """Risk/return stats from a list of monthly returns."""
    n = len(r)
    if n < 2:
        return None
    eq = [1.0]
    for x in r:
        eq.append(eq[-1] * (1 + x))
    years = n / 12
    total = eq[-1] - 1
    ann = (1 + total) ** (1 / years) - 1 if years else total
    mean = sum(r) / n
    sd = (sum((x - mean) ** 2 for x in r) / n) ** 0.5
    vol = sd * (12 ** 0.5)
    neg = [x for x in r if x < 0]
    dsd = (sum(x * x for x in neg) / len(neg)) ** 0.5 if neg else 0
    rf = 0.04
    sharpe = (ann - rf) / vol if vol else 0
    sortino = (ann - rf) / (dsd * (12 ** 0.5)) if dsd else 0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    return dict(ann=ann, total=total, vol=vol, sharpe=sharpe, sortino=sortino,
                maxDD=mdd, best=max(r), worst=min(r),
                posPct=sum(1 for x in r if x > 0) / n)


# ── static layout builders ────────────────────────────────────────────

def _range_btns():
    btns = []
    for r in PERF_RANGES:
        btns.append(html.Button(r, id={"type": "perf-range", "index": r},
            n_clicks=0, style=dict(border="none", background="transparent",
                color=T3, fontFamily="'JetBrains Mono',monospace", fontSize="12px",
                fontWeight="600", padding="5px 10px", borderRadius="7px",
                cursor="pointer")))
    return html.Div(btns, style=dict(display="flex", gap="2px",
                    background="rgba(255,255,255,0.04)", borderRadius="9px",
                    padding="3px"))


def _bench_btns():
    out = [html.Span("vs", style=dict(fontSize="11px", color=T4, fontWeight="600",
                                      letterSpacing="0.5px", textTransform="uppercase"))]
    for k in PERF_BENCH:
        bm = _BENCH[k]
        out.append(html.Button([
            html.Span(style=dict(width="13px", height="3px", borderRadius="2px",
                                 background=bm["color"], display="inline-block")),
            " " + bm["label"],
        ], id={"type": "perf-bench", "index": k}, n_clicks=0,
            style=dict(display="flex", alignItems="center", gap="6px",
                border="1px solid rgba(255,255,255,0.07)", background="transparent",
                color=T2, fontSize="12px", fontWeight="500", padding="5px 9px",
                borderRadius="8px", cursor="pointer", fontFamily="inherit")))
    return html.Div(out, style=dict(display="flex", alignItems="center", gap="7px"))


def _growth_card():
    return _card([
        html.Div([
            html.Div([
                html.Div(id="perf-growth-label", children="Growth",
                         style=dict(fontSize="13px", color=T3, fontWeight="500")),
                html.Div([
                    html.Div(id="perf-growth-val", style=dict(
                        fontFamily="'Space Grotesk',sans-serif",
                        fontSize="24px", fontWeight="600")),
                    html.Div(id="perf-growth-ret", style=dict(
                        fontFamily="'JetBrains Mono',monospace", fontSize="13.5px",
                        fontWeight="600")),
                    html.Div(id="perf-growth-period",
                             style=dict(fontSize="12.5px", color=T4)),
                ], style=dict(display="flex", alignItems="baseline", gap="10px",
                              marginTop="5px")),
                html.Div(id="perf-subtitle",
                         style=dict(fontSize="12px", color=T4, marginTop="7px")),
            ]),
            html.Div([
                html.Div([
                    html.Span("Today", style=dict(fontSize="10px", color=T4,
                        fontWeight="700", letterSpacing="0.6px",
                        textTransform="uppercase")),
                    html.Span(id="perf-day-change", style=dict(
                        fontFamily="'JetBrains Mono',monospace", fontSize="13px",
                        fontWeight="600", color=T2)),
                ], style=dict(display="flex", alignItems="center", gap="9px",
                              padding="7px 12px", borderRadius="9px",
                              border="1px solid rgba(255,255,255,0.08)")),
                html.Div([_bench_btns(), _range_btns()],
                         style=dict(display="flex", alignItems="center",
                                    gap="14px", flexWrap="wrap",
                                    justifyContent="flex-end")),
            ], style=dict(marginLeft="auto", display="flex",
                          flexDirection="column", alignItems="flex-end",
                          gap="12px")),
        ], style=dict(display="flex", alignItems="flex-start", gap="16px",
                      flexWrap="wrap")),
        html.Div(dcc.Graph(id="perf-growth-chart", config={"displayModeBar": False},
                           style={"height": "280px"}),
                 style=dict(position="relative", marginTop="14px")),
    ], pad="20px 20px 14px")


def _loading(text="Loading…"):
    return html.Div(text, style=dict(color=T4, fontSize="12.5px", padding="20px 0"))


# ── data-driven section builders ──────────────────────────────────────

def _kpi_card(k):
    return _card([
        html.Div([
            html.Div(k["label"], style=dict(fontSize="12px", color=T3, fontWeight="500")),
            html.Span(k["tag"], style=dict(fontSize="9.5px", fontWeight="600",
                letterSpacing="0.3px", color=k["tagColor"], background=k["tagBg"],
                padding="2px 6px", borderRadius="5px")),
        ], style=dict(display="flex", alignItems="center", gap="6px")),
        html.Div(k["value"], style=dict(fontFamily="'Space Grotesk',sans-serif",
            fontSize="27px", fontWeight="600", letterSpacing="0.2px", lineHeight="1",
            marginTop="10px", color=k["color"])),
        html.Div([
            html.Span(k["sub"], style=dict(color=k["subColor"], fontWeight="600",
                fontFamily="'JetBrains Mono',monospace")),
            html.Span(k["subNote"], style=dict(color=T4)),
        ], style=dict(display="flex", alignItems="center", gap="6px",
                      marginTop="11px", fontSize="12.5px")),
    ], pad="18px")


def _heatmap(pm, bench_months=None):
    """Monthly-returns grid. When ``bench_months`` (a list of
    ``dict(short, color, pm)``) is given, each year shows the portfolio row
    followed by one dimmer row per selected benchmark.
    """
    bench_months = [b for b in (bench_months or []) if b.get("pm") is not None]

    grid = [html.Div()]
    for mc in MON:
        grid.append(html.Div(mc, style=dict(fontSize="10px", color=T4,
            textAlign="center", fontWeight="600", letterSpacing="0.3px")))
    grid.append(html.Div("YR", style=dict(fontSize="10px", color="#8a909e",
        textAlign="center", fontWeight="600", letterSpacing="0.3px")))

    def year_map(series):
        by = {}
        for ts, r in series.items():
            by.setdefault(ts.year, {})[ts.month - 1] = float(r)
        return by

    def month_cells(ymap, y, dim=False):
        cells = []
        yprod, has = 1.0, False
        for mo in range(12):
            if mo in ymap.get(y, {}):
                r = ymap[y][mo]
                yprod *= 1 + r
                has = True
                txt = ("" if r >= 0 else "−") + f"{abs(r * 100):.1f}"
                cells.append(html.Div(txt, style=dict(height="34px",
                    borderRadius="6px", display="flex", alignItems="center",
                    justifyContent="center", background=_cell_bg(r),
                    fontFamily="'JetBrains Mono',monospace", fontSize="10.5px",
                    fontWeight="600", color=_cell_fg(r),
                    opacity="0.68" if dim else "1")))
            else:
                cells.append(html.Div("·", style=dict(height="34px",
                    borderRadius="6px", display="flex", alignItems="center",
                    justifyContent="center", background="transparent",
                    color="#3a3f49", fontFamily="'JetBrains Mono',monospace",
                    fontSize="10.5px")))
        return cells, yprod, has

    def yr_cell(yprod, has, dim=False):
        yr = (yprod - 1) * 100
        return html.Div(
            (("+" if yr >= 0 else "−") + f"{abs(yr):.1f}") if has else "—",
            style=dict(height="34px", borderRadius="6px", display="flex",
                alignItems="center", justifyContent="center",
                background=_cell_bg((yprod - 1) / 3) if has else "transparent",
                fontFamily="'JetBrains Mono',monospace", fontSize="11px",
                fontWeight="700", opacity="0.68" if dim else "1",
                color=("#a9edd0" if yr >= 0 else "#ffc2c2") if has else "#3a3f49"))

    port_by = year_map(pm)
    bench_by = [(b, year_map(b["pm"])) for b in bench_months]

    years = sorted(port_by)
    for i, y in enumerate(years):
        # portfolio row
        grid.append(html.Div(str(y), style=dict(
            fontFamily="'JetBrains Mono',monospace", fontSize="11px",
            color="#8a909e", fontWeight="600")))
        cells, yprod, has = month_cells(port_by, y)
        grid.extend(cells)
        grid.append(yr_cell(yprod, has))
        # one row per selected benchmark, labelled and dimmed
        for b, bmap in bench_by:
            grid.append(html.Div(b["short"], style=dict(
                fontFamily="'JetBrains Mono',monospace", fontSize="9px",
                color=b["color"], fontWeight="600", display="flex",
                alignItems="center")))
            bcells, byprod, bhas = month_cells(bmap, y, dim=True)
            grid.extend(bcells)
            grid.append(yr_cell(byprod, bhas, dim=True))
        # breathing room between grouped years
        if bench_by and i < len(years) - 1:
            grid.append(html.Div(style=dict(gridColumn="1 / -1", height="6px")))

    header = html.Div([
        html.Div("Monthly Returns", style=dict(fontSize="14px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif")),
        html.Div([
            html.Span("−8%"),
            html.Span(style=dict(width="70px", height="8px", borderRadius="3px",
                background="linear-gradient(90deg,#ff6b6b,rgba(255,255,255,0.06),#36d399)")),
            html.Span("+8%"),
        ], style=dict(marginLeft="auto", display="flex", alignItems="center",
                      gap="8px", fontSize="11px", color=T4)),
    ], style=dict(display="flex", alignItems="center", marginBottom="16px"))
    return [header, html.Div(grid, style=dict(display="grid",
        gridTemplateColumns="38px repeat(12,1fr) 52px", gap="4px",
        alignItems="center"))]


def _risk_table(ps, benches):
    """Risk/return table with one column per selected benchmark.

    ``benches`` is a list of ``dict(name, short, color, stats)`` ordered as the
    user selected them. The first benchmark is the primary one: portfolio values
    are coloured by out/underperformance against it, matching the KPI cards.
    """
    def col(v):
        return POS if v >= 0 else NEG

    primary = benches[0]["stats"] if benches else None

    def diff_color(key):
        return col(ps[key] - primary[key]) if primary is not None else T1

    p = lambda v: _pct(v * 100)
    ps_ = lambda v: _pct(v * 100, True)
    # each metric carries a formatter that maps a stats dict to its cell text,
    # applied to the portfolio and to every benchmark column.
    rows = [
        dict(label="Annualised Return", fmt=lambda s: p(s["ann"]), pColor=diff_color("ann")),
        dict(label="Cumulative Return", fmt=lambda s: p(s["total"]), pColor=diff_color("total")),
        dict(label="Volatility (Ann)", fmt=lambda s: p(s["vol"]), pColor=T1),
        dict(label="Sharpe Ratio", fmt=lambda s: f"{s['sharpe']:.2f}", pColor=diff_color("sharpe")),
        dict(label="Sortino Ratio", fmt=lambda s: f"{s['sortino']:.2f}", pColor=diff_color("sortino")),
        dict(label="Max Drawdown", fmt=lambda s: p(s["maxDD"]), pColor=diff_color("maxDD")),
        dict(label="Best Month", fmt=lambda s: ps_(s["best"]), pColor=POS),
        dict(label="Worst Month", fmt=lambda s: ps_(s["worst"]), pColor=NEG),
        dict(label="Positive Months", fmt=lambda s: f"{s['posPct'] * 100:.0f}%", pColor=T1),
    ]

    def bench_cell(b, m):
        return "—" if b["stats"] is None else m["fmt"](b["stats"])

    # label column + portfolio + one column per benchmark
    grid_cols = "1fr auto" + " auto" * len(benches)
    cell_w = "78px" if len(benches) <= 1 else "68px"

    head = html.Div([
        html.Div("Risk & Return", style=dict(fontSize="14px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif")),
        html.Div("vs " + " · ".join(b["name"] for b in benches),
            style=dict(marginLeft="auto", fontSize="11px", color=T4,
                       fontFamily="'JetBrains Mono',monospace")),
    ], style=dict(display="flex", alignItems="baseline", marginBottom="6px", gap="10px"))
    col_head = html.Div([
        html.Div("Metric"),
        html.Div("Portfolio", style=dict(textAlign="right", width=cell_w)),
        *[html.Div(b["short"], style=dict(textAlign="right", width=cell_w,
            color=b["color"])) for b in benches],
    ], style=dict(display="grid", gridTemplateColumns=grid_cols,
        fontSize="11px", color=T4, fontWeight="600", letterSpacing="0.4px",
        textTransform="uppercase", padding="10px 0 8px",
        borderBottom="1px solid rgba(255,255,255,0.06)"))
    body = [html.Div([
        html.Div(m["label"], style=dict(fontSize="13px", color="#c5cad3")),
        html.Div(m["fmt"](ps), style=dict(textAlign="right", width=cell_w,
            fontFamily="'JetBrains Mono',monospace", fontSize="13px",
            fontWeight="600", color=m["pColor"])),
        *[html.Div(bench_cell(b, m), style=dict(textAlign="right", width=cell_w,
            fontFamily="'JetBrains Mono',monospace", fontSize="13px", color=T2))
          for b in benches],
    ], style=dict(display="grid", gridTemplateColumns=grid_cols,
        alignItems="center", padding="11px 0",
        borderBottom="1px solid rgba(255,255,255,0.04)")) for m in rows]
    return [head, col_head, *body]


def _attribution(holdings):
    by_cls = {}
    for h in holdings:
        if h.get("ticker") == "CASH":
            continue
        cls = h.get("cls") or "Other"
        contrib = (h.get("weight") or 0) * (h.get("ret") or 0) * 100
        by_cls[cls] = by_cls.get(cls, 0.0) + contrib
    if not by_cls:
        return [_loading("No attribution data.")]
    items = sorted(by_cls.items(), key=lambda kv: abs(kv[1]), reverse=True)
    max_c = max(abs(c) for _, c in items) or 1
    rows = []
    for name, contrib in items:
        color = ALLOC_HEX.get(name, "#60a5fa")
        rows.append(html.Div([
            html.Div([
                html.Span(style=dict(width="9px", height="9px", borderRadius="3px",
                                     flexShrink="0", background=color)),
                html.Span(name, style=dict(fontSize="13px", color="#c5cad3")),
                html.Span(_pct(contrib, True) + "pt", style=dict(marginLeft="auto",
                    fontFamily="'JetBrains Mono',monospace", fontSize="13px",
                    fontWeight="600", color=color)),
            ], style=dict(display="flex", alignItems="center", gap="9px",
                          marginBottom="7px")),
            html.Div(html.Div(style=dict(height="100%", borderRadius="4px",
                width=f"{abs(contrib) / max_c * 100:.0f}%", background=color)),
                style=dict(height="7px", borderRadius="4px",
                    background="rgba(255,255,255,0.05)", overflow="hidden")),
        ]))
    return [
        html.Div("Return Attribution", style=dict(fontSize="14px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif", marginBottom="4px")),
        html.Div("Contribution to total return by asset class", style=dict(
            fontSize="12px", color=T4, marginBottom="16px")),
        html.Div(rows, style=dict(display="flex", flexDirection="column", gap="15px")),
    ]


def _dd_fig(dates, values, bench_data=None, active_bench=None):
    def _compute_dd(vals):
        peak = float("-inf")
        dd = []
        for v in vals:
            peak = max(peak, v)
            dd.append((v / peak - 1) * 100 if peak else 0)
        return dd

    portfolio_dd = _compute_dd(values)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(dates), y=portfolio_dd, mode="lines", name="Portfolio",
        line=dict(color=NEG, width=1.6), fill="tozeroy",
        fillcolor="rgba(255,107,107,0.18)",
        hovertemplate="<b>Portfolio: %{y:.1f}%</b><br>%{x|%b %Y}<extra></extra>"))

    for key in (active_bench or []):
        bm = _BENCH.get(key)
        if not bm:
            continue
        bd = bench_data and bench_data.get(key)
        raw_bench = None
        if bd and bd.get("dates") and bd.get("values"):
            aligned = _align_bench(bd["dates"], bd["values"], dates)
            if aligned and aligned[0]:
                raw_bench = aligned
        if raw_bench is None:
            raw_bench = _bench_series(bm["seed"], bm["drift"], bm["vol"], bm["shock"])
            n = len(dates)
            raw_bench = raw_bench[-n:]

        bench_dd = _compute_dd(raw_bench)
        fig.add_trace(go.Scatter(
            x=list(dates), y=bench_dd, mode="lines", name=bm["label"],
            line=dict(color=bm["color"], width=1.2, dash="dot"),
            hovertemplate=f"<b>{bm['label']}: %{{y:.1f}}%</b><br>%{{x|%b %Y}}<extra></extra>"))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=52, t=6, b=24), showlegend=False, hovermode="x",
        xaxis=dict(showgrid=False, zeroline=False, showline=False,
                   tickfont=dict(family="JetBrains Mono, monospace", color=T5, size=10),
                   tickformat="%b '%y"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", zeroline=False,
                   side="right", nticks=3, ticksuffix="%",
                   tickfont=dict(family="JetBrains Mono, monospace", color=T5, size=10)),
        font=dict(family="JetBrains Mono, monospace", color=T3))
    return fig


# ── page ──────────────────────────────────────────────────────────────

def performance_page():
    content = html.Div([
        html.Div(id="perf-kpis", children=_loading(), style=dict(
            display="grid", gridTemplateColumns="repeat(auto-fit,minmax(210px,1fr))",
            gap="14px")),
        _growth_card(),
        html.Div([
            html.Div([
                _card([
                    html.Div([
                        html.Div("Drawdown", style=dict(fontSize="14px", fontWeight="600",
                            fontFamily="'Space Grotesk',sans-serif")),
                        html.Div(id="perf-dd-max", style=dict(marginLeft="auto",
                            fontSize="12.5px", color=T4)),
                    ], style=dict(display="flex", alignItems="baseline")),
                    html.Div(dcc.Graph(id="perf-dd-chart",
                                       config={"displayModeBar": False},
                                       style={"height": "150px"}),
                             style=dict(marginTop="10px")),
                ], pad="20px 20px 14px"),
                _card(html.Div(id="perf-heatmap", children=_loading())),
            ], style=dict(display="flex", flexDirection="column", gap="18px")),
            html.Div([
                _card(html.Div(id="perf-risk", children=_loading())),
                _card(html.Div(id="perf-attribution", children=_loading())),
            ], style=dict(display="flex", flexDirection="column", gap="18px")),
        ], style=dict(display="grid",
                      gridTemplateColumns="minmax(0,1.55fr) minmax(0,1fr)",
                      gap="18px", alignItems="start")),
    ], style=dict(padding="24px 28px 40px", display="flex",
                  flexDirection="column", gap="18px"))

    return html.Div([
        dcc.Store(id="perf-range-store", data="ALL"),
        dcc.Store(id="perf-bench-store", data=["asx"]),
        content,
    ], style=dict(display="flex", flexDirection="column"))


# ── control callbacks ─────────────────────────────────────────────────

@app.callback(
    Output("perf-range-store", "data"),
    [Input({"type": "perf-range", "index": r}, "n_clicks") for r in PERF_RANGES],
    prevent_initial_call=True,
)
def _set_perf_range(*_):
    if not ctx.triggered_id:
        return no_update
    return ctx.triggered_id["index"]


@app.callback(
    Output("perf-bench-store", "data"),
    [Input({"type": "perf-bench", "index": k}, "n_clicks") for k in PERF_BENCH],
    State("perf-bench-store", "data"),
    prevent_initial_call=True,
)
def _toggle_perf_bench(*args):
    *_, cur = args
    if not ctx.triggered_id:
        return no_update
    k = ctx.triggered_id["index"]
    cur = list(cur or [])
    return [x for x in cur if x != k] if k in cur else cur + [k]


# ── main populate callback ────────────────────────────────────────────

@app.callback(
    Output("perf-day-change", "children"),
    Output("perf-day-change", "style"),
    Output("perf-subtitle", "children"),
    Output("perf-kpis", "children"),
    Output("perf-growth-chart", "figure"),
    Output("perf-growth-label", "children"),
    Output("perf-growth-val", "children"),
    Output("perf-growth-ret", "children"),
    Output("perf-growth-ret", "style"),
    Output("perf-growth-period", "children"),
    Output("perf-heatmap", "children"),
    Output("perf-risk", "children"),
    Output("perf-dd-chart", "figure"),
    Output("perf-dd-max", "children"),
    Output("perf-attribution", "children"),
    *[Output({"type": "perf-range", "index": r}, "style") for r in PERF_RANGES],
    *[Output({"type": "perf-bench", "index": k}, "style") for k in PERF_BENCH],
    Input("refresh", "n_intervals"),
    Input("perf-range-store", "data"),
    Input("perf-bench-store", "data"),
)
def _populate_performance(_n, range_key, active_bench):
    range_key = range_key if range_key in PERF_RANGES else "ALL"
    active_bench = active_bench or ["asx"]

    # range button styles
    range_styles = [dict(border="none",
        background="rgba(255,255,255,0.08)" if r == range_key else "transparent",
        color=T1 if r == range_key else T3,
        fontFamily="'JetBrains Mono',monospace", fontSize="12px", fontWeight="600",
        padding="5px 10px", borderRadius="7px", cursor="pointer")
        for r in PERF_RANGES]
    # bench button styles
    bench_styles = []
    for k in PERF_BENCH:
        on = k in active_bench
        bench_styles.append(dict(display="flex", alignItems="center", gap="6px",
            border=f"1px solid {'rgba(255,255,255,0.13)' if on else 'rgba(255,255,255,0.07)'}",
            background="rgba(255,255,255,0.06)" if on else "transparent",
            color=T1 if on else T2, fontSize="12px", fontWeight="500",
            padding="5px 9px", borderRadius="8px", cursor="pointer",
            fontFamily="inherit"))

    d = get_data()
    series = d.port_series_time or d.port_series
    dates = d.port_dates_time or d.port_dates

    # today's portfolio move (dollar + %), sourced from the holdings snapshot
    day_dollar = getattr(d, "day_dollar", 0.0) or 0.0
    day_pct = getattr(d, "day_pct", 0.0) or 0.0
    day_txt = f"{_money(day_dollar, True)} · {_pct(day_pct, True)}"
    day_style = dict(fontFamily="'JetBrains Mono',monospace", fontSize="13px",
                     fontWeight="600", color=POS if day_dollar >= 0 else NEG)

    ret_style = dict(fontFamily="'JetBrains Mono',monospace", fontSize="13.5px",
                     fontWeight="600")

    if not series:
        empty = go.Figure()
        empty.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=0, r=0, t=0, b=0),
                            xaxis=dict(visible=False), yaxis=dict(visible=False))
        loading = _loading("Fetching portfolio history…")
        return (day_txt, day_style, "Time-weighted returns · AUD",
                loading, empty, "Growth", "—", "", ret_style, "",
                loading, loading, empty, "", loading,
                *range_styles, *bench_styles)

    # -- growth chart: pre-slice to the requested range, reuse build_main_chart.
    # Money basis: plot the raw NAV series (deposits/withdrawals included) so
    # the portfolio line is on the same footing as the flow-simulated
    # benchmarks; the TWR series keeps feeding the stats below.
    m_series = d.port_series or series
    m_dates = d.port_dates or dates
    n_days = min(PERF_RANGE_DAYS[range_key], len(m_series))
    sl = m_series[-n_days:]
    sl_dates = list(m_dates[-n_days:]) if m_dates and len(m_dates) == len(m_series) else None
    fig = build_main_chart(sl, "ALL", active_bench, sl_dates, d.bench_data,
                           mode="value", return_basis="money",
                           cash_flows=d.cash_flows)
    start_v = sl[0] if sl[0] else 1
    end_v = sl[-1]
    range_ret = (end_v / start_v - 1) * 100
    ret_style = {**ret_style, "color": POS if range_ret >= 0 else NEG}

    # -- monthly returns (full history) for KPIs / heatmap / risk
    pm = _monthly(dates, series)
    ps = _stats(list(pm.values)) if pm is not None else None

    # stats for every selected benchmark; the first is primary (drives the KPI
    # comparisons and the risk-table portfolio colouring).
    benches = []
    for k in active_bench:
        bpm, bstats = None, None
        if d.bench_data and d.bench_data.get(k):
            bd = d.bench_data[k]
            bpm = _monthly(bd.get("dates"), bd.get("values"))
            bstats = _stats(list(bpm.values)) if bpm is not None else None
        benches.append(dict(name=_BENCH[k]["label"], short=k.upper(),
                            color=_BENCH[k]["color"], stats=bstats, pm=bpm))
    bench_name = benches[0]["name"]
    bs = benches[0]["stats"]

    tag_c, tag_bg = "#8a909e", "rgba(255,255,255,0.05)"
    if ps is not None:
        def col(v):
            return POS if v >= 0 else NEG
        d_ann = (ps["ann"] - bs["ann"]) if bs else ps["ann"]
        kpis = [_kpi_card(k) for k in [
            dict(label="Annualised Return", tag="TWR", tagColor=tag_c, tagBg=tag_bg,
                 value=_pct(ps["ann"] * 100), color=POS,
                 sub=_pct(d_ann * 100, True), subColor=col(d_ann),
                 subNote=f"vs {bench_name}" if bs else "annualised"),
            dict(label="Total Return", tag="SINCE INCEPTION", tagColor=tag_c, tagBg=tag_bg,
                 value=_pct(ps["total"] * 100), color=col(ps["total"]),
                 sub=_money(start_v * ps["total"], True), subColor=col(ps["total"]),
                 subNote=f"on {_money(start_v)}"),
            dict(label="Sharpe Ratio", tag="RF 4%", tagColor=tag_c, tagBg=tag_bg,
                 value=f"{ps['sharpe']:.2f}", color=T1,
                 sub=("—" if bs is None else f"{bs['sharpe']:.2f}"), subColor=T2,
                 subNote=bench_name),
            dict(label="Volatility", tag="ANN", tagColor=tag_c, tagBg=tag_bg,
                 value=_pct(ps["vol"] * 100), color=T1,
                 sub=("—" if bs is None else _pct(bs["vol"] * 100)), subColor=T2,
                 subNote=bench_name),
            dict(label="Max Drawdown", tag="PEAK→TROUGH", tagColor="#ffce6b",
                 tagBg="rgba(255,206,107,0.13)", value=_pct(ps["maxDD"] * 100),
                 color=NEG, sub=("—" if bs is None else _pct(bs["maxDD"] * 100)),
                 subColor=T2, subNote=bench_name),
        ]]
        heat = _heatmap(pm, benches)
        risk = _risk_table(ps, benches)
        dd_max = ["Max ", html.Span(_pct(ps["maxDD"] * 100), style=dict(
            fontFamily="'JetBrains Mono',monospace", color=NEG, fontWeight="600"))]
    else:
        kpis = _loading()
        heat = _loading()
        risk = _loading()
        dd_max = ""

    attribution = _attribution(d.holdings)
    # Drawdown stays on the TWR series: a deposit would otherwise refill the
    # peak and hide real drawdowns.
    tw_n = min(PERF_RANGE_DAYS[range_key], len(series))
    tw_sl = series[-tw_n:]
    tw_sl_dates = list(dates[-tw_n:]) if dates and len(dates) == len(series) else None
    dd_fig = _dd_fig(tw_sl_dates or list(range(len(tw_sl))), tw_sl,
                     bench_data=d.bench_data, active_bench=active_bench)

    inception = ""
    if dates:
        d0 = dates[0]
        try:
            d0 = d0 if isinstance(d0, _dt.date) else _dt.date.fromisoformat(str(d0)[:10])
            inception = d0.strftime("%b %Y")
        except Exception:
            inception = ""
    subtitle = "Time-weighted returns · AUD" + (f" · since {inception}" if inception else "")

    return (day_txt, day_style, subtitle, kpis, fig,
            f"Growth of {_money(start_v)}",
            _money(end_v), _pct(range_ret, True), ret_style,
            f"past {PERF_RANGE_LABEL[range_key]}",
            heat, risk, dd_fig, dd_max, attribution,
            *range_styles, *bench_styles)
