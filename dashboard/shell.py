"""Application shell: sidebar, (hidden) topbar, page assembly, the top-level
layout, the navigation/sidebar callbacks and the global data-refresh callback.

The ``refresh`` callback is intentionally a single cross-cutting callback: it
calls :func:`dashboard.data.get_data` once per tick and fans the result out to
the dashboard, holdings, side-rail, income, news, world-map and status
components. Splitting it per page would call ``get_data`` several times per
tick, so it lives here rather than in any one page module.
"""

import time

import pandas as pd
from dash import dcc, html, Input, Output, State, no_update, ALL, ClientsideFunction

from dashboard.app_instance import app
from dashboard.theme import (BG, BG_SIDE, BG_CARD, BORDER, ACCENT, RED,
                             T1, T2, T3, T4, T5, ALLOC_HEX,
                             _RANGES, _BENCH, REFRESH)
from dashboard.icons import (_icon, _icon_src,
                             _ICO_GRID, _ICO_LIST, _ICO_TREND, _ICO_CLOCK,
                             _ICO_DOC, _ICO_SEARCH, _ICO_SLIDERS,
                             _ICO_TARGET, _ICO_GLOBE,
                             _ICO_CHEVRON_LEFT, _ICO_CHEVRON_RIGHT)
from dashboard.formatters import money, pct_fmt, pnl_c
from dashboard.charts import build_main_chart, build_donut, spark, _build_world_map
from dashboard.data import (get_data, get_portfolio_metrics, get_portfolio_risk_metrics,
                            _div_cache, _fetch_news, _get_country_perf)

# Importing the page modules registers their callbacks and provides their
# layout builders for main_content() / the refresh callback.
from dashboard.pages.dashboard_page import dashboard_page
from dashboard.pages.holdings_page import holdings_page, _build_holdings_header
from dashboard.pages.search_page import search_page
from dashboard.pages.security_page import security_page
from dashboard.pages.cgt_page import cgt_page
from dashboard.pages.optimize_page import optimize_page
from dashboard.pages.macro_page import macro_page
from dashboard.pages.markets_page import markets_page
from dashboard.pages.performance_page import performance_page
from dashboard.pages.backtest_page import backtest_page


# --- Nav item helper -------------------------------------------------

def _nav_item(ico_body, label, active=False, badge=None, nav_id=None):
    base = dict(display="flex",alignItems="center",gap="11px",
                padding="9px 12px",borderRadius="9px",
                textDecoration="none",fontWeight="500",cursor="pointer",
                position="relative")
    base.update(color=T1 if active else T2,
                background="rgba(54,211,153,0.11)" if active else "transparent",
                boxShadow="none")
    icon = _icon(ico_body, active=active)
    if nav_id:
        icon.id = {"type":"nav-icon","index":nav_id}
    children = [icon, label]
    if badge:
        bc, bbg, btxt = badge
        children.append(html.Span(btxt,
            style=dict(marginLeft="auto",fontSize="9.5px",fontWeight="600",
                       letterSpacing="0.4px",color=bc,background=bbg,
                       padding="2px 6px",borderRadius="5px")))
    div_kwargs = dict(children=children, style=base)
    if nav_id:
        div_kwargs["id"] = nav_id
    return html.Div(**div_kwargs)


# --- Layout builders -------------------------------------------------

def sidebar():
    content = html.Div([
        # Nav
        html.Nav([
            _nav_item(_ICO_GRID,   "Dashboard",    active=True, nav_id="nav-dashboard"),
            _nav_item(_ICO_SEARCH, "Search",                   nav_id="nav-search"),
            _nav_item(_ICO_LIST,   "Holdings",                 nav_id="nav-holdings"),
            _nav_item(_ICO_TREND,  "Performance",              nav_id="nav-performance"),
            _nav_item(_ICO_CLOCK,  "Backtest",                  nav_id="nav-backtest",
                      badge=("#7fb2ff","rgba(74,168,255,0.13)","BETA")),
            _nav_item(_ICO_DOC,    "Capital Gains",            nav_id="nav-cgt",
                      badge=("#36d399","rgba(54,211,153,0.13)","AU TAX")),
            _nav_item(_ICO_TARGET, "Optimization",             nav_id="nav-optimize"),
            html.Div(_nav_item(_ICO_GLOBE, "Macro", nav_id="nav-macro"),
                     style=dict(display="none")),
            html.Div(_nav_item(_ICO_GLOBE, "Markets", nav_id="nav-markets"),
                     style=dict(display="none")),
        ], style=dict(display="flex",flexDirection="column",gap="2px",padding="12px 12px 0 12px")),
        # Bottom
        html.Div([
            html.Div([_icon(_ICO_SLIDERS), "Settings"],
                style=dict(display="flex",alignItems="center",gap="11px",
                    padding="9px 12px",color=T2,fontWeight="500",cursor="pointer")),
            html.Div([
                html.Div("JM", style=dict(
                    width="30px",height="30px",borderRadius="50%",
                    background="linear-gradient(135deg,#3a4252,#222732)",
                    display="flex",alignItems="center",justifyContent="center",
                    fontWeight="600",fontSize="12px",color="#cfd3da",flexShrink="0")),
                html.Div([
                    html.Div("Personal Account",style=dict(fontSize="12.5px",fontWeight="600")),
                    html.Div(id="acct-sub",style=dict(fontSize="11px",color=T5)),
                ], style=dict(minWidth="0")),
            ], style=dict(display="flex",alignItems="center",gap="10px",
                marginTop="8px",padding="10px 12px",borderRadius="11px",
                border="1px solid rgba(255,255,255,0.07)")),
        ], style=dict(marginTop="auto",padding="14px 12px")),
    ], id="sidebar-el", style=dict(
        width="236px", overflow="hidden", flexShrink="0",
        display="flex", flexDirection="column",
        background=BG_SIDE, transition="width 0.2s ease",
    ))
    toggle = html.Div(
        id="sidebar-toggle-btn", n_clicks=0,
        children=[_icon(_ICO_CHEVRON_LEFT)],
        style=dict(position="absolute", right="0", top="30%",
                   transform="translate(100%, -50%)", zIndex="20",
                   width="16px", height="60px", background=BG_CARD,
                   border=f"1px solid {BORDER}", borderLeft="none",
                   borderRadius="0 8px 8px 0", cursor="pointer",
                   display="flex", alignItems="center", justifyContent="center",
                   transition="right 0.2s ease",
                   ),
    )
    return html.Div([content, toggle],
        style=dict(display="flex", position="relative",
                   flexShrink="0", height="100vh", overflow="visible"))


def topbar():
    # Header removed — these elements are kept (hidden) so the existing
    # navigate/refresh callbacks that target them still resolve.
    return html.Div([
        html.H1(id="page-title", children="Portfolio Overview"),
        html.Div(id="as-of-line"),
    ], style=dict(display="none"))


_NAV_ARROW_BASE_R = dict(
    position="absolute", right="14px", top="50%",
    transform="translateY(-50%)",
    width="36px", height="36px",
    background=BG_CARD, border=f"1px solid {BORDER}",
    borderRadius="50%",
    cursor="pointer", zIndex="20",
    display="none", alignItems="center", justifyContent="center",
    boxShadow="0 2px 12px rgba(0,0,0,0.35)",
)
_NAV_ARROW_BASE_L = dict(
    position="absolute", left="14px", top="50%",
    transform="translateY(-50%)",
    width="36px", height="36px",
    background=BG_CARD, border=f"1px solid {BORDER}",
    borderRadius="50%",
    cursor="pointer", zIndex="20",
    display="none", alignItems="center", justifyContent="center",
    boxShadow="0 2px 12px rgba(0,0,0,0.35)",
)


def main_content():
    return html.Main([
        topbar(),
        html.Div([
            html.Div(id="dashboard-page", children=dashboard_page()),
            html.Div(id="search-page", children=search_page(),
                     style=dict(display="none")),
            html.Div(id="holdings-page", children=holdings_page(),
                     style=dict(display="none")),
            html.Div(id="cgt-page", children=cgt_page(), style=dict(display="none")),
            html.Div(id="security-page", children=security_page(),
                     style=dict(display="none")),
            html.Div(id="optimize-page", children=optimize_page(),
                     style=dict(display="none")),
            html.Div(id="macro-page", children=macro_page(),
                     style=dict(display="none")),
            html.Div(id="markets-page", children=markets_page(),
                     style=dict(display="none")),
            html.Div(id="performance-page", children=performance_page(),
                     style=dict(display="none")),
            html.Div(id="backtest-page", children=backtest_page(),
                     style=dict(display="none")),
        ]),
        # Right-edge circle: dashboard → macro
        html.Div([_icon(_ICO_CHEVRON_RIGHT)],
                 id="dash-to-macro-btn", n_clicks=0,
                 style={**_NAV_ARROW_BASE_R, "display": "flex"}),
        # Left-edge circle: macro → dashboard
        html.Div([_icon(_ICO_CHEVRON_LEFT)],
                 id="macro-to-dash-btn", n_clicks=0,
                 style=dict(**_NAV_ARROW_BASE_L)),
        # Right-edge circle: macro → markets
        html.Div([_icon(_ICO_CHEVRON_RIGHT)],
                 id="macro-to-markets-btn", n_clicks=0,
                 style=dict(**_NAV_ARROW_BASE_R)),
        # Left-edge circle: markets → macro
        html.Div([_icon(_ICO_CHEVRON_LEFT)],
                 id="markets-to-macro-btn", n_clicks=0,
                 style=dict(**_NAV_ARROW_BASE_L)),
    ], style=dict(flex="1", minWidth="0", display="flex",
                  flexDirection="column", overflow="hidden",
                  overflowY="auto", position="relative"))


def build_layout():
    return html.Div([
        dcc.Store(id="range-store",     data="1Y"),
        dcc.Store(id="bench-store",     data=["asx"]),
        dcc.Store(id="sort-store",      data={"col":"value","asc":False}),
        dcc.Store(id="chart-mode-store",data="value"),
        dcc.Store(id="movers-mode-store",data="pct"),
        dcc.Store(id="page-store",      data="dashboard"),
        dcc.Store(id="cgt-data-store",  data=None),
        dcc.Store(id="sec-detail-store", data=None),
        dcc.Store(id="pending-search-store", data=None),
        dcc.Store(id="sec-select-store", data=None),
        # Per-page-load "have I rendered real content yet?" guards for the two
        # cards fed by background data. Memory storage → resets on a browser
        # reload but persists across in-app tab switches, so a fresh page always
        # fills these once data lands, while the globe isn't rebuilt (rotation
        # reset) on every dashboard revisit.
        dcc.Store(id="world-map-built", data=False),
        dcc.Store(id="metrics-built",   data=False),
        dcc.Store(id="page-transition-dummy"),
        dcc.Interval(id="pending-search-timer",       interval=3600000, n_intervals=0),
        dcc.Interval(id="refresh",                    interval=REFRESH*1000, n_intervals=0),
        dcc.Interval(id="globe-rotate",               interval=100,    n_intervals=0),
        dcc.Interval(id="portfolio-metrics-refresh",  interval=600_000, n_intervals=0),
        html.Div(id="globe-rotate-dummy", style=dict(display="none")),
        html.Div(id="sidebar-resize-dummy", style=dict(display="none")),
        html.Div([sidebar(), main_content()],
            style=dict(display="flex",height="100vh",overflow="hidden",
                       background=BG,color=T1,
                       fontFamily="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif",
                       fontSize="14px",WebkitFontSmoothing="antialiased",
                       letterSpacing="0.1px")),
    ], style=dict(margin="0",padding="0"))


# --- Navigation / chrome callbacks -----------------------------------

_PAGE_TITLES = {
    "nav-dashboard":   "Portfolio Overview",
    "nav-search":      "Search Securities",
    "nav-holdings":    "Holdings",
    "nav-performance": "Performance",
    "nav-backtest":    "Backtest",
    "nav-cgt":         "Capital Gains Tax Report",
    "nav-optimize":    "Portfolio Optimization",
    "nav-macro":       "Macro Overview",
    "nav-markets":     "Daily Market Summary",
    "security":        "Security Detail",
}

_NAV_ICONS = {
    "nav-dashboard":   _ICO_GRID,
    "nav-search":      _ICO_SEARCH,
    "nav-holdings":    _ICO_LIST,
    "nav-performance": _ICO_TREND,
    "nav-backtest":    _ICO_CLOCK,
    "nav-cgt":         _ICO_DOC,
    "nav-optimize":    _ICO_TARGET,
    "nav-macro":       _ICO_GLOBE,
    "nav-markets":     _ICO_GLOBE,
}


@app.callback(
    Output("page-store","data"),
    Output("page-title","children"),
    Output("nav-dashboard","style"),
    Output("nav-search","style"),
    Output("nav-holdings","style"),
    Output("nav-performance","style"),
    Output("nav-backtest","style"),
    Output("nav-cgt","style"),
    Output("nav-optimize","style"),
    Output("nav-macro","style"),
    Output("nav-markets","style"),
    Output({"type":"nav-icon","index":"nav-dashboard"},"src"),
    Output({"type":"nav-icon","index":"nav-search"},"src"),
    Output({"type":"nav-icon","index":"nav-holdings"},"src"),
    Output({"type":"nav-icon","index":"nav-performance"},"src"),
    Output({"type":"nav-icon","index":"nav-backtest"},"src"),
    Output({"type":"nav-icon","index":"nav-cgt"},"src"),
    Output({"type":"nav-icon","index":"nav-optimize"},"src"),
    Output({"type":"nav-icon","index":"nav-macro"},"src"),
    Output({"type":"nav-icon","index":"nav-markets"},"src"),
    Output("dashboard-page","style"),
    Output("search-page","style"),
    Output("holdings-page","style"),
    Output("cgt-page","style"),
    Output("security-page","style"),
    Output("optimize-page","style"),
    Output("macro-page","style"),
    Output("markets-page","style"),
    Output("performance-page","style"),
    Output("backtest-page","style"),
    Input("nav-dashboard","n_clicks"),
    Input("nav-search","n_clicks"),
    Input("nav-holdings","n_clicks"),
    Input("nav-performance","n_clicks"),
    Input("nav-backtest","n_clicks"),
    Input("nav-cgt","n_clicks"),
    Input("nav-optimize","n_clicks"),
    Input("nav-macro","n_clicks"),
    Input("nav-markets","n_clicks"),
    Input("sec-detail-store","data"),
    prevent_initial_call=True,
)
def navigate(*args):
    from dash import ctx
    triggered = ctx.triggered_id
    if not triggered:
        return (no_update,) * 30
    if triggered == "sec-detail-store":
        sec_val = args[-1] if args else None
        print(f"[NAV] sec-detail-store triggered, value={sec_val!r}")
        if not sec_val:
            print("[NAV] sec-detail-store is empty, staying on current page")
            return (no_update,) * 30
        page = "security"
        print(f"[NAV] navigating to security page for: {sec_val}")
    else:
        page = triggered
        print(f"[NAV] triggered: {triggered}, page={page}")
    base_nav = dict(display="flex", alignItems="center", gap="11px",
                    padding="9px 12px", borderRadius="9px",
                    textDecoration="none", fontWeight="500", cursor="pointer",
                    position="relative")
    active_style = dict(**base_nav, color=T1,
                         background="rgba(54,211,153,0.11)",
                         boxShadow="none")
    inactive_style = dict(**base_nav, color=T2,
                           background="transparent", boxShadow="none")
    nav_ids = ["nav-dashboard","nav-search","nav-holdings","nav-performance",
               "nav-backtest","nav-cgt","nav-optimize","nav-macro","nav-markets"]
    highlight = "nav-dashboard" if page in ("nav-macro", "nav-markets") else page
    nav_styles = [active_style if n == highlight else inactive_style for n in nav_ids]
    active_color = ACCENT
    inactive_color = "#9aa0ad"
    icon_srcs = [
        _icon_src(_NAV_ICONS[n], active_color if n == highlight else inactive_color)
        for n in nav_ids
    ]
    title        = _PAGE_TITLES.get(page, "Portfolio Overview")
    dash_style   = dict() if page == "nav-dashboard" else dict(display="none")
    search_style = dict() if page == "nav-search"    else dict(display="none")
    hold_style   = dict() if page == "nav-holdings"  else dict(display="none")
    cgt_style    = dict() if page == "nav-cgt"       else dict(display="none")
    sec_style    = dict() if page == "security"      else dict(display="none")
    opt_style    = dict() if page == "nav-optimize"  else dict(display="none")
    macro_style  = dict() if page == "nav-macro"     else dict(display="none")
    markets_style = dict() if page == "nav-markets"  else dict(display="none")
    perf_style   = dict() if page == "nav-performance" else dict(display="none")
    back_style   = dict() if page == "nav-backtest"    else dict(display="none")
    return (page, title, *nav_styles, *icon_srcs,
            dash_style, search_style, hold_style, cgt_style, sec_style,
            opt_style, macro_style, markets_style, perf_style, back_style)


@app.callback(
    Output("sidebar-el","style"),
    Output("sidebar-toggle-btn","children"),
    Input("sidebar-toggle-btn","n_clicks"),
    prevent_initial_call=True,
)
def toggle_sidebar(n_clicks):
    collapsed = n_clicks % 2 == 1
    base = dict(
        flexShrink="0", display="flex", flexDirection="column",
        background=BG_SIDE, overflow="hidden",
        transition="width 0.2s ease",
    )
    if collapsed:
        base["width"] = "0px"
        icon = _icon(_ICO_CHEVRON_RIGHT)
    else:
        base["width"] = "236px"
        icon = _icon(_ICO_CHEVRON_LEFT)
    return base, [icon]


# Collapsing / expanding the sidebar resizes the main content, but Plotly only
# recomputes chart size on a window resize. After the 0.2s width transition,
# dispatch a resize event so the charts re-fit their (now-resized) containers
# instead of staying stuck at their previous width.
app.clientside_callback(
    """
    function(n) {
        setTimeout(function () {
            window.dispatchEvent(new Event('resize'));
        }, 260);
        return '';
    }
    """,
    Output("sidebar-resize-dummy", "children"),
    Input("sidebar-toggle-btn", "n_clicks"),
    prevent_initial_call=True,
)


# --- Nav arrow visibility & routing ----------------------------------

@app.callback(
    Output("dash-to-macro-btn", "style"),
    Output("macro-to-dash-btn", "style"),
    Output("macro-to-markets-btn", "style"),
    Output("markets-to-macro-btn", "style"),
    Input("page-store", "data"),
)
def update_nav_arrows(page):
    # Horizontal flow:  Dashboard  ⇄  Macro  ⇄  Markets
    show_r = {**_NAV_ARROW_BASE_R, "display": "flex"}
    show_l = {**_NAV_ARROW_BASE_L, "display": "flex"}
    hide   = dict(display="none")
    on_dash    = page in ("dashboard", "nav-dashboard")
    on_macro   = page == "nav-macro"
    on_markets = page == "nav-markets"
    return (
        show_r if on_dash    else hide,   # dashboard → macro
        show_l if on_macro   else hide,   # macro → dashboard
        show_r if on_macro   else hide,   # macro → markets
        show_l if on_markets else hide,   # markets → macro
    )


@app.callback(
    Output("nav-macro", "n_clicks"),
    Input("dash-to-macro-btn", "n_clicks"),
    State("nav-macro", "n_clicks"),
    prevent_initial_call=True,
)
def route_to_macro(n, current):
    if n:
        return (current or 0) + 1
    return no_update


@app.callback(
    Output("nav-dashboard", "n_clicks"),
    Input("macro-to-dash-btn", "n_clicks"),
    State("nav-dashboard", "n_clicks"),
    prevent_initial_call=True,
)
def route_to_dashboard(n, current):
    if n:
        return (current or 0) + 1
    return no_update


@app.callback(
    Output("nav-markets", "n_clicks"),
    Input("macro-to-markets-btn", "n_clicks"),
    State("nav-markets", "n_clicks"),
    prevent_initial_call=True,
)
def route_to_markets(n, current):
    if n:
        return (current or 0) + 1
    return no_update


@app.callback(
    Output("nav-macro", "n_clicks", allow_duplicate=True),
    Input("markets-to-macro-btn", "n_clicks"),
    State("nav-macro", "n_clicks"),
    prevent_initial_call=True,
)
def route_markets_to_macro(n, current):
    if n:
        return (current or 0) + 1
    return no_update


# Smooth page-enter animation for dashboard ↔ macro transitions.
# Fires client-side whenever either page's display style changes (set by
# navigate()). Uses double-rAF so the opacity-0/translate is painted
# before the transition kicks in, giving a genuine fade+slide-in effect.
app.clientside_callback(
    ClientsideFunction(namespace="pageTransition", function_name="animatePageChange"),
    Output("page-transition-dummy", "data"),
    Input("macro-page",     "style"),
    Input("dashboard-page", "style"),
    Input("markets-page",   "style"),
)


# --- Global data-refresh callback ------------------------------------

# Number of Outputs the ``refresh`` callback declares, so the off-tab short
# circuit can return a correctly-sized no_update tuple. Keep in sync with the
# Output list below:
#   range styles + disabled (2·ranges) + mode (2) + movers (2) + bench + chart
#   block (6) + donut/legend (2) + holdings (3) + movers-rows (1) + income (3)
#   + news (1) + status (2)
_N_REFRESH_OUTPUTS = 2 * len(_RANGES) + 2 + 2 + len(_BENCH) + 6 + 2 + 3 + 1 + 3 + 1 + 2

@app.callback(
    # Range button styles + disabled
    *[Output({"type":"range-btn","index":r},"style") for r,_ in _RANGES],
    *[Output({"type":"range-btn","index":r},"disabled") for r,_ in _RANGES],
    # Mode button styles
    *[Output({"type":"mode-btn","index":m},"style") for m in ("value","pct")],
    # Movers button styles
    *[Output({"type":"movers-btn","index":m},"style") for m in ("value","pct")],
    # Bench button styles
    *[Output({"type":"bench-btn","index":k},"style") for k in _BENCH],
    # Main chart
    Output("main-chart","figure"),
    Output("chart-val","children"),
    Output("chart-ret","children"),
    Output("chart-ret","style"),
    Output("chart-period","children"),
    Output("chart-title","children"),
    # Donut + legend
    Output("donut-chart","figure"),
    Output("alloc-legend","children"),
    # Holdings
    Output("holdings-header","children"),
    Output("holdings-rows","children"),
    Output("holdings-count","children"),
    # Movers
    Output("movers-rows","children"),
    # Income
    Output("income-total","children"),
    Output("income-bars","children"),
    Output("dividend-rows","children"),
    # News
    Output("news-rows","children"),
    # Status
    Output("as-of-line","children"),
    Output("acct-sub","children"),
    Input("refresh","n_intervals"),
    Input("range-store","data"),
    Input("bench-store","data"),
    Input("sort-store","data"),
    Input("chart-mode-store","data"),
    Input("return-basis-dropdown","value"),
    Input("movers-mode-store","data"),
    Input("page-store","data"),
)
def refresh(_, range_key, active_bench, sort_state, chart_mode, return_basis, movers_mode, page):
    # Every output of this callback lives on either the Dashboard tab (chart,
    # donut, movers, income, news) or the Holdings tab (holdings-header/rows/
    # count). Hidden pages stay in the DOM, so on any other tab this whole
    # rebuild — get_data() plus assembling every holdings row and sparkline —
    # is wasted work that serialises ahead of the navigation callback. Skip it
    # unless one of those two tabs is showing. page-store is an Input (not
    # State) so switching back to Dashboard/Holdings triggers an immediate
    # rebuild rather than waiting up to a full refresh interval.
    if page not in ("dashboard", "nav-dashboard", "nav-holdings"):
        return (no_update,) * _N_REFRESH_OUTPUTS
    _t_refresh = time.perf_counter()
    d = get_data()
    range_key = range_key or "1Y"
    active_bench = active_bench or ["asx"]
    return_basis = return_basis if return_basis in ("money", "time") else "money"
    chart_series = d.port_series_time if return_basis == "time" and d.port_series_time else d.port_series
    chart_dates = d.port_dates_time if return_basis == "time" and d.port_dates_time else d.port_dates

    # -- range button styles
    avail = len(chart_series)
    _range_days = dict(_RANGES)

    # If the selected range needs more data than we have, fall back to the
    # longest enabled range (ALL is always enabled so this always finds one).
    if range_key != "ALL" and _range_days.get(range_key, 0) > avail:
        enabled_keys = [r for r, days in _RANGES if r == "ALL" or days <= avail]
        range_key = enabled_keys[-1] if enabled_keys else "ALL"

    range_styles    = []
    range_disabled  = []
    for r, days in _RANGES:
        enabled = (r == "ALL") or (days <= avail)
        sel     = enabled and (r == range_key)
        range_disabled.append(True if not enabled else None)
        range_styles.append(dict(
            border="none",
            background="rgba(255,255,255,0.08)" if sel else "transparent",
            color=T1 if sel else (T3 if enabled else T5),
            fontFamily="'JetBrains Mono',monospace",fontSize="12px",fontWeight="600",
            padding="5px 10px",borderRadius="7px",
            cursor="pointer" if enabled else "not-allowed",
            opacity="1" if enabled else "0.35"))

    # -- mode button styles
    chart_mode  = chart_mode or "value"
    _btn_base   = dict(border="none",fontFamily="'JetBrains Mono',monospace",
                       fontSize="12px",fontWeight="600",padding="5px 10px",
                       borderRadius="7px",cursor="pointer")
    mode_styles = [
        {**_btn_base, "background":"rgba(255,255,255,0.08)" if chart_mode=="value" else "transparent",
                      "color": T1 if chart_mode=="value" else T3},
        {**_btn_base, "background":"rgba(255,255,255,0.08)" if chart_mode=="pct"   else "transparent",
                      "color": T1 if chart_mode=="pct"   else T3},
    ]

    # -- movers button styles
    movers_mode = movers_mode or "pct"
    _movers_btn_base = dict(border="none",fontFamily="'JetBrains Mono',monospace",
                            fontSize="11px",fontWeight="600",padding="3px 7px",
                            borderRadius="5px",cursor="pointer")
    movers_btn_styles = [
        {**_movers_btn_base, "background":"rgba(255,255,255,0.08)" if movers_mode=="value" else "transparent",
                             "color": T1 if movers_mode=="value" else T3},
        {**_movers_btn_base, "background":"rgba(255,255,255,0.08)" if movers_mode=="pct"   else "transparent",
                             "color": T1 if movers_mode=="pct"   else T3},
    ]

    # -- bench button styles
    bench_styles = []
    for k, bm in _BENCH.items():
        on = k in active_bench
        bench_styles.append(dict(
            display="flex",alignItems="center",gap="6px",
            border=f"1px solid {'rgba(255,255,255,0.13)' if on else 'rgba(255,255,255,0.07)'}",
            background="rgba(255,255,255,0.06)" if on else "transparent",
            color=T1 if on else T2,
            fontSize="12px",fontWeight="500",padding="5px 9px",borderRadius="8px",
            cursor="pointer",fontFamily="inherit"))

    # -- main chart
    chart_fig = build_main_chart(chart_series, range_key, active_bench, chart_dates, d.bench_data, chart_mode, return_basis, d.cash_flows)
    loading = not chart_series
    n = min(dict(_RANGES).get(range_key,260), len(chart_series))
    sl = chart_series[-n:] if chart_series else []
    range_ret = (sl[-1]/sl[0]-1)*100 if len(sl) > 1 and sl[0] else 0
    _rlabels = {"1M":"month","3M":"3 months","6M":"6 months","1Y":"year"}
    if loading:
        rlabel = "loading..."
    elif range_key == "ALL":
        _yrs = round(avail / 260)
        rlabel = f"{_yrs} year{'s' if _yrs != 1 else ''}" if _yrs >= 1 else "available history"
    else:
        rlabel = _rlabels.get(range_key, "year")
    chart_ret_style = dict(fontFamily="'JetBrains Mono',monospace",fontSize="13.5px",
                           fontWeight="600",color=pnl_c(range_ret))
    if return_basis == "time" and len(sl) > 1:
        chart_value = sl[-1] - sl[0]
    else:
        chart_value = chart_series[-1] if chart_series else d.total
    basis_label = "Return" if return_basis == "time" else "Value"

    # -- load dividend data once for income KPI + bars + upcoming
    divs_data = _div_cache.get("data")

    # -- holdings sort + header
    sort_state  = sort_state or {"col":"value","asc":False}
    sort_col    = sort_state.get("col","value")
    sort_asc    = sort_state.get("asc",False)
    holdings_sorted = sorted(
        d.holdings,
        key=lambda h: (h.get(sort_col) or 0) if sort_col != "ticker"
                      else (h.get("ticker") or "").lower(),
        reverse=not sort_asc,
    )
    holdings_header = _build_holdings_header(sort_col, sort_asc)

    # -- holdings rows
    rows = []
    for h in holdings_sorted:
        color = pnl_c(h["ret"])
        day_c = pnl_c(h["day_pct"])
        rows.append(html.Div([
            html.Div([
                html.Span(style=dict(width="8px",height="8px",borderRadius="3px",
                                     flexShrink="0",background=h["color"],
                                     display="inline-block")),
                html.Div(
                    html.Div(h["ticker"], title=h["name"], style=dict(
                        fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                        fontWeight="600",letterSpacing="0.3px",
                        cursor="pointer",whiteSpace="nowrap",overflow="hidden",
                        textOverflow="ellipsis")),
                id={"type":"sec-ticker","index":h["ticker"]}, n_clicks=0,
                style=dict(minWidth="0")),
            ], style=dict(display="flex",alignItems="center",gap="11px",
                          flex="1.7",minWidth="0",overflow="hidden")),
            html.Div(pct_fmt(h["weight"]*100, dp=1),
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                           color=T2,textAlign="right",flex="0.8")),
            html.Div(money(h["value"]),
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                           fontWeight="500",textAlign="right",flex="1")),
            html.Div(pct_fmt(h["day_pct"],signed=True),
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                           fontWeight="600",textAlign="right",flex="0.85",color=day_c)),
            html.Div([
                html.Div(pct_fmt(h["ret"]*100,signed=True),
                    style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                               fontWeight="600",color=color)),
                html.Div(money(h["ret_dollar"],signed=True),
                    style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="11px",color=T4)),
            ], style=dict(textAlign="right",flex="1.15")),
            html.Div(pct_fmt(h.get("div_yield",0), dp=1),
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                           fontWeight="500",textAlign="right",flex="0.7",
                           color=ACCENT if h.get("div_yield",0) >= 3 else T3)),
            html.Div(spark(h["series"],color,w=70,h=28),
                style=dict(display="flex",justifyContent="flex-end",
                           width="70px",flexShrink="0")),
        ], style=dict(display="flex",alignItems="center",gap="8px",
                      padding="12px 20px",borderBottom=f"1px solid rgba(255,255,255,0.04)")))

    # -- movers
    movers_mode = movers_mode if movers_mode in ("pct", "value") else "pct"
    non_cash = [h for h in d.holdings if h["ticker"]!="CASH"]
    if movers_mode == "value":
        by_day = sorted(non_cash, key=lambda h: h["day_dollar"], reverse=True)
        def _mover_val(h): return h["day_dollar"]
        def _mover_fmt(h): return money(h["day_dollar"], signed=True)
        def _mover_color(h): return pnl_c(h["day_dollar"])
    else:
        by_day = sorted(non_cash, key=lambda h: h["day_pct"], reverse=True)
        def _mover_val(h): return h["day_pct"]
        def _mover_fmt(h): return pct_fmt(h["day_pct"], signed=True)
        def _mover_color(h): return pnl_c(h["day_pct"])
    best5 = by_day[:5]
    worst5 = by_day[-5:]
    max_abs_best = max((abs(_mover_val(h)) for h in best5), default=1) or 1
    max_abs_worst = max((abs(_mover_val(h)) for h in worst5), default=1) or 1
    def _mover_row(h, scale):
        val = _mover_val(h)
        frac = abs(val) / scale * 50
        bar_left = "50%" if val >= 0 else f"{50-frac:.1f}%"
        bar_w    = f"{frac:.1f}%"
        bar_col  = ACCENT if val >= 0 else RED
        return html.Div([
            html.Div(h["ticker"],
                id={"type":"sec-ticker","index":h["ticker"]}, n_clicks=0,
                style=dict(
                fontFamily="'JetBrains Mono',monospace",fontSize="12.5px",
                fontWeight="600",width="42px",cursor="pointer")),
            html.Div([
                html.Div(style=dict(position="absolute",top="0",bottom="0",
                    left=bar_left,width=bar_w,background=bar_col,borderRadius="3px")),
            ], style=dict(flex="1",height="5px",borderRadius="3px",
                background="rgba(255,255,255,0.05)",overflow="hidden",position="relative")),
            html.Div(_mover_fmt(h),
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="12.5px",
                    fontWeight="600",width="58px",textAlign="right",
                    color=_mover_color(h))),
        ], style=dict(display="flex",alignItems="center",gap="11px"))
    mover_rows = [html.Div("Best",style=dict(fontSize="10px",color=T5,fontWeight="600",
        letterSpacing="0.5px",textTransform="uppercase",marginBottom="-4px"))]
    mover_rows += [_mover_row(h, max_abs_best) for h in best5]
    mover_rows.append(html.Div(style=dict(borderTop="1px solid rgba(255,255,255,0.06)",
        margin="8px 0")))
    mover_rows.append(html.Div("Worst",style=dict(fontSize="10px",color=T5,fontWeight="600",
        letterSpacing="0.5px",textTransform="uppercase",marginBottom="-4px")))
    mover_rows += [_mover_row(h, max_abs_worst) for h in worst5]

    # -- income bars
    if divs_data is not None and not divs_data.empty and "amount" in divs_data.columns and "datetime" in divs_data.columns:
        import datetime as _dt
        tmp = divs_data.copy()
        tmp["month"] = pd.to_datetime(tmp["datetime"], errors="coerce").dt.to_period("M")
        monthly = tmp.groupby("month")["amount"].sum().sort_index()
        bar_vals = monthly.tolist()[-8:] if len(monthly) > 8 else monthly.tolist()
        bar_labels = [p.strftime("%b") for p in monthly.index[-8:]] if len(monthly) > 8 else [p.strftime("%b") for p in monthly.index]
    else:
        bar_labels = []
        bar_vals = []
    mx = max(bar_vals) if bar_vals else 1
    income_bars = [
        html.Div([
            html.Div(style=dict(width="100%",borderRadius="4px 4px 2px 2px",
                background=ACCENT if i==len(bar_vals)-1 else "rgba(255,255,255,0.14)",
                height=f"{v/mx*100:.0f}%")),
            html.Div(l,style=dict(fontSize="10px",color=T5)),
        ], style=dict(flex="1",display="flex",flexDirection="column",
                      alignItems="center",gap="6px",height="100%",justifyContent="flex-end"))
        for i,(l,v) in enumerate(zip(bar_labels,bar_vals))
    ]

    # -- dividends
    divs_data = _div_cache.get("data")
    if divs_data is not None and not divs_data.empty and "symbol" in divs_data.columns:
        import datetime as _dt
        now = _dt.datetime.now()
        dt_col = "datetime" if "datetime" in divs_data.columns else "date"
        upcoming = divs_data.copy()
        upcoming["_dt"] = pd.to_datetime(upcoming[dt_col], errors="coerce")
        upcoming = upcoming[upcoming["_dt"] >= now].sort_values("_dt")
        if not upcoming.empty and "amount" in upcoming.columns:
            # Aggregate by symbol + settle date to deduplicate
            date_col = "settleDate" if "settleDate" in upcoming.columns else dt_col
            upcoming["_date_key"] = pd.to_datetime(upcoming[date_col], errors="coerce").dt.date
            agg = upcoming.groupby(["symbol", "_date_key"]).agg(
                total_amount=("amount", "sum"),
                pay_date=("_dt", "min"),
            ).reset_index().sort_values("pay_date").head(8)
            divs = []
            for _, row in agg.iterrows():
                sym = str(row["symbol"])
                amt = float(row["total_amount"])
                pay_dt = row["pay_date"]
                if hasattr(pay_dt, "strftime"):
                    note = pay_dt.strftime("Ex-div %d %b") if pay_dt.date() > now.date() else pay_dt.strftime("Pays %d %b")
                else:
                    note = str(pay_dt)[:16]
                divs.append((sym, note, money(amt)))
        else:
            divs = []
    else:
        divs = []
    div_rows = [
        html.Div([
            html.Div(t,style=dict(fontFamily="'JetBrains Mono',monospace",
                fontSize="12.5px",fontWeight="600",width="42px")),
            html.Div(note,style=dict(fontSize="12px",color=T3)),
            html.Div(amt,style=dict(marginLeft="auto",
                fontFamily="'JetBrains Mono',monospace",
                fontSize="12.5px",fontWeight="600",color="#c5cad3")),
        ], style=dict(display="flex",alignItems="center",gap="10px"))
        for t,note,amt in divs
    ]

    # News
    news_items = _fetch_news()
    if news_items:
        cols = [[], [], []]
        for i, item in enumerate(news_items[:9]):
            title_text = item["title"]
            a_style = dict(textDecoration="none", color=T1, fontSize="11px",
                           fontWeight="500", lineHeight="16px",
                           whiteSpace="nowrap", display="inline-block")
            ticker = html.Div([
                html.Div([
                    html.A(title_text, href=item["link"], target="_blank",
                           style=a_style),
                    html.A(title_text, href=item["link"], target="_blank",
                           style=a_style),
                ], className="news-ticker-inner"),
            ], className="news-ticker")
            date_div = html.Div(item["date"], className="news-date")
            cols[i % 3].append(
                html.Div([ticker, date_div], className="news-item"))
        news_rows = [html.Div(col, className="news-col") for col in cols]
    else:
        news_rows = [html.Div("No news available", style=dict(
            color=T4, fontSize="12px", gridColumn="1/-1"))]

    # -- allocation legend
    alloc_legend = [
        html.Div([
            html.Span(style=dict(width="9px",height="9px",borderRadius="3px",
                flexShrink="0",background=ALLOC_HEX.get(a["name"],"#888"),
                display="inline-block")),
            html.Span(a["name"],style=dict(fontSize="13px",color="#c5cad3")),
            html.Span(money(a["value"]),style=dict(marginLeft="auto",
                fontFamily="'JetBrains Mono',monospace",fontSize="12.5px",color=T3)),
            html.Span(pct_fmt(a["value"]/d.total*100 if d.total else 0,dp=1),
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="13px",
                    fontWeight="600",color=T1,width="46px",textAlign="right")),
        ], style=dict(display="flex",alignItems="center",gap="10px"))
        for a in d.allocation
    ]

    ts = time.strftime("%H:%M:%S")

    print(f"[PERF] refresh callback (page={page}): "
          f"{(time.perf_counter() - _t_refresh) * 1000:.0f}ms", flush=True)

    return (
        # range btn styles (6)
        *range_styles,
        # range btn disabled (6)
        *range_disabled,
        # mode btn styles (2)
        *mode_styles,
        # movers btn styles (2)
        *movers_btn_styles,
        # bench btn styles (3)
        *bench_styles,
        # chart
        chart_fig,
        money(chart_value, signed=return_basis == "time"),
        pct_fmt(range_ret, signed=True),
        chart_ret_style,
        f"past {rlabel} - {basis_label}",
        "Portfolio Return" if return_basis == "time" else "Portfolio Value",
        # Donut
        build_donut(d.allocation, d.total),
        alloc_legend,
        # Holdings
        holdings_header,
        rows,
        f"{len(d.holdings)} positions",
        # Movers
        mover_rows,
        # Income
        money(d.income),
        income_bars,
        div_rows,
        # News
        news_rows,
        # Status
        f"All accounts · AUD · as of {ts} AEST",
        "AUD · IBKR",
    )


# ── World map ─────────────────────────────────────────────────────────
# Rebuilt in its OWN callback (not the 10s refresh above) and only when the
# country data actually changes. Rebuilding replaces the globe's DOM node,
# which resets the client-side rotation to lon=0; returning no_update when the
# data is unchanged lets the globe rotate continuously without snapping back.
# Country performance is cached ~hourly, so in practice the map is built once
# per session and rotates seamlessly thereafter.
_world_map_sig: dict = {"v": None}

@app.callback(
    Output("world-map-body", "children"),
    Output("world-map-built", "data"),
    Input("refresh", "n_intervals"),
    State("world-map-built", "data"),
)
def _update_world_map(n, built):
    data = _get_country_perf()
    sig = tuple((r.get("code"), round(r.get("change_pct", 0.0), 2)) for r in data)
    # Render when either this page hasn't painted a real globe yet (``built`` is
    # a per-page-load memory store, so a fresh load always starts False), or the
    # country data actually changed. The signature is only used to avoid
    # rebuilding an already-painted globe on unchanged data — rebuilding replaces
    # the DOM node and resets the client-side rotation. ``built`` flips true only
    # once we actually have data, so an empty first tick keeps retrying instead
    # of latching a blank map until a manual reload.
    changed = sig != _world_map_sig["v"]
    if built and not changed:
        return no_update, no_update
    _world_map_sig["v"] = sig
    return _build_world_map(data), bool(data)


# ── Portfolio fundamentals card ───────────────────────────────────────

_metrics_sig: dict = {"v": None}

@app.callback(
    Output("portfolio-metrics-card", "children"),
    Output("metrics-built", "data"),
    Input("refresh", "n_intervals"),
    Input("portfolio-metrics-refresh", "n_intervals"),
    State("metrics-built", "data"),
)
def refresh_portfolio_metrics(n, _slow, built):
    _t_pm = time.perf_counter()
    d = get_data()
    try:
        metrics = get_portfolio_metrics(d.holdings)
    except Exception:
        metrics = {}
    try:
        risk = get_portfolio_risk_metrics()
    except Exception:
        risk = []
    print(f"[PERF] refresh_portfolio_metrics: "
          f"{(time.perf_counter() - _t_pm) * 1000:.0f}ms", flush=True)

    # Render when either this page hasn't shown real metrics yet (``built`` is a
    # per-page-load memory store, so a fresh load starts False) or the content
    # changed; skip otherwise so the 10s tick doesn't churn the DOM. ``built``
    # flips true only once real data is present, so an empty first tick keeps
    # retrying instead of latching the "Fetching…" placeholder until a manual
    # reload. yfinance info is cached ~6h and the risk maths run on in-memory
    # history, so the per-tick recompute is cheap after the first fetch.
    sig = (tuple(sorted((k, tuple(v)) for k, v in metrics.items())), tuple(risk))
    changed = sig != _metrics_sig["v"]
    if built and not changed:
        return no_update, no_update
    _metrics_sig["v"] = sig

    if not metrics and not risk:
        return html.Div([
            html.Div("Portfolio Metrics", style=dict(
                fontSize="13px", fontWeight="600", color=T2, marginBottom="6px")),
            html.Div("Fetching data…", style=dict(fontSize="12px", color=T4)),
        ], style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px 24px")), no_update

    def _row(label, value, last=False):
        dim = value == "—"
        return html.Div([
            html.Div(label, style=dict(
                fontSize="12.5px", color=T4, flex="1", minWidth="0")),
            html.Div(value, style=dict(
                fontSize="12.5px", fontWeight="600",
                fontFamily="'JetBrains Mono',monospace",
                color=T4 if dim else T1,
                marginLeft="12px", whiteSpace="nowrap")),
        ], style=dict(
            display="flex", justifyContent="space-between",
            alignItems="center", padding="5px 0",
            borderBottom="none" if last else "1px solid rgba(255,255,255,0.04)",
        ))

    def _section(title, rows):
        row_els = [_row(item[0], item[1], i == len(rows) - 1)
                   for i, item in enumerate(rows)]
        return html.Div([
            html.Div(title, style=dict(
                fontSize="10px", fontWeight="700", color=T5,
                letterSpacing="0.8px", textTransform="uppercase",
                marginBottom="10px")),
            *row_els,
        ], style=dict(flex="1", minWidth="0"))

    divider = html.Div(style=dict(
        width="1px", background="rgba(255,255,255,0.06)",
        margin="0 24px", alignSelf="stretch"))

    val_rows  = metrics.get("valuation", [])
    qual_rows = metrics.get("quality", [])

    all_covs  = [cov for rows in metrics.values() for _, v, cov in rows if v != "—"]
    cov_note  = f"~{sum(all_covs) // len(all_covs)}% coverage" if all_covs else ""
    sub_parts = ["Market-cap weighted · equity positions"]
    if cov_note:
        sub_parts.append(cov_note)

    cols = []
    if val_rows:
        cols.append(_section("Valuation", val_rows))
    if qual_rows:
        if cols:
            cols.append(divider)
        cols.append(_section("Profitability", qual_rows))
    if risk:
        if cols:
            cols.append(divider)
        cols.append(_section("Portfolio Risk", risk))

    return html.Div([
        html.Div([
            html.Div("Portfolio Metrics", style=dict(
                fontSize="13px", fontWeight="600", color=T2)),
            html.Div(" · ".join(sub_parts), style=dict(fontSize="11px", color=T5)),
        ], style=dict(display="flex", justifyContent="space-between",
                      alignItems="baseline", marginBottom="20px")),
        html.Div(cols, style=dict(display="flex", alignItems="flex-start")),
    ], style=dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding="20px 24px")), True
