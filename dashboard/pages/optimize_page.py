"""Portfolio Optimization page: method/parameter controls, a current-vs-
optimized weights chart and a stats table.
"""

from dash import dcc, html, Input, Output, State, no_update, ALL
from dash.exceptions import PreventUpdate

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T4


def optimize_page():
    """Portfolio optimization page."""
    method_options = [
        {"label": "Mean-Variance (Markowitz)", "value": "mean_variance"},
        {"label": "Maximum Sharpe Ratio", "value": "max_sharpe"},
        {"label": "Minimum Variance", "value": "min_variance"},
        {"label": "Risk Parity", "value": "risk_parity"},
        {"label": "Hierarchical Risk Parity", "value": "hrp"},
    ]
    return html.Div([
        # Controls
        html.Div([
            html.Div([
                html.Div("Method", style=dict(fontSize="12px",color=T4,
                    fontWeight="600",marginBottom="6px",letterSpacing="0.3px")),
                dcc.Dropdown(id="opt-method", options=method_options,
                    value="mean_variance", clearable=False,
                    style=dict(background="#1e2330",color=T1,
                        border=f"1px solid {BORDER}",borderRadius="9px")),
            ], style=dict(flex="1.5")),
            html.Div([
                html.Div("Risk-Free Rate (%)", style=dict(fontSize="12px",color=T4,
                    fontWeight="600",marginBottom="6px",letterSpacing="0.3px")),
                dcc.Input(id="opt-rf", type="number", value=4.0, step=0.5,
                    style=dict(background="#1e2330",color=T1,
                        border=f"1px solid {BORDER}",borderRadius="9px",
                        padding="9px 12px",fontSize="13px",width="100%")),
            ], style=dict(flex="0.8")),
            html.Div([
                html.Div("Lookback (days)", style=dict(fontSize="12px",color=T4,
                    fontWeight="600",marginBottom="6px",letterSpacing="0.3px")),
                dcc.Input(id="opt-lookback", type="number", value=90, step=30,
                    min=30, max=365,
                    style=dict(background="#1e2330",color=T1,
                        border=f"1px solid {BORDER}",borderRadius="9px",
                        padding="9px 12px",fontSize="13px",width="100%")),
            ], style=dict(flex="0.8")),
            html.Button("Run Optimization", id="opt-run", n_clicks=0,
                style=dict(border="none",background=ACCENT,color="#06150f",
                    fontWeight="600",fontSize="13px",padding="10px 20px",
                    borderRadius="9px",cursor="pointer",fontFamily="inherit",
                    alignSelf="flex-end")),
        ], style=dict(display="flex",gap="16px",alignItems="flex-end",
                      padding="20px 28px",background=BG_CARD,
                      borderRadius="14px",border=f"1px solid {BORDER}")),
        # Status
        html.Div(id="opt-status"),
        dcc.Store(id="opt-data-store"),
        dcc.Store(id="opt-sort-store", data={"col": "Change", "asc": False}),
        # Results — wrapped in a loading overlay so a spinner shows over this
        # area while the (slow) optimization callback fetches history and solves.
        dcc.Loading(
            id="opt-loading", type="circle", color=ACCENT,
            children=html.Div([
                # Chart (left) + stats (right)
                html.Div([
                    # Weights comparison chart
                    html.Div(id="opt-chart-card",
                        style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                            borderRadius="14px",padding="20px",minHeight="360px")),
                    # Stats table
                    html.Div(id="opt-stats-card",
                        style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                            borderRadius="14px",padding="20px")),
                ], style=dict(display="grid",
                    gridTemplateColumns="minmax(0,2.2fr) minmax(0,1fr)",gap="18px")),
                # Weights comparison table
                html.Div(id="opt-weights-table-wrap",
                    style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                        borderRadius="14px",padding="20px")),
            ], style=dict(display="flex",flexDirection="column",gap="18px")),
        ),
    ], style=dict(padding="24px 28px 40px",display="flex",
                  flexDirection="column",gap="18px"))


def _stat_row(label, value):
    return html.Div([
        html.Span(label, style=dict(color=T4,fontSize="12.5px")),
        html.Span(value, style=dict(
            fontFamily="'JetBrains Mono',monospace",
            fontSize="12.5px",fontWeight="600",color=T1)),
    ], style=dict(display="flex",justifyContent="space-between",
                  padding="7px 0",borderBottom="1px solid rgba(255,255,255,0.05)"))


# --- Callbacks -------------------------------------------------------

# Immediate button feedback on click (client-side, so no server round-trip
# delay): disable the button and relabel it while the optimization runs. The
# server re-enables it below once results land.
app.clientside_callback(
    """
    function(n) {
        if (!n) { return window.dash_clientside.no_update; }
        return [true, 'Running…'];
    }
    """,
    Output("opt-run", "disabled"),
    Output("opt-run", "children"),
    Input("opt-run", "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("opt-run", "disabled", allow_duplicate=True),
    Output("opt-run", "children", allow_duplicate=True),
    Input("opt-status", "children"),
    prevent_initial_call=True,
)
def _reenable_run(_status):
    # opt-status is written on every run_optimization return path (success or
    # early exit), so this fires once the run finishes and restores the button.
    return False, "Run Optimization"


_COLS = ["Ticker", "Current", "Optimized", "Change"]


def _build_backtest_fig(returns_df, common_tickers, current_weights, opt_weights, rf_rate):
    import numpy as np
    import plotly.graph_objects as go

    curr_w = np.array([current_weights.get(t, 0) for t in common_tickers])
    opt_w = np.array([opt_weights.get(t, 0) for t in common_tickers])

    port_returns = returns_df[common_tickers].values
    curr_daily = port_returns @ curr_w
    opt_daily = port_returns @ opt_w

    cum_curr = np.cumprod(1 + curr_daily)
    cum_opt = np.cumprod(1 + opt_daily)
    dates = returns_df.index

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=(cum_curr - 1) * 100, name="Current",
        line=dict(color="rgba(107,114,128,0.8)", width=1.5),
        hovertemplate="%{x|%b %d, %Y}<br>Current: %{y:.2f}%<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=dates, y=(cum_opt - 1) * 100, name="Optimized",
        line=dict(color=ACCENT, width=1.5),
        hovertemplate="%{x|%b %d, %Y}<br>Optimized: %{y:.2f}%<extra></extra>"))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=40,r=16,t=8,b=40),
        height=270,
        legend=dict(orientation="h",y=1.1,x=0.5,xanchor="center",
                    font=dict(size=11)),
        xaxis=dict(showgrid=False, color=T4, tickfont=dict(size=10),
                   title=dict(text="Date", font=dict(size=11, color=T4)),
                   tickformat="%b %d, %Y"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                   color=T4, tickfont=dict(size=10), ticksuffix="%"),
        hovermode="x unified",
    )
    return fig

def _build_weights_table(tickers, current_weights, opt_weights, sort_data):
    col = sort_data.get("col", "Change")
    asc = sort_data.get("asc", True)

    def _sort_key(t):
        if col == "Ticker":
            return t
        elif col == "Current":
            return current_weights.get(t, 0)
        elif col == "Optimized":
            return opt_weights.get(t, 0)
        else:  # Change
            return opt_weights.get(t, 0) - current_weights.get(t, 0)

    sorted_tickers = sorted(tickers, key=_sort_key, reverse=not asc)

    def _arrow(c):
        if c != col:
            return ""
        return " \u25B2" if asc else " \u25BC"

    header_style = dict(fontWeight="600",fontSize="12px",color=T4,cursor="pointer",
        userSelect="none",padding="8px 0",borderBottom=f"1px solid {BORDER}")
    return html.Div([
        html.Div("Current vs Optimized Weights", style=dict(
            fontSize="13px",fontWeight="600",color=T2,marginBottom="8px")),
        html.Div([
            html.Div([
                html.Span(f"{c}{_arrow(c)}", id={"type": "opt-sort-btn", "index": c},
                    className="opt-sort-btn",
                    style=header_style | {"flex": "1", "textAlign": "right"} if c != "Ticker"
                          else header_style | {"flex": "1"})
                for c in _COLS
            ], style=dict(display="flex")),
        ] + [
            html.Div([
                html.Div(t, style=dict(fontSize="12.5px",color=T1,flex="1",
                    fontFamily="'JetBrains Mono',monospace")),
                html.Div(f"{current_weights.get(t, 0)*100:.2f}%", style=dict(
                    fontSize="12.5px",color=T1,textAlign="right",flex="1",
                    fontFamily="'JetBrains Mono',monospace")),
                html.Div(f"{opt_weights.get(t, 0)*100:.2f}%", style=dict(
                    fontSize="12.5px",color=ACCENT,textAlign="right",flex="1",
                    fontFamily="'JetBrains Mono',monospace")),
                html.Div(
                    f"{(opt_weights.get(t, 0) - current_weights.get(t, 0))*100:+.2f}%",
                    style=dict(fontSize="12.5px",textAlign="right",flex="1",
                        fontFamily="'JetBrains Mono',monospace",
                        color=ACCENT if opt_weights.get(t, 0) > current_weights.get(t, 0)
                              else RED if opt_weights.get(t, 0) < current_weights.get(t, 0)
                              else T1)),
            ], style=dict(display="flex",padding="6px 0",
                borderBottom="1px solid rgba(255,255,255,0.04)"))
            for t in sorted_tickers
        ], className="opt-weights-table",
            style=dict(maxHeight="300px",overflowY="auto",marginTop="4px")),
    ])

@app.callback(
    Output("opt-status","children"),
    Output("opt-chart-card","children"),
    Output("opt-stats-card","children"),
    Output("opt-data-store","data"),
    Output("opt-sort-store","data"),
    Output("opt-weights-table-wrap","children"),
    Input("opt-run","n_clicks"),
    State("opt-method","value"),
    State("opt-rf","value"),
    State("opt-lookback","value"),
    prevent_initial_call=True,
)
def run_optimization(n, method, rf_pct, lookback):
    print(f"[OPT] Callback fired: n={n}, method={method}, rf={rf_pct}, lookback={lookback}")
    import datetime as _dt
    import numpy as np
    from services.ibkr_client import get_client
    import plotly.graph_objects as go

    client = get_client()
    print(f"[OPT] Client connected: {client.is_connected}")
    d = client.cached_snapshot
    print(f"[OPT] cached_snapshot holdings: {None if d.holdings is None else (len(d.holdings) if not d.holdings.empty else 'empty')}")
    holdings = d.holdings if d.holdings is not None and not d.holdings.empty else None
    if holdings is None or holdings.empty:
        print("[OPT] No holdings — returning early")
        empty = html.Div("No holdings data available.",
                    style=dict(color=RED,fontSize="13px",padding="8px 0"))
        return empty, no_update, no_update, no_update, no_update, no_update

    non_cash = holdings[holdings["Symbol"] != "CASH"].copy()
    print(f"[OPT] Non-CASH holdings: {len(non_cash)} — {non_cash['Symbol'].tolist()}")
    if len(non_cash) < 2:
        need = html.Div("Need at least 2 securities for optimization.",
                    style=dict(color=RED,fontSize="13px",padding="8px 0"))
        return need, no_update, no_update, no_update, no_update, no_update

    tickers = non_cash["Symbol"].tolist()
    current_weights = {row["Symbol"]: row["% Port"] / 100.0
                       for _, row in non_cash.iterrows()}
    total_w = sum(current_weights.values())
    if total_w > 0:
        current_weights = {t: w / total_w for t, w in current_weights.items()}
    print(f"[OPT] Tickers: {tickers}")
    print(f"[OPT] Current weights: {current_weights}")

    # Fetch historical prices for all holdings concurrently
    from ib_insync import Contract
    import pandas as pd
    import asyncio
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lookback_days = int(lookback or 90)

    def _fetch_one(ticker):
        row = non_cash[non_cash["Symbol"] == ticker].iloc[0]
        currency = row.get("Currency", "USD")
        contract = Contract(symbol=ticker, secType="STK",
                            exchange="SMART", currency=currency)
        future = asyncio.run_coroutine_threadsafe(
            client._ib.reqHistoricalDataAsync(
                contract, endDateTime="",
                durationStr=f"{lookback_days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES", useRTH=True),
            client._loop)
        bars = future.result(timeout=30)
        dates = [bar.date for bar in bars]
        closes = [bar.close for bar in bars if bar.close > 0]
        return ticker, dates, closes

    price_data = {}
    date_data = {}
    failed_tickers = []
    print(f"[OPT] Fetching {len(tickers)} tickers concurrently ...")
    with ThreadPoolExecutor(max_workers=len(tickers)) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                _, dates, closes = fut.result()
                print(f"[OPT]   {ticker}: {len(closes)} valid closes")
                if len(closes) >= 20:
                    price_data[ticker] = closes[-lookback_days:]
                    date_data[ticker] = dates[-lookback_days:]
                else:
                    print(f"[OPT]   {ticker}: skipped (only {len(closes)} closes, need 20)")
                    failed_tickers.append(ticker)
            except Exception as e:
                print(f"[OPT]   {ticker}: FAILED — {e}")
                failed_tickers.append(ticker)

    print(f"[OPT] Price data succeeded: {list(price_data.keys())}, failed: {failed_tickers}")
    if len(price_data) < 2:
        print("[OPT] Not enough tickers with price data — returning early")
        insuff = html.Div("Insufficient price data. Make sure IBKR is connected.",
                    style=dict(color=RED,fontSize="13px",padding="8px 0"))
        return insuff, no_update, no_update, no_update, no_update, no_update

    # Build returns DataFrame (columns in tickers order)
    min_len = min(len(v) for v in price_data.values())
    # Use dates from the first ticker as the index (all tickers have aligned dates)
    first_ticker = list(price_data.keys())[0]
    date_index = pd.DatetimeIndex(date_data[first_ticker][-min_len:])
    prices_df = pd.DataFrame({t: v[-min_len:] for t, v in price_data.items()}, index=date_index)
    returns_df = prices_df.pct_change().dropna()
    available_tickers = list(returns_df.columns)
    print(f"[OPT] Returns DF shape: {returns_df.shape}, columns: {available_tickers}")

    # Run optimization
    rf_rate = (rf_pct or 4.0) / 100.0
    from services.optimizer import METHODS, run_optimization as _run_opt
    try:
        print(f"[OPT] Running optimizer: method={method}, rf_rate={rf_rate}")
        opt_weights = _run_opt(method, returns_df, risk_free_rate=rf_rate)
        print(f"[OPT] Opt weights: {opt_weights}")
    except Exception as e:
        print(f"[OPT] Optimizer FAILED: {e}")
        import traceback; traceback.print_exc()
        fail = html.Div(f"Optimization failed: {e}",
                    style=dict(color=RED,fontSize="13px",padding="8px 0"))
        return fail, no_update, no_update, no_update, no_update, no_update

    # Build comparison chart
    common_tickers = sorted(opt_weights.keys())
    curr_vals = [current_weights.get(t, 0) * 100 for t in common_tickers]
    opt_vals = [opt_weights.get(t, 0) * 100 for t in common_tickers]
    print(f"[OPT] Building chart: common_tickers={common_tickers}")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Current", x=common_tickers, y=curr_vals,
        marker_color="rgba(107,114,128,0.6)", text=[f"{v:.1f}%" for v in curr_vals],
        textposition="auto", textfont=dict(size=11)))
    fig.add_trace(go.Bar(
        name="Optimized", x=common_tickers, y=opt_vals,
        marker_color=ACCENT, text=[f"{v:.1f}%" for v in opt_vals],
        textposition="auto", textfont=dict(size=11)))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=40,r=16,t=32,b=40),
        height=340, barmode="group",
        legend=dict(orientation="h",y=1.08,x=0.5,xanchor="center",
                    font=dict(size=11)),
        xaxis=dict(showgrid=False, color=T4, tickfont=dict(size=11)),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                   color=T4, tickfont=dict(size=11), ticksuffix="%"),
    )
    chart_card = html.Div([
        html.Div("Current vs Optimized Weights", style=dict(
            fontSize="13px",fontWeight="600",color=T2,marginBottom="8px")),
        dcc.Graph(figure=fig, config={"displayModeBar": False},
                  style=dict(height="340px")),
        # Backtest chart
        html.Div("Portfolio Backtest (lookback period)", style=dict(
            fontSize="13px",fontWeight="600",color=T2,marginBottom="8px",marginTop="16px")),
        dcc.Graph(figure=_build_backtest_fig(returns_df, common_tickers,
                    current_weights, opt_weights, rf_rate),
                  config={"displayModeBar": False},
                  style=dict(height="280px")),
    ])

    # Stats table — align mean returns and covariance to common_tickers order
    mean_rets = returns_df[common_tickers].mean().values
    cov_matrix = returns_df[common_tickers].cov().values
    opt_w = np.array(opt_vals) / 100.0

    ret_opt = float(mean_rets @ opt_w * 252)
    var_opt = float(opt_w @ (cov_matrix * 252) @ opt_w)
    vol_opt = np.sqrt(max(var_opt, 0))
    sharpe_opt = (ret_opt - rf_rate) / vol_opt if vol_opt > 0 else 0

    curr_w = np.array([current_weights.get(t, 0) for t in common_tickers])
    ret_curr = float(mean_rets @ curr_w * 252)
    var_curr = float(curr_w @ (cov_matrix * 252) @ curr_w)
    vol_curr = np.sqrt(max(var_curr, 0))
    sharpe_curr = (ret_curr - rf_rate) / vol_curr if vol_curr > 0 else 0

    # Sortino ratio (downside deviation)
    opt_daily = returns_df[common_tickers].values @ opt_w
    downside = opt_daily[opt_daily < 0]
    downside_std = np.sqrt(np.mean(downside ** 2)) * np.sqrt(252) if len(downside) > 0 else 0
    sortino_opt = (ret_opt - rf_rate) / downside_std if downside_std > 0 else 0

    curr_daily = returns_df[common_tickers].values @ curr_w
    downside_c = curr_daily[curr_daily < 0]
    downside_std_c = np.sqrt(np.mean(downside_c ** 2)) * np.sqrt(252) if len(downside_c) > 0 else 0
    sortino_curr = (ret_curr - rf_rate) / downside_std_c if downside_std_c > 0 else 0

    # Max drawdown
    cum_opt = np.cumprod(1 + opt_daily)
    peak_opt = np.maximum.accumulate(cum_opt)
    mdd_opt = float(np.min((cum_opt - peak_opt) / peak_opt))

    cum_curr = np.cumprod(1 + curr_daily)
    peak_curr = np.maximum.accumulate(cum_curr)
    mdd_curr = float(np.min((cum_curr - peak_curr) / peak_curr))

    # Calmar ratio (annual return / |max drawdown|)
    calmar_opt = ret_opt / abs(mdd_opt) if abs(mdd_opt) > 1e-10 else 0
    calmar_curr = ret_curr / abs(mdd_curr) if abs(mdd_curr) > 1e-10 else 0

    # Diversification ratio (weighted avg vol / portfolio vol)
    asset_vols = np.sqrt(np.diag(cov_matrix))
    div_ratio = float((opt_w @ asset_vols) / vol_opt) if vol_opt > 0 else 1.0

    # Active positions
    n_active = int(np.sum(opt_w > 0.005))

    # Best / worst day
    best_day_opt = float(np.max(opt_daily))
    worst_day_opt = float(np.min(opt_daily))

    def _pct(v):
        return f"{v*100:.1f}%"

    def _diff(a, b):
        d = a - b
        sign = "+" if d >= 0 else ""
        return f"{sign}{d*100:.1f}%"

    stats_rows = [
        _stat_row("Method", METHODS[method][0] if method in METHODS else method),
        _stat_row("Lookback", f"{lookback_days} days"),
        _stat_row("Holdings", f"{n_active} active / {len(common_tickers)} total"),
        _stat_row("Risk-Free Rate", _pct(rf_rate)),
        _stat_row("Annual Return", _pct(ret_opt)),
        _stat_row("Annual Volatility", _pct(vol_opt)),
        _stat_row("Sharpe Ratio", f"{sharpe_opt:.2f}"),
        _stat_row("Sortino Ratio", f"{sortino_opt:.2f}"),
        _stat_row("Max Drawdown", _pct(mdd_opt)),
        _stat_row("Calmar Ratio", f"{calmar_opt:.2f}"),
        _stat_row("Diversification", f"{div_ratio:.2f}"),
        _stat_row("Best Day", _pct(best_day_opt)),
        _stat_row("Worst Day", _pct(worst_day_opt)),
    ]
    # Current vs Optimized comparison section
    if vol_curr > 0:
        stats_rows.append(_stat_row("─" * 20, ""))
        stats_rows.append(_stat_row("Current Annual Return", _pct(ret_curr)))
        stats_rows.append(_stat_row("Current Volatility", _pct(vol_curr)))
        stats_rows.append(_stat_row("Current Sharpe", f"{sharpe_curr:.2f}"))
        stats_rows.append(_stat_row("Current Sortino", f"{sortino_curr:.2f}"))
        stats_rows.append(_stat_row("Current Max DD", _pct(mdd_curr)))
        stats_rows.append(_stat_row("Current Calmar", f"{calmar_curr:.2f}"))
        stats_rows.append(_stat_row("Return Diff", _diff(ret_opt, ret_curr)))
        stats_rows.append(_stat_row("Vol Diff", _diff(vol_opt, vol_curr)))
        stats_rows.append(_stat_row("Sharpe Diff", f"{sharpe_opt - sharpe_curr:+.2f}"))
    stats_card = html.Div([
        html.Div("Optimization Stats", style=dict(
            fontSize="13px",fontWeight="600",color=T2,marginBottom="12px")),
        html.Div(stats_rows, style=dict(maxHeight="calc(100vh - 200px)",overflowY="auto")),
    ])

    method_name = METHODS[method][0] if method in METHODS else method
    skipped = f" (skipped: {', '.join(failed_tickers)})" if failed_tickers else ""
    status = html.Div(f"Optimized using {method_name} on {len(common_tickers)} holdings "
                      f"({lookback_days}-day lookback){skipped}",
        style=dict(fontSize="12.5px",color=ACCENT,padding="4px 0"))

    # Data for sorting
    import json
    data_store = json.dumps({
        "current_weights": {t: current_weights.get(t, 0) for t in common_tickers},
        "opt_weights": {t: opt_weights.get(t, 0) for t in common_tickers},
        "common_tickers": common_tickers,
    })
    sort_data = {"col": "Change", "asc": False}

    print(f"[OPT] Done — returning chart + stats")
    return (status, chart_card, stats_card, data_store, sort_data,
            _build_weights_table(common_tickers, current_weights, opt_weights, sort_data))


@app.callback(
    Output("opt-sort-store","data", allow_duplicate=True),
    Output("opt-weights-table-wrap","children", allow_duplicate=True),
    Input({"type": "opt-sort-btn", "index": ALL}, "n_clicks"),
    State("opt-data-store","data"),
    State("opt-sort-store","data"),
    prevent_initial_call=True,
)
def sort_weights_table(n_clicks, data_json, sort_data):
    import json as _json
    from dash import ctx

    if not ctx.triggered or not data_json:
        raise PreventUpdate

    btn = ctx.triggered_id
    if not isinstance(btn, dict) or btn.get("type") != "opt-sort-btn":
        raise PreventUpdate

    clicked_col = btn["index"]
    if clicked_col == sort_data.get("col"):
        sort_data["asc"] = not sort_data.get("asc", True)
    else:
        sort_data = {"col": clicked_col, "asc": True}

    data = _json.loads(data_json)
    table = _build_weights_table(
        data["common_tickers"],
        data["current_weights"],
        data["opt_weights"],
        sort_data,
    )
    return sort_data, table
