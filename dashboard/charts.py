"""Plotly figure builders and the SVG sparkline helper."""

import datetime

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

from dashboard.theme import (ACCENT, RED, T1, T2, T3, T4, T5,
                             ALLOC_HEX, _RANGES, _BENCH)
from dashboard.formatters import money
from dashboard.icons import _svg_b64
from dashboard.data import _bench_series


# --- SVG sparkline (rendered as base64 data-URI img) -----------------

def spark(series, color, w=84, h=30, p=2):
    if not series or len(series) < 2:
        return html.Span(style={"display":"block","width":f"{w}px","height":f"{h}px"})
    lo, hi = min(series), max(series); rn = hi-lo or 1; n = len(series)
    pts = [(p + i/(n-1)*(w-2*p), p + (1-(v-lo)/rn)*(h-2*p)) for i,v in enumerate(series)]
    dl = " ".join(f"{'M' if i==0 else 'L'}{x:.1f} {y:.1f}" for i,(x,y) in enumerate(pts))
    da = f"{dl} L{pts[-1][0]:.1f} {h-p} L{pts[0][0]:.1f} {h-p} Z"
    # parse hex color for rgba fill
    hx = color.lstrip("#")
    if len(hx) == 6:
        r2,g2,b2 = int(hx[0:2],16),int(hx[2:4],16),int(hx[4:6],16)
        fill_color = f"rgba({r2},{g2},{b2},0.18)"
    else:
        fill_color = "rgba(255,255,255,0.1)"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
        f'<path d="{da}" fill="{fill_color}"/>'
        f'<path d="{dl}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )
    return html.Img(src=_svg_b64(svg),
                    style={"display":"block","width":f"{w}px","height":f"{h}px"})


# --- Plotly charts ---------------------------------------------------

def _align_bench(bench_dates, bench_values, chart_dates):
    """Align a benchmark price series to the chart's date axis using ffill.

    Returns a list of floats (same length as chart_dates), or None if there is
    insufficient overlap to produce a meaningful series.
    """
    s = pd.Series(bench_values, index=pd.to_datetime(bench_dates)).sort_index()
    idx = pd.to_datetime(chart_dates)
    aligned = s.reindex(s.index.union(idx)).sort_index().ffill().reindex(idx)
    if aligned.isna().all():
        return None
    aligned = aligned.ffill().bfill()
    return aligned.tolist()


def _loading_figure():
    fig = go.Figure()
    fig.add_annotation(
        text="Fetching portfolio history...",
        x=0.5, y=0.5, xref="paper", yref="paper",
        showarrow=False,
        font=dict(family="JetBrains Mono, monospace", color=T3, size=12),
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0,r=52,t=4,b=28),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig


def build_main_chart(port_series, range_key="1Y", active_bench=("asx",), port_dates=None, bench_data=None, mode="value", return_basis="money", cash_flows=None):
    if not port_series:
        return _loading_figure()
    N = len(port_series)
    n = min(dict(_RANGES).get(range_key, 260), N)
    start = N - n
    sl = port_series[start:]

    if port_dates and len(port_dates) == N:
        dates = list(port_dates[start:])
    else:
        today = datetime.date.today()
        dates = [today - datetime.timedelta(days=n-1-i) for i in range(n)]

    pct_mode = mode == "pct"
    time_basis = return_basis == "time"
    base = sl[0] if sl[0] else 1

    # Build cash flows dict for money-weighted benchmark simulation
    mw_flows = {}
    if not time_basis and cash_flows is not None:
        import datetime as _dt
        try:
            if hasattr(cash_flows, "empty") and not cash_flows.empty and "datetime" in cash_flows.columns and "amount" in cash_flows.columns:
                for _, row in cash_flows.iterrows():
                    dt = row.get("datetime")
                    if pd.isna(dt):
                        continue
                    flow_date = dt.date() if hasattr(dt, "date") else _dt.date.fromisoformat(str(dt)[:10])
                    mw_flows[flow_date] = mw_flows.get(flow_date, 0.0) + float(row.get("amount") or 0.0)
        except Exception:
            mw_flows = {}

    # IBKR T+1 quirk: initial deposits are sometimes dated to the next trading
    # day in CashTransactions even though sl[0] already reflects them (equity
    # summary uses trade-date accounting). When showing full history (start == 0),
    # absorb any dates[1] flow into dates[0] so the simulation loop — which
    # starts at i=1 — does not double-apply the opening deposit and inflate
    # the benchmark. Uses a ≤5-day window to cover Australian long weekends.
    if start == 0 and len(dates) >= 2 and mw_flows:
        try:
            _d0 = dates[0] if isinstance(dates[0], datetime.date) else datetime.date.fromisoformat(str(dates[0])[:10])
            _d1 = dates[1] if isinstance(dates[1], datetime.date) else datetime.date.fromisoformat(str(dates[1])[:10])
            if (_d1 - _d0).days <= 5 and _d1 in mw_flows:
                mw_flows[_d0] = mw_flows.get(_d0, 0.0) + mw_flows.pop(_d1)
        except Exception:
            pass

    def to_display(vals, ref=None):
        r = ref if ref is not None else (vals[0] if vals[0] else 1)
        # Time-weighted always shows P&L in dollars from $0; pct mode only for money-weighted.
        if time_basis:
            return [v - r for v in vals]
        if pct_mode:
            return [(v / r - 1) * 100 for v in vals]
        return list(vals)

    port_y = to_display(sl)

    def _fmt_pnl(v):
        return f"+${v:,.1f}" if v >= 0 else f"-${abs(v):,.1f}"

    # Daily change of the portfolio line: absolute $ in the Value view,
    # % in the Return / pct views. First point has no prior day.
    day_chg = ["—"]
    for i in range(1, len(sl)):
        prev, curr = sl[i - 1], sl[i]
        if time_basis or pct_mode:
            day_chg.append(f"{((curr / prev - 1) * 100) if prev else 0.0:+.2f}%")
        else:
            day_chg.append(_fmt_pnl(curr - prev))

    fig = go.Figure()
    portfolio_name = "Return" if return_basis == "time" else "Value"
    if time_basis or pct_mode:
        cum = [_fmt_pnl(v) for v in port_y] if time_basis else [f"{v:+.1f}%" for v in port_y]
        port_customdata = list(zip(cum, day_chg))
        port_hover = (f"<b>%{{customdata[0]}}</b> · 1D %{{customdata[1]}}"
                      f"<br>%{{x|%d %b %Y}}<extra>{portfolio_name}</extra>")
    else:
        port_customdata = day_chg
        port_hover = (f"<b>%{{y:$,.1f}}</b> · 1D %{{customdata}}"
                      f"<br>%{{x|%d %b %Y}}<extra>{portfolio_name}</extra>")
    fig.add_trace(go.Scatter(
        x=dates, y=port_y, mode="lines", name="Portfolio",
        line=dict(color=ACCENT, width=2.2),
        fill="tozeroy", fillcolor="rgba(54,211,153,0.10)",
        customdata=port_customdata,
        hovertemplate=port_hover,
    ))
    for key in (active_bench or []):
        bm = _BENCH.get(key)
        if not bm: continue

        raw_bench = None
        bd = bench_data and bench_data.get(key)
        if bd and bd.get("dates") and bd.get("values"):
            aligned = _align_bench(bd["dates"], bd["values"], dates)
            if aligned and aligned[0]:
                raw_bench = aligned

        if raw_bench is None:
            synthetic = _bench_series(bm["seed"], bm["drift"], bm["vol"], bm["shock"])
            raw_bench = synthetic[start:]

        # Time-weighted: show benchmark P&L scaled to portfolio starting value.
        # Money-weighted with cash flows: simulate same deposits/withdrawals into benchmark.
        if time_basis:
            bench_y = [(base * v / raw_bench[0]) - base for v in raw_bench]
        elif mw_flows:
            # Simulate: if same cash flows were invested into the benchmark
            bench_scale = base / raw_bench[0] if raw_bench[0] else 1
            bench_sim = [base]
            for i in range(1, len(raw_bench)):
                prev_val = bench_sim[-1]
                day_return = (raw_bench[i] / raw_bench[i-1] - 1) if raw_bench[i-1] else 0
                flow = 0.0
                if i < len(dates) and dates[i] in mw_flows:
                    flow = mw_flows[dates[i]]
                bench_sim.append(prev_val * (1 + day_return) + flow)
            if pct_mode:
                r = bench_sim[0] if bench_sim[0] else 1
                bench_y = [(v / r - 1) * 100 for v in bench_sim]
            else:
                bench_y = bench_sim
        elif pct_mode:
            bench_y = to_display(raw_bench)
        else:
            bench_y = [base * v / raw_bench[0] for v in raw_bench]

        fig.add_trace(go.Scatter(
            x=dates, y=bench_y, mode="lines", name=bm["label"],
            line=dict(color=bm["color"], width=1.4, dash="dot"), opacity=0.85,
            customdata=(
                [f"{bm['label']}: {_fmt_pnl(v)}" for v in bench_y] if time_basis
                else [f"{bm['label']}: {v:+.1f}%" for v in bench_y] if pct_mode
                else None
            ),
            hovertemplate=(
                "<b>%{customdata}</b><extra></extra>" if (time_basis or pct_mode)
                else f"<b>{bm['label']}</b>: %{{y:$,.1f}}<extra></extra>"
            ),
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0,r=52,t=4,b=28), showlegend=False,
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1b1f28", bordercolor="rgba(255,255,255,0.1)",
                        font=dict(family="JetBrains Mono, monospace",color=T1,size=12)),
        xaxis=dict(showgrid=False, zeroline=False, showline=False,
                   tickfont=dict(family="JetBrains Mono, monospace",color=T5,size=10),
                   tickformat={"1M": "%d %b", "3M": "%d %b"}.get(range_key, "%b '%y")),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                   zeroline=time_basis, zerolinecolor="rgba(255,255,255,0.14)", zerolinewidth=1,
                   showline=False, side="right", nticks=4,
                   tickfont=dict(family="JetBrains Mono, monospace",color=T5,size=10),
                   tickformat="+$~s" if time_basis else "+.1f" if pct_mode else "$~s",
                   ticksuffix="%" if pct_mode and not time_basis else ""),
        font=dict(family="JetBrains Mono, monospace", color=T3),
    )
    return fig

def build_donut(allocation, total):
    names  = [a["name"]  for a in allocation]
    values = [a["value"] for a in allocation]
    colors = [ALLOC_HEX.get(n,"#888") for n in names]
    fig = go.Figure(go.Pie(
        labels=names, values=values, hole=0.62,
        marker=dict(colors=colors, line=dict(width=0)),
        textposition="none",
        hovertemplate="<b>%{label}</b><br>%{value:$,.0f} (%{percent:.1%})<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0,r=0,t=0,b=0),
        showlegend=False,
        font=dict(family="Space Grotesk, sans-serif", color=T1),
        annotations=[dict(
            text=f"<b style='font-size:16px'>{money(total)}</b><br>"
                 f"<span style='color:{T4};font-size:9px;letter-spacing:0.5px'>TOTAL VALUE</span>",
            x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
            font=dict(family="Space Grotesk, sans-serif", color=T1, size=13),
        )],
    )
    return fig


def _build_world_map(country_perf: list):
    """Build a Plotly choropleth world map from country performance data."""
    import plotly.graph_objects as go

    if not country_perf:
        return html.Div("No market data available", style=dict(
            color=T4, fontSize="13px", padding="40px 0", textAlign="center"))

    codes = [r["code"] for r in country_perf]
    chg = [r["change_pct"] for r in country_perf]
    names = [r["country"] for r in country_perf]
    etfs = [r["etf"] for r in country_perf]

    hover = [
        f"<b>{n}</b><br>"
        f"ETF: {e}<br>"
        f"Change: {c:+.2f}%"
        for n, e, c in zip(names, etfs, chg)
    ]

    fig = go.Figure(go.Choropleth(
        locations=codes,
        z=chg,
        text=hover,
        hoverinfo="text",
        colorscale=[
            [0, RED],
            [0.5, "#2a2d35"],
            [1, ACCENT],
        ],
        zmin=-5,
        zmax=5,
        marker_line_color="rgba(255,255,255,0.08)",
        marker_line_width=0.5,
        showscale=False,
    ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=180,
        autosize=True,
        geo=dict(
            bgcolor="rgba(0,0,0,0)",
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.1)",
            # Countries without performance data fall back to this land colour;
            # black flags "not fetching data" and contrasts with the blue sea.
            showland=True,
            landcolor="#000000",
            showocean=True,
            oceancolor="#1e4d7b",
            showlakes=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.06)",
            projection_type="orthographic",
            projection_rotation=dict(lat=12, lon=0),
            # Faint graticule (meridians/parallels) reads as a globe surface and
            # sweeps realistically as the globe rotates.
            lonaxis=dict(showgrid=True, gridcolor="rgba(150,190,230,0.10)",
                         gridwidth=0.5, dtick=30),
            lataxis=dict(showgrid=True, gridcolor="rgba(150,190,230,0.10)",
                         gridwidth=0.5, dtick=30),
        ),
        font=dict(family="JetBrains Mono, monospace", color=T3),
    )

    # Fixed light source on the sphere: a soft highlight toward the upper-left
    # fading to a slightly darkened lower-right, faking specular shading so the
    # globe reads as a lit 3D surface. pointer-events off so hover passes
    # through to the choropleth beneath.
    sphere_shading = html.Div(style={
        "position": "absolute", "inset": "0", "pointerEvents": "none",
        "borderRadius": "10px", "zIndex": "2",
        "background": (
            "radial-gradient(circle at 38% 30%, "
            "rgba(255,255,255,0.16) 0%, rgba(255,255,255,0.05) 18%, "
            "rgba(255,255,255,0) 42%), "
            "radial-gradient(circle at 68% 74%, "
            "rgba(0,10,25,0.45) 0%, rgba(0,10,25,0) 46%)"
        ),
    })

    globe = html.Div([
        dcc.Graph(id="world-map-graph", figure=fig,
                  # Disable user zoom (scroll) and double-click reset; hover
                  # stays enabled. Drag-to-rotate is neutralised by the
                  # rotation callback, which reasserts the projection each tick.
                  config={"displayModeBar": False, "scrollZoom": False,
                          "doubleClick": False},
                  style={"height": "200px", "background": "transparent",
                         "border": "none", "outline": "none"}),
        sphere_shading,
    ], style={"flex": "1 1 55%", "minWidth": "0", "background": "transparent",
              "borderRadius": "10px", "overflow": "hidden",
              "position": "relative"})

    return html.Div([globe, _build_movers_panel(country_perf)],
                    style=dict(display="flex", alignItems="center",
                               justifyContent="space-between",
                               gap="18px", width="100%"))


def _build_movers_panel(country_perf: list):
    """Best/worst country ETF movers, shown to the right of the globe."""
    ranked = sorted(country_perf, key=lambda r: r["change_pct"], reverse=True)
    best = ranked[:5]
    worst = sorted(ranked[len(best):][-5:], key=lambda r: r["change_pct"], reverse=True)

    def row(r):
        pos = r["change_pct"] >= 0
        return html.Div([
            html.Span(r["country"], style=dict(
                color=T2, fontSize="12.5px", fontWeight="500",
                whiteSpace="nowrap", overflow="hidden", textOverflow="ellipsis")),
            html.Span(f'{r["change_pct"]:+.1f}%', style=dict(
                color=ACCENT if pos else RED, fontSize="12.5px", fontWeight="600",
                fontFamily="'JetBrains Mono',monospace", flexShrink="0")),
        ], style=dict(display="flex", justifyContent="space-between",
                      alignItems="center", gap="14px"))

    def group(label, rows, color):
        return html.Div([
            html.Div(label, style=dict(
                fontSize="10.5px", fontWeight="600", letterSpacing="0.5px",
                textTransform="uppercase", color=color, marginBottom="8px")),
            html.Div([row(r) for r in rows],
                     style=dict(display="flex", flexDirection="column", gap="8px")),
        ])

    return html.Div([
        group("Best", best, ACCENT),
        group("Worst", worst, RED),
    ], style=dict(flex="0 0 170px", display="flex", flexDirection="column",
                  gap="18px", transform="translateY(-15px)"))


def _add_trade_markers(fig, trades, dates, ccy="$"):
    """Overlay buy/sell markers on the price panel (row 1) of ``fig``.

    Trades are clipped to the visible date window and aggregated per
    (date, side) so multiple fills on one day collapse to a single marker.
    """
    if not trades or not dates:
        return

    lo, hi = min(dates), max(dates)  # ISO date strings compare correctly

    # Aggregate by (date, action): volume-weighted price, total quantity.
    agg = {}
    for t in trades:
        d = t.get("date", "")[:10]
        if not d or d < lo or d > hi:
            continue
        side = t.get("action", "").upper()
        if side not in ("BUY", "SELL"):
            continue
        qty = float(t.get("quantity") or 0)
        px  = float(t.get("price") or 0)
        if px <= 0:
            continue
        key = (d, side)
        a = agg.setdefault(key, {"qty": 0.0, "notional": 0.0})
        a["qty"]      += qty
        a["notional"] += qty * px

    if not agg:
        return

    def _series(side):
        xs, ys, texts = [], [], []
        for (d, s), a in sorted(agg.items()):
            if s != side:
                continue
            qty = a["qty"]
            avg = a["notional"] / qty if qty else 0.0
            xs.append(d)
            ys.append(avg)
            qty_str = f"{qty:.0f}" if qty == int(qty) else f"{qty:.2f}"
            texts.append(f"<b>{side}</b><br>{d}<br>{qty_str} @ {ccy}{avg:,.2f}")
        return xs, ys, texts

    for side, color, symbol, name in (
        ("BUY",  ACCENT, "triangle-up",   "Buy"),
        ("SELL", RED,    "triangle-down", "Sell"),
    ):
        xs, ys, texts = _series(side)
        if not xs:
            continue
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers", name=name,
            marker=dict(symbol=symbol, size=12, color=color,
                        line=dict(width=1.4, color="#0a0c10")),
            hovertext=texts, hoverinfo="text",
            showlegend=True,
        ), row=1, col=1)


def _kalman_log_level(closes, lam=0.01):
    """Causal Kalman smoother (local-level model) run on log prices, returned
    in price space.

    ``lam`` is the process/measurement noise ratio Q/R and is the single
    smoothing knob: higher tracks price more closely, lower is smoother
    (``lam=0.01`` behaves like a ~20-period EMA). In a linear Kalman filter the
    covariance and gain evolve independently of the observations, so the
    smoothing is scale-independent; filtering in log space (then exp-ing back)
    keeps the responsiveness proportional across price levels rather than in
    absolute dollars. The returned series is aligned 1:1 with ``closes`` and
    already in dollars, so it overlays the candles on the same axis.
    """
    import math
    out = []
    L, P, R = None, 1.0, 1.0
    Q = max(lam, 1e-9) * R
    for c in closes:
        if c is None or c <= 0:            # skip bad prints, hold last estimate
            out.append(math.exp(L) if L is not None else None)
            continue
        y = math.log(c)
        if L is None:                      # initialise on first valid price
            L = y
            out.append(c)
            continue
        P = P + Q                          # predict
        K = P / (P + R)                    # Kalman gain
        L = L + K * (y - L)                # update toward the new observation
        P = (1 - K) * P
        out.append(math.exp(L))            # transform back to price space
    return out


def _build_candlestick_chart(daily, trades=None, kalman=False, ccy="$"):
    """Build a candlestick chart with volume bars from daily OHLCV data.

    ``trades`` is an optional list of the portfolio's buy/sell trades
    (from ``_get_symbol_trades``); when supplied, markers are overlaid on the
    price panel showing where the security was bought and sold.
    ``kalman`` overlays a log-space Kalman-smoothed price line.
    ``ccy`` is the currency symbol used in hover labels and the price axis.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    if not daily:
        return html.Div("No chart data available", style=dict(
            color=T4,fontSize="13px",padding="60px 0",textAlign="center"))

    dates = [b["date"] for b in daily]
    opens = [b["open"] for b in daily]
    highs = [b["high"] for b in daily]
    lows = [b["low"] for b in daily]
    closes = [b["close"] for b in daily]
    volumes = [b["volume"] for b in daily]

    # Color each candle green/red
    colors = [ACCENT if c >= o else RED for o, c in zip(opens, closes)]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
    )

    # Candlestick
    hover_texts = [
        f"{d}<br>Open: {ccy}{o:.2f}<br>High: {ccy}{h:.2f}<br>Low: {ccy}{l:.2f}<br>Close: {ccy}{c:.2f}"
        for d, o, h, l, c in zip(dates, opens, highs, lows, closes)
    ]
    fig.add_trace(go.Candlestick(
        x=dates, open=opens, high=highs, low=lows, close=closes,
        increasing_line_color=ACCENT, decreasing_line_color=RED,
        increasing_fillcolor=ACCENT, decreasing_fillcolor=RED,
        hovertext=hover_texts, hoverinfo="text",
        name="Price", showlegend=False,
    ), row=1, col=1)

    # Kalman-smoothed price overlay (computed in log space, shown in dollars)
    if kalman:
        kline = _kalman_log_level(closes)
        fig.add_trace(go.Scatter(
            x=dates, y=kline, mode="lines",
            line=dict(color="#7fb2ff", width=1.8),
            name="Kalman", showlegend=True,
            hovertemplate=f"Kalman: {ccy}%{{y:.2f}}<extra></extra>",
        ), row=1, col=1)

    # Volume bars
    fig.add_trace(go.Bar(
        x=dates, y=volumes,
        marker_color=colors,
        hovertemplate="%{x}<br>Vol: %{y:,.0f}<extra></extra>",
        name="Volume", showlegend=False,
    ), row=2, col=1)

    # Buy / sell markers — where the portfolio traded this security
    _add_trade_markers(fig, trades, dates, ccy=ccy)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=50, r=16, t=8, b=32),
        height=380,
        showlegend=True,
        legend=dict(
            orientation="h", x=0, y=1.02, xanchor="left", yanchor="bottom",
            bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=T3),
        ),
        xaxis_rangeslider_visible=False,
        xaxis=dict(showgrid=False, color=T4, tickfont=dict(size=10)),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                   color=T4, tickfont=dict(size=10), tickprefix=ccy),
        xaxis2=dict(showgrid=False, color=T4, tickfont=dict(size=10)),
        yaxis2=dict(showgrid=False, color=T4, tickfont=dict(size=10)),
    )

    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style=dict(height="380px"))
