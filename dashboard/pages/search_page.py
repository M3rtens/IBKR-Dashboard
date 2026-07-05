"""Search page: a landing-style centred search bar plus the security-lookup
callbacks (debounced lookup with bounded async retries, result selection).
"""

from dash import dcc, html, Input, Output, State, no_update, ALL

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, T1, T3, T4, T5, MARKET_DATA_SOURCE
from dashboard.icons import _icon_sm, _ICO_SEARCH


# ── yfinance search (synchronous, handles tickers + company names) ────

def _yf_search(query: str) -> list:
    """Return up to 8 matching securities from yfinance Search."""
    try:
        import yfinance as yf
        res = yf.Search(query, max_results=8, news_count=0)
        quotes = getattr(res, "quotes", None) or []
        out = []
        for q in quotes:
            if q.get("quoteType", "") not in ("EQUITY", "ETF"):
                continue
            sym = q.get("symbol", "")
            if not sym:
                continue
            out.append({
                "symbol": sym,
                # Yahoo's search API uses lowercase keys; older yfinance
                # versions surfaced camel-case ones, so accept both.
                "name":   (q.get("shortname") or q.get("longname")
                           or q.get("shortName") or q.get("longName") or sym),
                "exchange": q.get("exchDisp") or q.get("exchange") or "",
            })
        return out
    except Exception:
        return []


def search_page():
    """Landing-style search screen: a single large, centred search bar."""
    return html.Div([
        html.Div([
            html.Div("Search Securities", style=dict(
                fontFamily="'Space Grotesk',sans-serif", fontSize="30px",
                fontWeight="600", letterSpacing="0.2px", textAlign="center")),
            html.Div("Look up any symbol to view live pricing, performance and news",
                style=dict(fontSize="14px", color=T4, textAlign="center",
                           margin="8px 0 30px")),
            html.Div([
                html.Div([
                    _icon_sm(_ICO_SEARCH),
                    dcc.Input(id="sec-search-input", type="text",
                        placeholder="Search by symbol or name…", debounce=True,
                        n_submit=0, autoFocus=True, style=dict(
                            background="transparent", border="none",
                            outline="none", color=T1, fontSize="17px",
                            width="100%", fontFamily="inherit")),
                ], style=dict(display="flex", alignItems="center", gap="13px",
                    padding="17px 22px", borderRadius="14px",
                    border="1px solid rgba(255,255,255,0.10)",
                    background=BG_CARD,
                    boxShadow="0 10px 34px rgba(0,0,0,0.35)")),
                html.Div(id="sec-search-dropdown",
                    style=dict(display="none", position="absolute",
                        top="100%", left="0", right="0", marginTop="8px",
                        background="#181d27", border=f"1px solid {BORDER}",
                        borderRadius="12px", padding="6px 0",
                        boxShadow="0 8px 24px rgba(0,0,0,0.45)",
                        zIndex="101", maxHeight="320px", overflowY="auto")),
            ], style=dict(position="relative", width="100%")),
        ], style=dict(width="100%", maxWidth="560px")),
        html.Div(id="search-overlay", n_clicks=0,
            style=dict(display="none", position="fixed", top="0", left="0",
                right="0", bottom="0", zIndex="99", background="transparent")),
    ], style=dict(flex="1", display="flex", flexDirection="column",
                  alignItems="center", justifyContent="center",
                  padding="24px 28px", minHeight="calc(100vh - 140px)"))


# --- Security search callbacks ---------------------------------------

def _result_row(symbol: str, name: str, exchange: str):
    return html.Div([
        html.Div([
            html.Div(symbol, style=dict(
                fontWeight="600", fontSize="14px", color=T1, minWidth="60px")),
            html.Div(name, style=dict(
                fontSize="12.5px", color=T3, overflow="hidden",
                textOverflow="ellipsis", whiteSpace="nowrap")),
        ], style=dict(display="flex", flexDirection="column", gap="2px",
                      minWidth="0", overflow="hidden")),
        html.Div(exchange, style=dict(fontSize="11px", color=T5, flexShrink="0")),
    ], id={"type": "sec-result", "index": symbol},
       n_clicks=0,
       style=dict(display="flex", alignItems="center", gap="12px",
                  padding="9px 14px", cursor="pointer", transition="background 0.15s"),
       className="sec-result-row")


@app.callback(
    Output("pending-search-timer","interval"),
    Input("pending-search-store","data"),
    prevent_initial_call=True,
)
def toggle_search_timer(pending):
    return 800 if pending else 3600000


@app.callback(
    Output("sec-search-dropdown","children"),
    Output("sec-search-dropdown","style"),
    Output("pending-search-store","data"),
    Output("search-overlay","style"),
    Input("sec-search-input","n_submit"),
    Input("pending-search-timer","n_intervals"),
    Input("search-overlay","n_clicks"),
    Input("sec-select-store","data"),
    State("sec-search-input","value"),
    State("pending-search-store","data"),
    prevent_initial_call=True,
)
def on_sec_search(n_submit, n_intervals, overlay_clicks, select_data, value, pending):
    from dash import ctx
    triggered = ctx.triggered_id
    MAX_ATTEMPTS = 8
    overlay_show = dict(display="block", position="fixed", top="0", left="0",
                        right="0", bottom="0", zIndex="99", background="transparent")
    overlay_hide = dict(display="none")
    dropdown_hide = dict(display="none")
    dropdown_show = dict(
        display="block", position="absolute", top="100%", left="0",
        right="0", marginTop="4px", background="#181d27",
        border=f"1px solid {BORDER}", borderRadius="10px",
        padding="6px 0", boxShadow="0 8px 24px rgba(0,0,0,0.45)",
        zIndex="101", maxHeight="320px", overflowY="auto")

    if triggered == "search-overlay":
        return [], dropdown_hide, None, overlay_hide
    if triggered == "sec-select-store" and select_data and select_data.get("selected"):
        return [], dropdown_hide, None, overlay_hide

    # ── IBKR retry path (fallback only) ──────────────────────────────
    if triggered == "pending-search-timer":
        if not isinstance(pending, dict) or not pending.get("symbol"):
            return [], dropdown_hide, None, overlay_hide
        symbol   = pending["symbol"]
        attempts = pending.get("attempts", 0)
        from services.ibkr_client import get_client
        client = get_client()
        info = client.lookup_security(symbol)
        if info:
            if MARKET_DATA_SOURCE == "ibkr":
                client.get_security_detail(info["symbol"])
            row = _result_row(info["symbol"], info["name"], info.get("exchange", ""))
            return [row], dropdown_show, None, overlay_show
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            return [html.Div(f'No results for "{symbol}"', style=dict(
                padding="10px 14px", color=T4, fontSize="13px"))], \
                dropdown_show, None, overlay_show
        return [html.Div("Searching…", style=dict(
            padding="10px 14px", color=T4, fontSize="13px"))], \
            dropdown_show, {"symbol": symbol, "attempts": attempts}, overlay_show

    # ── New search ────────────────────────────────────────────────────
    if triggered != "sec-search-input":
        return no_update, no_update, no_update, no_update
    if not value or not value.strip():
        return [], dropdown_hide, None, overlay_hide

    query = value.strip()

    # Try yfinance Search first — handles both tickers and company names
    results = _yf_search(query)
    if results:
        # Pre-warm the IBKR detail cache for the top result (only when IBKR is
        # the market-data source; in yfinance mode the detail page fetches on
        # click and this would just time out).
        if MARKET_DATA_SOURCE == "ibkr":
            from services.ibkr_client import get_client
            get_client().get_security_detail(results[0]["symbol"])
        rows = [_result_row(r["symbol"], r["name"], r["exchange"]) for r in results]
        return rows, dropdown_show, None, overlay_show

    # yfinance found nothing — fall back to IBKR async lookup
    symbol = query.upper()
    from services.ibkr_client import get_client
    client = get_client()
    info = client.lookup_security(symbol)
    if info:
        if MARKET_DATA_SOURCE == "ibkr":
            client.get_security_detail(info["symbol"])
        row = _result_row(info["symbol"], info["name"], info.get("exchange", ""))
        return [row], dropdown_show, None, overlay_show
    return [html.Div("Searching…", style=dict(
        padding="10px 14px", color=T4, fontSize="13px"))], \
        dropdown_show, {"symbol": symbol, "attempts": 0}, overlay_show


@app.callback(
    Output("sec-search-input","value"),
    Output("sec-detail-store","data"),
    Output("sec-select-store","data"),
    Input({"type":"sec-result","index":ALL},"n_clicks"),
    Input({"type":"sec-ticker","index":ALL},"n_clicks"),
    prevent_initial_call=True,
)
def on_sec_select(*_):
    from dash import ctx
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update, no_update, no_update
    symbol = triggered.get("index", "")
    if not symbol or symbol == "CASH":
        return no_update, no_update, no_update
    triggered_prop = ctx.triggered[0] if ctx.triggered else {}
    val = triggered_prop.get("value", 0)
    if isinstance(val, list):
        clicked = any(v > 0 for v in val if isinstance(v, (int, float)))
    else:
        clicked = isinstance(val, (int, float)) and val > 0
    if not clicked:
        return no_update, no_update, no_update
    clear = "" if triggered.get("type") == "sec-result" else no_update
    return clear, symbol, {"selected": symbol}
