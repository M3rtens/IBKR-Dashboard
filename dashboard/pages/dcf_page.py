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
    stmt_store = dcc.Store(id="dcf-stmt-store", data="income")

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
        html.Div([
            html.Div("Revenue & Cost Projections", style=dict(
                fontSize="13px", fontWeight="600", color=T2)),
            _toggle_group(
                html.Button("Income Statement", id="dcf-stmt-income",
                    n_clicks=0, className="sec-range-btn active"),
                html.Button("Balance Sheet",    id="dcf-stmt-balance",
                    n_clicks=0, className="sec-range-btn"),
                html.Button("Cash Flow",        id="dcf-stmt-cashflow",
                    n_clicks=0, className="sec-range-btn"),
            ),
        ], style=dict(display="flex", justifyContent="space-between",
                      alignItems="center", marginBottom="14px")),
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
        stmt_store,
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

# Grouped financial statement definitions for DCF input tables
# Each group: (group_label, [(label, input_id, is_total), ...])

_INCOME_STMT_ROWS = [
    ("Revenue", [
        ("Total Revenue",       "total-revenue",  True,  False),
        ("Cost of Revenue",     "cogs",           False, False),
        ("Gross Profit",        "gross-profit",   True,  True),
    ]),
    ("Operating Expenses", [
        ("R&D",                 "rnd",            False, False),
        ("SG&A",                "sga",            False, False),
        ("Total Op. Expenses",  "opex",           False, True),
        ("Operating Income",    "op-income",      True,  True),
    ]),
    ("EBITDA / EBIT", [
        ("EBITDA",              "ebitda",         True,  True),
        ("Depreciation",        "da",             False, False),
        ("EBIT",                "ebit",           False, True),
    ]),
    ("Below Operating", [
        ("Interest Income",     "int-income",     False, False),
        ("Interest Expense",    "int-expense",    False, False),
        ("Pretax Income",       "pretax",         True,  True),
    ]),
    ("Tax & Net Income", [
        ("Tax Provision",       "tax",            False, False),
        ("Net Income",          "net-income",     True,  True),
    ]),
    ("Per Share", [
        ("Basic EPS",           "basic-eps",      False, False),
        ("Diluted EPS",         "diluted-eps",    True,  False),
        ("Basic Shares",        "basic-shares",   False, False),
        ("Diluted Shares",      "diluted-shares", False, False),
    ]),
]

_BALANCE_SHEET_ROWS = [
    ("Current Assets", [
        ("Cash & ST Investments",  "cash",              False, False),
        ("Accounts Receivable",    "receivables",       False, False),
        ("Inventory",              "inventory",         False, False),
        ("Other Current Assets",   "other-ca",          False, False),
        ("Total Current Assets",   "total-ca",          True,  True),
    ]),
    ("Non-Current Assets", [
        ("Net PPE",                "ppe",               False, False),
        ("Investments & Advances", "investments",       False, False),
        ("Other Non-Curr Assets",  "other-nca",         False, False),
        ("Total Non-Curr Assets",  "total-nca",         True,  True),
    ]),
    ("Total Assets", [
        ("Total Assets",           "total-assets",      True,  True),
    ]),
    ("Current Liabilities", [
        ("Accounts Payable",       "payables",          False, False),
        ("Current Debt",           "current-debt",      False, False),
        ("Other Current Liabs",    "other-cl",          False, False),
        ("Total Current Liabs",    "total-cl",          True,  True),
    ]),
    ("Non-Current Liabilities", [
        ("Long Term Debt",         "lt-debt",           False, False),
        ("Other Non-Curr Liabs",   "other-ncl",         False, False),
        ("Total Non-Curr Liabs",   "total-ncl",         True,  True),
    ]),
    ("Total Liabilities & Equity", [
        ("Total Liabilities",      "total-liab",        True,  True),
        ("Stockholders Equity",    "equity",            True,  True),
    ]),
    ("Key Metrics", [
        ("Working Capital",        "working-capital",   False, True),
        ("Net Debt",               "net-debt",          False, True),
        ("Invested Capital",       "invested-capital",  False, True),
    ]),
]

_CASHFLOW_STMT_ROWS = [
    ("Operating Activities", [
        ("Net Income",             "cf-net-income",     False, False),
        ("D&A",                    "cf-da",             False, False),
        ("Stock-Based Comp.",      "sbc",               False, False),
        ("Chg Working Capital",    "cf-nwc",            False, False),
        ("Other Non-Cash",         "cf-other",          False, False),
        ("Operating Cash Flow",    "ocf",               True,  True),
    ]),
    ("Investing Activities", [
        ("Capital Expenditure",    "capex",             False, False),
        ("Purchase of PPE",        "purchase-ppe",      False, False),
        ("Purchase Investments",   "purchase-inv",      False, False),
        ("Sale of Investments",    "sale-inv",          False, False),
        ("Investing Cash Flow",    "icf",               True,  True),
    ]),
    ("Financing Activities", [
        ("Dividends Paid",         "dividends",         False, False),
        ("Stock Repurchases",      "buybacks",          False, False),
        ("LT Debt Issuance",       "debt-issuance",     False, False),
        ("LT Debt Repayment",      "debt-repayment",    False, False),
        ("Net Debt Issuance",      "net-debt-issuance", False, False),
        ("Financing Cash Flow",    "fcf-total",         True,  True),
    ]),
    ("Summary", [
        ("Free Cash Flow",         "fcf",               True,  True),
        ("End Cash Position",      "end-cash",          False, True),
        ("Net Change in Cash",     "net-cash-change",   False, True),
    ]),
]

# Percentage-of-parent fields: (child_id, parent_id, pct_label)
# When a % is entered, the child value = parent * pct / 100
_PCT_FIELDS = [
    ("cogs",         "total-revenue", "of Total Revenue"),
    ("rnd",          "total-revenue", "of Total Revenue"),
    ("sga",          "total-revenue", "of Total Revenue"),
    ("opex",         "total-revenue", "of Total Revenue"),
    ("da",           "total-revenue", "of Total Revenue"),
    ("int-expense",  "total-revenue", "of Total Revenue"),
    ("tax",          "total-revenue", "of Total Revenue"),
    ("payables",     "total-ca",      "of Total Current Assets"),
    ("receivables",  "total-ca",      "of Total Current Assets"),
    ("inventory",    "total-ca",      "of Total Current Assets"),
    ("other-ca",     "total-ca",      "of Total Current Assets"),
    ("ppe",          "total-nca",     "of Total Non-Curr Assets"),
    ("investments",  "total-nca",     "of Total Non-Curr Assets"),
    ("other-nca",    "total-nca",     "of Total Non-Curr Assets"),
    ("current-debt", "total-cl",      "of Total Current Liabs"),
    ("other-cl",     "total-cl",      "of Total Current Liabs"),
    ("lt-debt",      "total-ncl",     "of Total Non-Curr Liabs"),
    ("other-ncl",    "total-ncl",     "of Total Non-Curr Liabs"),
    ("cf-net-income","ocf",           "of Operating Cash Flow"),
    ("cf-da",        "ocf",           "of Operating Cash Flow"),
    ("sbc",          "ocf",           "of Operating Cash Flow"),
    ("cf-nwc",       "ocf",           "of Operating Cash Flow"),
    ("cf-other",     "ocf",           "of Operating Cash Flow"),
    ("capex",        "icf",           "of Investing Cash Flow"),
    ("purchase-ppe", "icf",           "of Investing Cash Flow"),
    ("purchase-inv", "icf",           "of Investing Cash Flow"),
    ("sale-inv",     "icf",           "of Investing Cash Flow"),
    ("dividends",    "fcf-total",     "of Financing Cash Flow"),
    ("buybacks",     "fcf-total",     "of Financing Cash Flow"),
    ("debt-issuance","fcf-total",     "of Financing Cash Flow"),
    ("debt-repayment","fcf-total",    "of Financing Cash Flow"),
]


_PCT_MAP = {child: (child, parent, label)
            for child, parent, label in _PCT_FIELDS}


def _year_labels(income_df, n_years):
    """Generate projection year labels like '2027-12 E' from the most recent statement date."""
    base_year = None
    if income_df is not None and not (hasattr(income_df, "empty") and income_df.empty):
        try:
            base_year = int(str(income_df.columns[0])[:4])
        except Exception:
            pass
    if base_year is None:
        base_year = 2025
    return [f"{base_year + i + 1}-12 E" for i in range(n_years)]

# Build dynamic callback args for the auto-calc client-side callback
# Single percentage input per row (applies to all years)
_PCT_CHILD_IDS = [f"{child}" for child, _, _ in _PCT_FIELDS]
_PCT_PARENT_IDS = list({parent for _, parent, _ in _PCT_FIELDS})
# Full per-year IDs for outputs and states
_PCT_CHILD_IDSFull = [f"{child}-{i}" for child, _, _ in _PCT_FIELDS for i in range(10)]
_PCT_PARENT_IDSFull = [f"{parent}-{i}" for _, parent, _ in _PCT_FIELDS for i in range(10)]

# Calculated field IDs (displayed as read-only divs, computed by client-side callback)
_CALC_IDS = [
    "gross-profit", "opex", "op-income", "ebitda", "ebit", "pretax", "net-income",
    "total-ca", "total-nca", "total-assets", "total-cl", "total-ncl",
    "total-liab", "equity", "working-capital", "net-debt", "invested-capital",
    "ocf", "icf", "fcf-total", "fcf", "end-cash", "net-cash-change",
]
_CALC_CHILD_IDS = [f"{cid}-{i}" for cid in _CALC_IDS for i in range(10)]

# All input IDs that feed into calculations
_CALC_INPUT_IDS = [
    "total-revenue", "cogs", "rnd", "sga", "da", "int-income", "int-expense", "tax",
    "basic-eps", "diluted-eps", "basic-shares", "diluted-shares",
    "cash", "receivables", "inventory", "other-ca", "ppe", "investments", "other-nca",
    "payables", "current-debt", "other-cl", "lt-debt", "other-ncl",
    "cf-net-income", "cf-da", "sbc", "cf-nwc", "cf-other",
    "capex", "purchase-ppe", "purchase-inv", "sale-inv",
    "dividends", "buybacks", "debt-issuance", "debt-repayment", "net-debt-issuance",
]
_CALC_INPUT_IDS_FULL = [f"{iid}-{i}" for iid in _CALC_INPUT_IDS for i in range(10)]


def _proj_table(n_years, symbol, base_revenue, stmt="income", year_labels=None):
    """Build the projection input table with grouped financial statement layout."""
    ccy = "$"
    if year_labels is None:
        year_labels = [f"Year {i+1}" for i in range(n_years)]

    # Select the statement rows
    stmt_rows_map = {
        "income":   _INCOME_STMT_ROWS,
        "balance":  _BALANCE_SHEET_ROWS,
        "cashflow": _CASHFLOW_STMT_ROWS,
    }
    groups = stmt_rows_map.get(stmt, _INCOME_STMT_ROWS)

    def _hdr_row():
        cells = [html.Div(style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W))]
        for y in year_labels:
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

    def _data_row(label, input_id, is_total, calculated=False):
        cells = [html.Div(label, style=dict(
            flex=f"0 0 {LABEL_W}", minWidth=LABEL_W, paddingLeft="8px",
            fontSize="12.5px",
            fontWeight="600" if is_total else "400",
            color=T1 if is_total else T3))]
        for i in range(n_years):
            if calculated:
                cells.append(html.Div(
                    id={"type": "dcf-calc-cell", "index": f"{input_id}-{i}"},
                    style=dict(flex="1", textAlign="right",
                        fontSize="12.5px",
                        fontWeight="600" if is_total else "400",
                        color=T1 if is_total else T2,
                        fontFamily="'JetBrains Mono',monospace",
                        padding="6px 8px")))
            else:
                input_id_dict = {"type": "dcf-proj-input",
                                 "index": f"{input_id}-{i}"}
                cells.append(html.Div(
                    _input_cell("", value=None, input_id=input_id_dict),
                    style=dict(flex="1")))
        result = [html.Div(cells, style=dict(
            display="flex", gap="16px",
            padding="5px 0",
            background="rgba(255,255,255,0.015)" if is_total else "transparent"))]

        # Add percentage sub-row if this field has a parent
        pct_def = _PCT_MAP.get(input_id)
        if pct_def:
            _, parent_id, pct_label = pct_def
            pct_id_dict = {"type": "dcf-pct-input", "index": input_id}
            pct_cells = [
                html.Div([
                    html.Span(f"% {pct_label} ", style=dict(
                        fontSize="10px", fontStyle="italic", color=T5)),
                    html.Span("%", style=dict(
                        fontSize="9px", color=T4,
                        fontFamily="'JetBrains Mono',monospace")),
                    dcc.Input(
                        id=pct_id_dict, type="number", placeholder="",
                        value=None, debounce=True, step=1,
                        style=dict(
                            background="rgba(255,255,255,0.04)",
                            border="1px solid rgba(255,255,255,0.08)",
                            borderRadius="3px", color=T1,
                            fontFamily="'JetBrains Mono',monospace",
                            fontSize="10px", padding="2px 4px",
                            width="42px", textAlign="right",
                            outline="none", marginLeft="3px")),
                ], style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W,
                    paddingLeft="20px", display="flex",
                    alignItems="center")),
            ]
            # Empty cells for year columns (percentage applies to all)
            for i in range(n_years):
                pct_cells.append(html.Div(style=dict(flex="1")))
            result.append(html.Div(pct_cells, style=dict(
                display="flex", gap="16px", padding="0 0 3px")))
        return result

    rows = [_hdr_row()]
    for grp_label, row_defs in groups:
        rows.append(_group_hdr(grp_label))
        for row_def in row_defs:
            label, input_id, is_total = row_def[:3]
            calculated = row_def[3] if len(row_def) > 3 else False
            rows.extend(_data_row(label, input_id, is_total, calculated))

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


# ── Statement toggle callback ────────────────────────────────────────

@app.callback(
    Output("dcf-stmt-store", "data"),
    Input("dcf-stmt-income",   "n_clicks"),
    Input("dcf-stmt-balance",  "n_clicks"),
    Input("dcf-stmt-cashflow", "n_clicks"),
    prevent_initial_call=True,
)
def _set_dcf_stmt(*_):
    from dash import ctx
    tid = ctx.triggered_id
    return {"dcf-stmt-income": "income",
            "dcf-stmt-balance": "balance",
            "dcf-stmt-cashflow": "cashflow"}.get(tid, no_update)


@app.callback(
    Output("dcf-stmt-income",   "className"),
    Output("dcf-stmt-balance",  "className"),
    Output("dcf-stmt-cashflow", "className"),
    Input("dcf-stmt-store", "data"),
)
def _dcf_stmt_btn_classes(stmt):
    b = "sec-range-btn"
    return (
        f"{b} active" if stmt == "income"   else b,
        f"{b} active" if stmt == "balance"  else b,
        f"{b} active" if stmt == "cashflow" else b,
    )


# ── Projection table callback ────────────────────────────────────────

@app.callback(
    Output("dcf-projection-table", "children"),
    Input("dcf-period-store", "data"),
    Input("dcf-ticker-store", "data"),
    Input("dcf-stmt-store", "data"),
    prevent_initial_call=True,
)
def _build_projection_table(period, symbol, stmt):
    if not symbol:
        return html.Div("Select a security to build a DCF model.",
            style=dict(color=T4, fontSize="12.5px", padding="16px 0"))

    n_years = {"3Y": 3, "5Y": 5, "10Y": 10}.get(period or "5Y", 5)

    yf = _fetch_yf_info(symbol)
    info = yf.get("info", {})
    income = yf.get("income")
    base_rev = _df_val(income, "Total Revenue", "Operating Revenue") or 1e9
    labels = _year_labels(income, n_years)

    return _proj_table(n_years, symbol, base_rev, stmt=stmt or "income",
                       year_labels=labels)


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

    # Default assumptions (% of revenue for income-statement items)
    defaults = {
        "cogs": 60, "opex": 20, "da": 5, "capex": 6,
        "rnd": 10, "sga": 10, "int-expense": 2, "tax": 25,
        "cf-da": 5, "cf-nwc": 2, "sbc": 2,
        "purchase-ppe": 6, "dividends": 2,
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
        cogs = rev * _get("cogs", i)
        opex = rev * _get("opex", i)
        da = rev * _get("da", i)
        capex = rev * _get("capex", i)
        nwc = rev * _get("cf-nwc", i)

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


# ── Client-side auto-calc: % of parent → child value ────────────────

# Outputs: one per child per year (e.g. cogs-0, cogs-1, ..., rnd-0, rnd-1, ...)
_PCSOutputs = [Output({"type": "dcf-proj-input", "index": c}, "value")
               for c in _PCT_CHILD_IDSFull]
# Inputs: single percentage per row (e.g. cogs-pct, rnd-pct, ...)
_PCSInputs  = [Input({"type": "dcf-pct-input", "index": c}, "value")
               for c in _PCT_CHILD_IDS]
# States: parent values per year (e.g. total-revenue-0, total-revenue-1, ...)
_PCSStates  = [State({"type": "dcf-proj-input", "index": p}, "value")
               for p in _PCT_PARENT_IDSFull]

app.clientside_callback(
    """
    function(...args) {
        const n_pct = %(n_pct)s;
        const n_years = %(n_years)s;
        const childBases = %(child_bases)s;
        const parentBases = %(parent_bases)s;
        const mapping = %(mapping)s;

        // First n_pct args are single percentage values per row
        var pctMap = {};
        for (var j = 0; j < n_pct; j++) {
            if (args[j] !== null && args[j] !== undefined && args[j] !== "")
                pctMap[childBases[j]] = parseFloat(args[j]);
        }

        // Remaining args are parent values per year: parent0-yr0, parent0-yr1, ...
        var parMap = {};
        var stateArgs = args.slice(n_pct);
        for (var j = 0; j < parentBases.length; j++) {
            for (var y = 0; y < n_years; y++) {
                var key = parentBases[j] + "-" + y;
                var idx = j * n_years + y;
                if (idx < stateArgs.length && stateArgs[idx] !== null && stateArgs[idx] !== undefined && stateArgs[idx] !== "")
                    parMap[key] = parseFloat(stateArgs[idx]);
            }
        }

        // For each child per year, calculate value = parent * pct / 100
        var results = [];
        for (var c = 0; c < childBases.length; c++) {
            var baseId = childBases[c];
            var pct = pctMap[baseId];
            var parentId = mapping[baseId] || null;
            for (var y = 0; y < n_years; y++) {
                if (pct === undefined || pct === null || !parentId) {
                    results.push(null);
                    continue;
                }
                var parKey = parentId + "-" + y;
                if (parMap[parKey] !== undefined) {
                    results.push(Math.round(parMap[parKey] * pct / 100));
                } else {
                    results.push(null);
                }
            }
        }
        return results;
    }
    """ % {
        "n_pct": len(_PCT_CHILD_IDS),
        "n_years": 10,
        "child_bases": str(_PCT_CHILD_IDS),
        "parent_bases": str(_PCT_PARENT_IDS),
        "mapping": str({child: parent for child, parent, _ in _PCT_FIELDS}),
    },
    _PCSOutputs,
    _PCSInputs + _PCSStates,
)


# ── Client-side auto-calc: computed fields (totals, subtotals) ──────

_CCSInputs = [Input({"type": "dcf-proj-input", "index": c}, "value")
              for c in _CALC_INPUT_IDS_FULL]
_CCSOutputs = [Output({"type": "dcf-calc-cell", "index": c}, "children")
               for c in _CALC_CHILD_IDS]

app.clientside_callback(
    """
    function(...args) {
        const n_in = %(n_in)s;
        const inIds  = %(in_ids)s;
        const calcIds = %(calc_ids)s;
        const mapping = %(mapping)s;

        // Build input lookup
        var vals = {};
        for (var j = 0; j < inIds.length; j++) {
            if (args[j] !== null && args[j] !== undefined && args[j] !== "")
                vals[inIds[j]] = parseFloat(args[j]);
        }

        function v(key) { return vals[key] || 0; }

        var results = [];
        for (var i = 0; i < calcIds.length; i++) {
            var cid = calcIds[i];
            // Extract year suffix (e.g. "gross-profit-0" → "0")
            var dashIdx = cid.lastIndexOf("-");
            var year = cid.substring(dashIdx + 1);
            var base = cid.substring(0, dashIdx);
            var val = 0;

            // ── Income Statement ──
            if (base === "gross-profit")
                val = v("total-revenue-"+year) - v("cogs-"+year);
            else if (base === "opex")
                val = v("rnd-"+year) + v("sga-"+year);
            else if (base === "op-income")
                val = (v("total-revenue-"+year) - v("cogs-"+year)) - (v("rnd-"+year) + v("sga-"+year));
            else if (base === "ebitda")
                val = ((v("total-revenue-"+year) - v("cogs-"+year)) - (v("rnd-"+year) + v("sga-"+year))) + v("da-"+year);
            else if (base === "ebit")
                val = ((v("total-revenue-"+year) - v("cogs-"+year)) - (v("rnd-"+year) + v("sga-"+year))) - v("da-"+year);
            else if (base === "pretax")
                val = (((v("total-revenue-"+year) - v("cogs-"+year)) - (v("rnd-"+year) + v("sga-"+year))) - v("da-"+year)) + v("int-income-"+year) - v("int-expense-"+year);
            else if (base === "net-income")
                val = ((((v("total-revenue-"+year) - v("cogs-"+year)) - (v("rnd-"+year) + v("sga-"+year))) - v("da-"+year)) + v("int-income-"+year) - v("int-expense-"+year)) * (1 - v("tax-"+year) / 100);

            // ── Balance Sheet ──
            else if (base === "total-ca")
                val = v("cash-"+year) + v("receivables-"+year) + v("inventory-"+year) + v("other-ca-"+year);
            else if (base === "total-nca")
                val = v("ppe-"+year) + v("investments-"+year) + v("other-nca-"+year);
            else if (base === "total-assets")
                val = (v("cash-"+year) + v("receivables-"+year) + v("inventory-"+year) + v("other-ca-"+year))
                    + (v("ppe-"+year) + v("investments-"+year) + v("other-nca-"+year));
            else if (base === "total-cl")
                val = v("payables-"+year) + v("current-debt-"+year) + v("other-cl-"+year);
            else if (base === "total-ncl")
                val = v("lt-debt-"+year) + v("other-ncl-"+year);
            else if (base === "total-liab")
                val = (v("payables-"+year) + v("current-debt-"+year) + v("other-cl-"+year))
                    + (v("lt-debt-"+year) + v("other-ncl-"+year));
            else if (base === "equity")
                val = ((v("cash-"+year) + v("receivables-"+year) + v("inventory-"+year) + v("other-ca-"+year))
                    + (v("ppe-"+year) + v("investments-"+year) + v("other-nca-"+year)))
                    - ((v("payables-"+year) + v("current-debt-"+year) + v("other-cl-"+year))
                    + (v("lt-debt-"+year) + v("other-ncl-"+year)));
            else if (base === "working-capital")
                val = (v("cash-"+year) + v("receivables-"+year) + v("inventory-"+year) + v("other-ca-"+year))
                    - (v("payables-"+year) + v("current-debt-"+year) + v("other-cl-"+year));
            else if (base === "net-debt")
                val = (v("current-debt-"+year) + v("lt-debt-"+year)) - v("cash-"+year);
            else if (base === "invested-capital")
                val = ((v("cash-"+year) + v("receivables-"+year) + v("inventory-"+year) + v("other-ca-"+year))
                    + (v("ppe-"+year) + v("investments-"+year) + v("other-nca-"+year)))
                    - (v("payables-"+year) + v("other-cl-"+year));

            // ── Cash Flow ──
            else if (base === "ocf")
                val = v("cf-net-income-"+year) + v("cf-da-"+year) + v("sbc-"+year) + v("cf-nwc-"+year) + v("cf-other-"+year);
            else if (base === "icf")
                val = v("capex-"+year) + v("purchase-ppe-"+year) + v("purchase-inv-"+year) + v("sale-inv-"+year);
            else if (base === "fcf-total")
                val = v("dividends-"+year) + v("buybacks-"+year) + v("debt-issuance-"+year) + v("debt-repayment-"+year) + v("net-debt-issuance-"+year);
            else if (base === "fcf")
                val = (v("cf-net-income-"+year) + v("cf-da-"+year) + v("sbc-"+year) + v("cf-nwc-"+year) + v("cf-other-"+year))
                    + (v("capex-"+year) + v("purchase-ppe-"+year) + v("purchase-inv-"+year) + v("sale-inv-"+year));
            else if (base === "end-cash")
                val = v("cash-"+year) + (v("cf-net-income-"+year) + v("cf-da-"+year) + v("sbc-"+year) + v("cf-nwc-"+year) + v("cf-other-"+year))
                    + (v("capex-"+year) + v("purchase-ppe-"+year) + v("purchase-inv-"+year) + v("sale-inv-"+year))
                    + (v("dividends-"+year) + v("buybacks-"+year) + v("debt-issuance-"+year) + v("debt-repayment-"+year) + v("net-debt-issuance-"+year));
            else if (base === "net-cash-change")
                val = (v("cf-net-income-"+year) + v("cf-da-"+year) + v("sbc-"+year) + v("cf-nwc-"+year) + v("cf-other-"+year))
                    + (v("capex-"+year) + v("purchase-ppe-"+year) + v("purchase-inv-"+year) + v("sale-inv-"+year))
                    + (v("dividends-"+year) + v("buybacks-"+year) + v("debt-issuance-"+year) + v("debt-repayment-"+year) + v("net-debt-issuance-"+year));

            // Format: abbreviate large numbers
            var abs = Math.abs(val);
            var sign = val < 0 ? "-" : "";
            var formatted;
            if (abs >= 1e12)      formatted = sign + "$" + (abs/1e12).toFixed(2) + "T";
            else if (abs >= 1e9)  formatted = sign + "$" + (abs/1e9).toFixed(2) + "B";
            else if (abs >= 1e6)  formatted = sign + "$" + (abs/1e6).toFixed(1) + "M";
            else if (abs >= 1e3)  formatted = sign + "$" + (abs/1e3).toFixed(1) + "K";
            else if (abs === 0 && val === 0) formatted = "—";
            else                  formatted = sign + "$" + abs.toFixed(2);

            results.push(val === 0 ? "—" : formatted);
        }
        return results;
    }
    """ % {
        "n_in": len(_CALC_INPUT_IDS_FULL),
        "in_ids": str(_CALC_INPUT_IDS_FULL),
        "calc_ids": str(_CALC_CHILD_IDS),
        "mapping": "{}",
    },
    _CCSOutputs,
    _CCSInputs,
)
