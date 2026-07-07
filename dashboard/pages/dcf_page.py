"""DCF Model page – editable projection table with valuation summary."""

import time

from dash import dcc, html, Input, Output, State, no_update, ALL

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5
from dashboard.formatters import ccy_symbol


# ── yfinance fetch (reuses security_page cache when possible) ────────

def _fetch_yf_info(symbol: str) -> dict:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.info or {}
        income = t.income_stmt
        balance = t.balance_sheet
        cashflow = t.cashflow
        return {"info": info, "income": income, "balance": balance, "cashflow": cashflow}
    except Exception:
        return {}


def _df_val(df, *names):
    """Get the most recent value from a yfinance DataFrame for one of the given row names."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return None
    for n in names:
        if n in df.index:
            row = df.loc[n]
            if len(row) > 0:
                return float(row.iloc[0])
    return None


# ── Formatters ───────────────────────────────────────────────────────

def _fmt_money(v, sym="$"):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    sign = "-" if f < 0 else ""
    a = abs(f)
    if a >= 1e12: return f"{sign}{sym}{a/1e12:.2f}T"
    if a >= 1e9:  return f"{sign}{sym}{a/1e9:.2f}B"
    if a >= 1e6:  return f"{sign}{sym}{a/1e6:.1f}M"
    if a >= 1e3:  return f"{sign}{sym}{a/1e3:.1f}K"
    return f"{sign}{sym}{a:.2f}"


def _fmt_plain(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    return f"{f:,.0f}"


# ── Layout ───────────────────────────────────────────────────────────

def _toggle_group(*buttons):
    return html.Div(buttons, style=dict(
        display="flex", gap="2px",
        background="rgba(255,255,255,0.04)",
        borderRadius="7px", padding="2px"))


def _input_cell(placeholder, value=None, input_id=None):
    return dcc.Input(
        id=input_id, type="number", placeholder=placeholder,
        value=value, debounce=True, step=1,
        style=dict(
            background="rgba(255,255,255,0.04)",
            border="1px solid rgba(255,255,255,0.08)",
            borderRadius="5px",
            color=T1,
            fontFamily="'JetBrains Mono',monospace",
            fontSize="12.5px",
            padding="6px 8px",
            width="100%",
            textAlign="right",
            outline="none"),
    )


def _calc_cell(text):
    return html.Div(text, style=dict(
        fontSize="12.5px", color=T2, textAlign="right",
        fontFamily="'JetBrains Mono',monospace",
        padding="6px 8px"))


def dcf_page():
    period_store = dcc.Store(id="dcf-period-store", data="5Y")

    # ── Assumptions card ──
    assumptions_card = html.Div([
        html.Div("Assumptions", style=dict(
            fontSize="13px", fontWeight="600", color=T2, marginBottom="14px")),
        html.Div([
            html.Div([
                html.Div("WACC (%)", style=dict(fontSize="10.5px", color=T4,
                    fontWeight="500", letterSpacing="0.3px", marginBottom="6px")),
                _input_cell("10.0", value=10, input_id="dcf-wacc"),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Terminal Growth (%)", style=dict(fontSize="10.5px", color=T4,
                    fontWeight="500", letterSpacing="0.3px", marginBottom="6px")),
                _input_cell("2.5", value=2.5, input_id="dcf-tgr"),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Tax Rate (%)", style=dict(fontSize="10.5px", color=T4,
                    fontWeight="500", letterSpacing="0.3px", marginBottom="6px")),
                _input_cell("25.0", value=25, input_id="dcf-tax"),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Shares Out (M)", style=dict(fontSize="10.5px", color=T4,
                    fontWeight="500", letterSpacing="0.3px", marginBottom="6px")),
                _input_cell("1000", value=1000, input_id="dcf-shares"),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Net Debt (M)", style=dict(fontSize="10.5px", color=T4,
                    fontWeight="500", letterSpacing="0.3px", marginBottom="6px")),
                _input_cell("0", value=0, input_id="dcf-net-debt"),
            ], style=dict(flex="1")),
        ], style=dict(display="flex", gap="14px")),
    ], style=dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding="20px"))

    # ── Period toggle ──
    period_toggle = html.Div([
        html.Div("Projection Period", style=dict(
            fontSize="13px", fontWeight="600", color=T2, marginRight="14px")),
        _toggle_group(
            html.Button("3Y", id={"type": "dcf-period-btn", "index": "3Y"},
                n_clicks=0, className="sec-range-btn"),
            html.Button("5Y", id={"type": "dcf-period-btn", "index": "5Y"},
                n_clicks=0, className="sec-range-btn active"),
            html.Button("10Y", id={"type": "dcf-period-btn", "index": "10Y"},
                n_clicks=0, className="sec-range-btn"),
        ),
    ], style=dict(display="flex", alignItems="center"))

    # ── Projection table (filled by callback) ──
    projection_card = html.Div([
        html.Div("Revenue & Cost Projections", style=dict(
            fontSize="13px", fontWeight="600", color=T2, marginBottom="14px")),
        html.Div(id="dcf-projection-table"),
    ], style=dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding="20px"))

    # ── Valuation summary (filled by callback) ──
    valuation_card = html.Div([
        html.Div("Valuation Summary", style=dict(
            fontSize="13px", fontWeight="600", color=T2, marginBottom="14px")),
        html.Div(id="dcf-valuation"),
    ], id="dcf-valuation-card", style=dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding="20px", display="none"))

    # ── Perform DCF button ──
    dcf_button = html.Div([
        html.Button("Perform DCF", id="dcf-perform-btn", n_clicks=0,
            style=dict(
                background="rgba(54,211,153,0.15)", color=ACCENT,
                border="1px solid rgba(54,211,153,0.3)",
                borderRadius="8px", padding="10px 28px",
                fontFamily="'Space Grotesk',sans-serif",
                fontSize="14px", fontWeight="600", cursor="pointer",
                letterSpacing="0.3px", transition="all 0.15s")),
    ], style=dict(display="flex", justifyContent="center", marginTop="6px"))

    return html.Div([
        period_store,
        # ── Header ──
        html.Div([
            html.Div([
                html.Div(id="dcf-ticker-name", style=dict(
                    fontFamily="'Space Grotesk',sans-serif",
                    fontSize="22px", fontWeight="600", letterSpacing="0.2px")),
                html.Div("DCF Model", style=dict(
                    fontSize="13px", color=T4, marginTop="2px")),
            ], style=dict(flex="1")),
            html.Button("Back", id="dcf-back-btn", n_clicks=0,
                className="sec-range-btn",
                style=dict(fontSize="12px")),
        ], style=dict(display="flex", alignItems="center", gap="28px",
                      marginBottom="6px")),
        # ── Cards ──
        assumptions_card,
        period_toggle,
        html.Div(style=dict(borderTop=f"1px solid {BORDER}", margin="4px 0")),
        projection_card,
        valuation_card,
        dcf_button,
    ], style=dict(padding="24px 28px 40px", display="flex",
                  flexDirection="column", gap="18px",
                  height="100%", overflowY="auto"))


# ── Projection table builder ────────────────────────────────────────

LABEL_W = "185px"

# Input rows: (label, input_id_suffix, is_total)
_PROJ_INPUT_ROWS = [
    ("Revenue Growth (%)",   "rev-growth",  False),
    ("COGS (% of Rev)",     "cogs-pct",    False),
    ("OpEx (% of Rev)",     "opex-pct",    False),
    ("D&A (% of Rev)",      "da-pct",      False),
    ("CapEx (% of Rev)",    "capex-pct",   False),
    ("ΔNWC (% of Rev)",     "nwc-pct",     False),
]

# Calculated rows: (label, key, is_total)
_PROJ_CALC_ROWS = [
    ("Revenue",              "revenue",      True),
    ("Gross Profit",         "gross_profit", False),
    ("EBITDA",               "ebitda",       True),
    ("EBIT",                 "ebit",         False),
    ("NOPAT",                "nopat",        False),
    ("Free Cash Flow",       "fcf",          True),
]


def _proj_table(n_years, symbol, base_revenue):
    """Build the projection input table."""
    ccy = "$"
    cols = [f"Year {i+1}" for i in range(n_years)]

    def _hdr_row():
        cells = [html.Div(style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W))]
        for y in cols:
            cells.append(html.Div(y, style=dict(
                flex="1", textAlign="right",
                fontSize="11px", color=T4, fontWeight="600")))
        return html.Div(cells, style=dict(
            display="flex", gap="16px", padding="6px 0",
            borderBottom="1px solid rgba(255,255,255,0.08)",
            marginBottom="2px"))

    def _group_hdr(text):
        return html.Div(text, style=dict(
            fontSize="10px", fontWeight="700", color=T5,
            letterSpacing="0.7px", textTransform="uppercase",
            padding="10px 0 3px"))

    def _input_row(label, input_suffix, is_total=False):
        cells = [html.Div(label, style=dict(
            flex=f"0 0 {LABEL_W}", minWidth=LABEL_W, paddingLeft="8px",
            fontSize="12.5px",
            fontWeight="600" if is_total else "400",
            color=T1 if is_total else T3))]
        for i in range(n_years):
            input_id = {"type": "dcf-proj-input", "index": f"{input_suffix}-{i}"}
            cells.append(html.Div(
                _input_cell("", value=None, input_id=input_id),
                style=dict(flex="1")))
        return html.Div(cells, style=dict(
            display="flex", gap="16px",
            padding="5px 0",
            background="rgba(255,255,255,0.015)" if is_total else "transparent"))

    def _calc_row(label, key, is_total=False):
        cells = [html.Div(label, style=dict(
            flex=f"0 0 {LABEL_W}", minWidth=LABEL_W, paddingLeft="8px",
            fontSize="12.5px",
            fontWeight="600" if is_total else "400",
            color=T1 if is_total else T3))]
        for i in range(n_years):
            cells.append(html.Div(
                id={"type": "dcf-proj-calc", "index": f"{key}-{i}"},
                style=dict(flex="1", textAlign="right",
                    fontSize="12.5px",
                    fontWeight="600" if is_total else "400",
                    color=T1 if is_total else T2,
                    fontFamily="'JetBrains Mono',monospace",
                    padding="6px 8px")))
        return html.Div(cells, style=dict(
            display="flex", gap="16px",
            padding="5px 0",
            background="rgba(255,255,255,0.015)" if is_total else "transparent"))

    rows = [_hdr_row()]
    rows.append(_group_hdr("Assumptions"))
    for label, suffix, is_total in _PROJ_INPUT_ROWS:
        rows.append(_input_row(label, suffix, is_total))
    rows.append(_group_hdr("Projections"))
    for label, key, is_total in _PROJ_CALC_ROWS:
        rows.append(_calc_row(label, key, is_total))

    return html.Div([
        html.Div(f"Base Revenue: {ccy}{_fmt_plain(base_revenue)}",
            style=dict(fontSize="11px", color=T4, marginBottom="8px")),
        html.Div(rows, style=dict(overflowX="auto")),
    ])


# ── Valuation summary builder ────────────────────────────────────────

def _valuation_summary(ev, eq, price, shares_m):
    ccy = "$"
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Enterprise Value", style=dict(
                    fontSize="10.5px", color=T4, fontWeight="500",
                    letterSpacing="0.3px")),
                html.Div(f"{ccy}{_fmt_money(ev, '')}", style=dict(
                    fontSize="18px", fontWeight="600", color=T1,
                    fontFamily="'JetBrains Mono',monospace", marginTop="4px")),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Equity Value", style=dict(
                    fontSize="10.5px", color=T4, fontWeight="500",
                    letterSpacing="0.3px")),
                html.Div(f"{ccy}{_fmt_money(eq, '')}", style=dict(
                    fontSize="18px", fontWeight="600", color=T1,
                    fontFamily="'JetBrains Mono',monospace", marginTop="4px")),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Implied Share Price", style=dict(
                    fontSize="10.5px", color=T4, fontWeight="500",
                    letterSpacing="0.3px")),
                html.Div(f"{ccy}{price:.2f}", style=dict(
                    fontSize="18px", fontWeight="600", color=ACCENT,
                    fontFamily="'JetBrains Mono',monospace", marginTop="4px")),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("Shares Outstanding", style=dict(
                    fontSize="10.5px", color=T4, fontWeight="500",
                    letterSpacing="0.3px")),
                html.Div(f"{shares_m:,.0f}M", style=dict(
                    fontSize="18px", fontWeight="600", color=T1,
                    fontFamily="'JetBrains Mono',monospace", marginTop="4px")),
            ], style=dict(flex="1")),
        ], style=dict(display="flex", gap="20px")),
    ])


# ── Period toggle callback ───────────────────────────────────────────

@app.callback(
    Output("dcf-period-store", "data"),
    Input({"type": "dcf-period-btn", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _set_dcf_period(_clicks):
    from dash import ctx
    tid = ctx.triggered_id
    if isinstance(tid, dict):
        return tid.get("index", "5Y")
    return no_update


@app.callback(
    Output({"type": "dcf-period-btn", "index": ALL}, "className"),
    Input("dcf-period-store", "data"),
)
def _dcf_period_btn_classes(active):
    b = "sec-range-btn"
    return [f"{b} active" if i["index"] == active else b
            for i in [{"index": "3Y"}, {"index": "5Y"}, {"index": "10Y"}]]


# ── Projection table callback ────────────────────────────────────────

@app.callback(
    Output("dcf-projection-table", "children"),
    Input("dcf-period-store", "data"),
    Input("dcf-ticker-store", "data"),
    prevent_initial_call=True,
)
def _build_projection_table(period, symbol):
    if not symbol:
        return html.Div("Select a security to build a DCF model.",
            style=dict(color=T4, fontSize="12.5px", padding="16px 0"))

    n_years = {"3Y": 3, "5Y": 5, "10Y": 10}.get(period or "5Y", 5)

    yf = _fetch_yf_info(symbol)
    info = yf.get("info", {})
    income = yf.get("income")
    base_rev = _df_val(income, "Total Revenue", "Operating Revenue") or 1e9

    return _proj_table(n_years, symbol, base_rev)


# ── DCF calculation callback ─────────────────────────────────────────

@app.callback(
    Output("dcf-valuation-card", "style"),
    Output("dcf-valuation", "children"),
    Input("dcf-perform-btn", "n_clicks"),
    State("dcf-period-store", "data"),
    State("dcf-ticker-store", "data"),
    State("dcf-wacc", "value"),
    State("dcf-tgr", "value"),
    State("dcf-tax", "value"),
    State("dcf-shares", "value"),
    State("dcf-net-debt", "value"),
    State({"type": "dcf-proj-input", "index": ALL}, "id"),
    State({"type": "dcf-proj-input", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def _perform_dcf(n, period, symbol, wacc, tgr, tax_rate,
                 shares_m, net_debt, input_ids, input_values):
    if not symbol or not n:
        return no_update, no_update

    wacc = (wacc or 10) / 100.0
    tgr = (tgr or 2.5) / 100.0
    tax_rate = (tax_rate or 25) / 100.0
    shares_m = shares_m or 1000
    net_debt = net_debt or 0

    n_years = {"3Y": 3, "5Y": 5, "10Y": 10}.get(period or "5Y", 5)

    # Fetch base revenue
    yf = _fetch_yf_info(symbol)
    income = yf.get("income")
    base_rev = _df_val(income, "Total Revenue", "Operating Revenue") or 1e9

    # Parse input values into a dict keyed by suffix
    vals = {}
    for inp_id, v in zip(input_ids, input_values):
        suffix = inp_id["index"]  # e.g. "rev-growth-0"
        vals[suffix] = v

    # Default assumptions (% of revenue)
    defaults = {
        "rev-growth": 8, "cogs-pct": 60, "opex-pct": 20,
        "da-pct": 5, "capex-pct": 6, "nwc-pct": 2,
    }

    def _get(suffix, year_idx):
        key = f"{suffix}-{year_idx}"
        v = vals.get(key)
        if v is not None:
            return float(v) / 100.0
        return defaults.get(suffix, 0) / 100.0

    # Build projections year by year
    projections = []
    rev = base_rev
    for i in range(n_years):
        growth = _get("rev-growth", i)
        rev = rev * (1 + growth)
        cogs = rev * _get("cogs-pct", i)
        opex = rev * _get("opex-pct", i)
        da = rev * _get("da-pct", i)
        capex = rev * _get("capex-pct", i)
        nwc = rev * _get("nwc-pct", i)

        gross_profit = rev - cogs
        ebitda = gross_profit - opex
        ebit = ebitda - da
        nopat = ebit * (1 - tax_rate)
        fcf = nopat + da - capex - nwc

        projections.append({
            "revenue": rev, "gross_profit": gross_profit,
            "ebitda": ebitda, "ebit": ebit,
            "nopat": nopat, "fcf": fcf,
        })

    # Terminal value (Gordon Growth Model)
    if projections:
        terminal_fcf = projections[-1]["fcf"] * (1 + tgr)
        terminal_value = terminal_fcf / (wacc - tgr) if wacc > tgr else 0
    else:
        terminal_value = 0

    # Discount projected FCFs and terminal value
    pv_fcfs = sum(
        p["fcf"] / (1 + wacc) ** (i + 1)
        for i, p in enumerate(projections)
    )
    pv_terminal = terminal_value / (1 + wacc) ** n_years if projections else 0

    ev = pv_fcfs + pv_terminal
    eq = ev - net_debt
    price_per_share = eq / shares_m if shares_m else 0

    # Build valuation summary
    summary = _valuation_summary(ev, eq, price_per_share, shares_m)

    card_style = dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="14px", padding="20px", display="block")

    return card_style, summary


# ── Back button callback ─────────────────────────────────────────────

@app.callback(
    Output("sec-detail-store", "data", allow_duplicate=True),
    Input("dcf-back-btn", "n_clicks"),
    State("dcf-ticker-store", "data"),
    prevent_initial_call=True,
)
def _dcf_back(_n, symbol):
    return symbol


# ── Ticker name display ─────────────────────────────────────────────

@app.callback(
    Output("dcf-ticker-name", "children"),
    Input("dcf-ticker-store", "data"),
    prevent_initial_call=True,
)
def _update_dcf_ticker_name(symbol):
    if not symbol:
        return ""
    yf = _fetch_yf_info(symbol)
    info = yf.get("info", {})
    name = info.get("name", symbol)
    return f"{name} ({symbol})"
