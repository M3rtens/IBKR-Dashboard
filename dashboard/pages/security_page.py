"""Security detail page: header, price cards, candlestick chart, per-symbol
news and full yfinance financials (income, balance sheet, cash flow).
Populated dynamically when a symbol is selected.
"""

import time
import threading

from dash import dcc, html, Input, Output, State, no_update, ALL

from dashboard.app_instance import app
from dashboard.theme import (BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5,
                             MARKET_DATA_SOURCE)
from dashboard.formatters import money, price_money, ccy_symbol
from dashboard.charts import _build_candlestick_chart
from dashboard.data import _fetch_news, _fetch_symbol_news, _get_symbol_trades


# ── Security-detail loading (yfinance or IBKR per MARKET_DATA_SOURCE) ──

def _held_position(symbol: str):
    """Position dict for ``symbol`` from the IBKR snapshot, or None if not held."""
    try:
        from services.ibkr_client import get_client
        hdf = get_client().cached_snapshot.holdings
        if hdf is not None and not hdf.empty:
            m = hdf[hdf["Symbol"].astype(str).str.upper() == symbol.upper()]
            if not m.empty:
                r = m.iloc[0]
                return {
                    "shares":        float(r.get("Position") or 0),
                    "avgCost":       float(r.get("Avg Cost") or 0),
                    "marketValue":   float(r.get("Market Value") or 0),
                    "unrealisedPnl": float(r.get("Unrealized P&L") or 0),
                    "realisedPnl":   float(r.get("Realized P&L") or 0),
                }
    except Exception:
        pass
    return None


def _load_detail(symbol: str):
    """Load security detail from the configured market-data source."""
    if MARKET_DATA_SOURCE == "yfinance":
        from services.market_data import get_security_detail
        return get_security_detail(symbol, position=_held_position(symbol))

    import time as _time
    from services.ibkr_client import get_client
    client = get_client()
    d = client.get_security_detail(symbol)
    if not d:
        for _ in range(10):
            _time.sleep(0.5)
            d = client.get_security_detail(symbol)
            if d:
                break
    return d


# ── Layout ────────────────────────────────────────────────────────────

def security_page():
    sec_range_store = dcc.Store(id="sec-range-store", data="1Y")
    sec_kalman_store = dcc.Store(id="sec-kalman-store", data=False)
    fin_stmt_store  = dcc.Store(id="fin-stmt-store",  data="income")
    fin_period_store = dcc.Store(id="fin-period-store", data="annual")
    dcf_ticker_store = dcc.Store(id="dcf-ticker-store")

    def _toggle_group(*buttons):
        return html.Div(buttons, style=dict(
            display="flex", gap="2px",
            background="rgba(255,255,255,0.04)",
            borderRadius="7px", padding="2px"))

    return html.Div([
        sec_range_store,
        sec_kalman_store,
        fin_stmt_store,
        fin_period_store,
        dcf_ticker_store,
        # ── Header ──
        html.Div([
            html.Div([
                html.Div(id="sec-name", style=dict(
                    fontFamily="'Space Grotesk',sans-serif",
                    fontSize="22px", fontWeight="600", letterSpacing="0.2px")),
                html.Div(id="sec-subtitle",
                    style=dict(fontSize="13px", color=T4, marginTop="2px")),
            ], style=dict(flex="1")),
            html.Div(id="sec-price-header"),
            html.Div(id="sec-position-badge"),
            html.Button("DCF Model", id="sec-dcf-btn", n_clicks=0,
                className="sec-range-btn",
                style=dict(fontSize="11px")),
        ], style=dict(display="flex", alignItems="center", gap="28px")),
        # ── Valuation metric cards ──
        html.Div(id="sec-price-cards",
            style=dict(display="grid",
                gridTemplateColumns="repeat(auto-fit, minmax(130px, 1fr))",
                gap="12px")),
        # ── Chart (3/5) + News (2/5) row ──
        html.Div([
            # Price chart card
            html.Div([
                html.Div([
                    html.Span("Price Chart", style=dict(
                        fontSize="13px", fontWeight="600", color=T2)),
                    html.Div([
                        # Kalman smoothing overlay toggle
                        html.Button("Kalman", id="sec-kalman-btn", n_clicks=0,
                            title="Overlay a Kalman-smoothed price line "
                                  "(filtered in log space)",
                            className="sec-range-btn"),
                        # Timeframe selector
                        html.Div([
                            html.Button("1D", id={"type":"sec-range-btn","index":"1D"},
                                n_clicks=0, className="sec-range-btn"),
                            html.Button("5D", id={"type":"sec-range-btn","index":"5D"},
                                n_clicks=0, className="sec-range-btn"),
                            html.Button("3M", id={"type":"sec-range-btn","index":"3M"},
                                n_clicks=0, className="sec-range-btn"),
                            html.Button("1Y", id={"type":"sec-range-btn","index":"1Y"},
                                n_clicks=0, className="sec-range-btn active"),
                            html.Button("5Y", id={"type":"sec-range-btn","index":"5Y"},
                                n_clicks=0, className="sec-range-btn"),
                        ], style=dict(display="flex", gap="2px",
                            background="rgba(255,255,255,0.04)",
                            borderRadius="7px", padding="2px")),
                    ], style=dict(display="flex", alignItems="center", gap="10px")),
                ], style=dict(display="flex", alignItems="center",
                              justifyContent="space-between", marginBottom="12px")),
                html.Div(id="sec-chart-body", style=dict(minHeight="320px")),
            ], style=dict(flex="3", background=BG_CARD, border=f"1px solid {BORDER}",
                    borderRadius="14px", padding="20px", minWidth="0")),
            # News card
            html.Div(id="sec-news-card",
                style=dict(flex="2", background=BG_CARD, border=f"1px solid {BORDER}",
                    borderRadius="14px", padding="20px", minWidth="0",
                    overflowY="auto")),
        ], style=dict(display="flex", gap="18px", alignItems="stretch")),
        # ── Financials card (hidden until a security is loaded) ──
        html.Div([
            html.Div([
                html.Div("Financials", style=dict(
                    fontSize="13px", fontWeight="600", color=T2)),
                html.Div(id="sec-financials-metrics"),
            ]),
            # Divider + toggle bar
            html.Div([
                html.Div(style=dict(
                    borderTop="1px solid rgba(255,255,255,0.06)",
                    margin="18px 0")),
                html.Div([
                    # Period toggle (left side)
                    _toggle_group(
                        html.Button("Annual",    id="fin-period-annual",
                            n_clicks=0, className="sec-range-btn active"),
                        html.Button("Quarterly", id="fin-period-quarterly",
                            n_clicks=0, className="sec-range-btn"),
                    ),
                    # Statement toggle (right side)
                    _toggle_group(
                        html.Button("Income Statement", id="fin-stmt-income",
                            n_clicks=0, className="sec-range-btn active"),
                        html.Button("Balance Sheet",    id="fin-stmt-balance",
                            n_clicks=0, className="sec-range-btn"),
                        html.Button("Cash Flow",        id="fin-stmt-cashflow",
                            n_clicks=0, className="sec-range-btn"),
                    ),
                ], style=dict(display="flex", justifyContent="space-between",
                              alignItems="center")),
            ]),
            # Statement table (filled by update_fin_table)
            html.Div(id="sec-fin-table", style=dict(marginTop="6px")),
        ], id="sec-financials-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Loading overlay (shown while a security's data is fetched) ──
        html.Div([
            html.Div(className="sec-spinner"),
            html.Div(id="sec-loading-text", children="Loading…", style=dict(
                fontFamily="'JetBrains Mono',monospace", fontSize="13px",
                color=T3, letterSpacing="0.5px")),
        ], id="sec-loading-overlay", style=dict(display="none")),
    ], style=dict(padding="24px 28px 40px", display="flex",
                  flexDirection="column", gap="18px",
                  position="relative", minHeight="calc(100vh - 60px)"))


# ── yfinance fetch & cache ────────────────────────────────────────────

_yf_cache: dict = {}
_YF_TTL = 6 * 3600


def _fetch_yf_financials(symbol: str) -> dict:
    now = time.time()
    cached = _yf_cache.get(symbol)
    if cached and now - cached["ts"] < _YF_TTL:
        return cached["data"]
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        data = {
            "info":       t.info or {},
            "income":     t.income_stmt,
            "balance":    t.balance_sheet,
            "cashflow":   t.cashflow,
            "q_income":   t.quarterly_income_stmt,
            "q_balance":  t.quarterly_balance_sheet,
            "q_cashflow": t.quarterly_cashflow,
        }
        _yf_cache[symbol] = {"data": data, "ts": now}
        return data
    except Exception:
        return {}


# ── Formatters ───────────────────────────────────────────────────────

def _fmt_large(v, sym="$") -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12: return f"{sign}{sym}{a/1e12:.2f}T"
    if a >= 1e9:  return f"{sign}{sym}{a/1e9:.2f}B"
    if a >= 1e6:  return f"{sign}{sym}{a/1e6:.1f}M"
    if a >= 1e3:  return f"{sign}{sym}{a/1e3:.1f}K"
    return f"{sign}{sym}{a:.2f}"


def _fmt_pct(v) -> str:
    try:
        f = float(v)
        return "—" if f != f else f"{f*100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_div_yield(v) -> str:
    # yfinance returns dividendYield already in percent form (0.25 = 0.25%),
    # unlike margins/growth fields which are decimal fractions.
    try:
        f = float(v)
        return "—" if (f != f or f <= 0) else f"{f:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_ratio(v, suffix="x") -> str:
    try:
        f = float(v)
        return "—" if f != f else f"{f:.1f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _fmt_eps(v, sym="$") -> str:
    try:
        f = float(v)
        return "—" if f != f else f"{sym}{f:.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_shares(v, sym=None) -> str:
    # Share counts carry no currency; ``sym`` is accepted (and ignored) so every
    # statement formatter shares one (value, sym) calling convention.
    try:
        f = float(v)
        return "—" if f != f else f"{f/1e6:.0f}M"
    except (TypeError, ValueError):
        return "—"


# ── Statement table builder ──────────────────────────────────────────

# Row def: (display_label, [df_key_candidates], fmt_fn_or_None, is_total[, margin_label])
# The optional 5th element adds an indented "% of revenue" margin sub-row
# directly beneath this line (e.g. "Gross Margin" under "Gross Profit").

_L = _fmt_large
_E = _fmt_eps
_S = _fmt_shares

INCOME_GROUPS = [
    ("Revenue", [
        ("Total Revenue",       ["Total Revenue", "Operating Revenue"],          _L, True),
        ("Cost of Revenue",     ["Cost Of Revenue","Reconciled Cost Of Revenue"], _L, False),
        ("Gross Profit",        ["Gross Profit"],                                 _L, True, "Gross Margin"),
    ]),
    ("Operating Expenses", [
        ("R&D",                 ["Research And Development"],                     _L, False),
        ("SG&A",                ["Selling General And Administration"],           _L, False),
        ("Total Op. Expenses",  ["Total Expenses","Operating Expense"],           _L, False),
        ("Operating Income",    ["Operating Income",
                                 "Total Operating Income As Reported"],           _L, True, "Operating Margin"),
    ]),
    ("EBITDA / EBIT", [
        ("EBITDA",              ["EBITDA","Normalized EBITDA"],                   _L, True, "EBITDA Margin"),
        ("Depreciation",        ["Reconciled Depreciation"],                      _L, False),
        ("EBIT",                ["EBIT"],                                         _L, False),
    ]),
    ("Below Operating", [
        ("Interest Income",     ["Interest Income",
                                 "Interest Income Non Operating"],                _L, False),
        ("Interest Expense",    ["Interest Expense",
                                 "Interest Expense Non Operating"],               _L, False),
        ("Net Interest",        ["Net Interest Income",
                                 "Net Non Operating Interest Income Expense"],    _L, False),
        ("Other Inc/Exp",       ["Other Income Expense",
                                 "Other Non Operating Income Expenses"],          _L, False),
        ("Pretax Income",       ["Pretax Income"],                                _L, True, "Pretax Margin"),
    ]),
    ("Tax & Net Income", [
        ("Tax Provision",       ["Tax Provision"],                                _L, False),
        ("Net Income",          ["Net Income","Net Income Common Stockholders"],  _L, True, "Net Margin"),
        ("Normalized Income",   ["Normalized Income"],                            _L, False),
        ("Net Inc (Cont.)",     ["Net Income Continuous Operations",
                                 "Net Income From Continuing Operation Net Minority Interest"], _L, False),
    ]),
    ("Per Share", [
        ("Basic EPS",           ["Basic EPS"],                                    _E, False),
        ("Diluted EPS",         ["Diluted EPS"],                                  _E, True),
        ("Basic Shares",        ["Basic Average Shares"],                         _S, False),
        ("Diluted Shares",      ["Diluted Average Shares"],                       _S, False),
    ]),
]

BALANCE_GROUPS = [
    ("Current Assets", [
        ("Cash & ST Investments", ["Cash Cash Equivalents And Short Term Investments",
                                   "Cash And Cash Equivalents"],                  _L, False),
        ("Other ST Investments",  ["Other Short Term Investments"],               _L, False),
        ("Accounts Receivable",   ["Accounts Receivable","Receivables"],          _L, False),
        ("Inventory",             ["Inventory"],                                  _L, False),
        ("Other Current Assets",  ["Other Current Assets"],                      _L, False),
        ("Total Current Assets",  ["Current Assets"],                            _L, True),
    ]),
    ("Non-Current Assets", [
        ("Net PPE",               ["Net PPE"],                                    _L, False),
        ("Investments & Advances",["Investments And Advances"],                   _L, False),
        ("Other Non-Curr Assets", ["Other Non Current Assets"],                   _L, False),
        ("Total Non-Curr Assets", ["Total Non Current Assets"],                   _L, True),
    ]),
    ("Total Assets", [
        ("Total Assets",          ["Total Assets"],                               _L, True),
    ]),
    ("Current Liabilities", [
        ("Accounts Payable",      ["Accounts Payable","Payables"],                _L, False),
        ("Current Debt",          ["Current Debt",
                                   "Current Debt And Capital Lease Obligation"],  _L, False),
        ("Deferred Revenue",      ["Current Deferred Revenue",
                                   "Current Deferred Liabilities"],               _L, False),
        ("Other Current Liabs",   ["Other Current Liabilities"],                  _L, False),
        ("Total Current Liabs",   ["Current Liabilities"],                        _L, True),
    ]),
    ("Non-Current Liabilities", [
        ("Long Term Debt",        ["Long Term Debt",
                                   "Long Term Debt And Capital Lease Obligation"], _L, False),
        ("Other Non-Curr Liabs",  ["Other Non Current Liabilities"],              _L, False),
        ("Total Non-Curr Liabs",  ["Total Non Current Liabilities Net Minority Interest"], _L, True),
    ]),
    ("Total Liabilities & Equity", [
        ("Total Liabilities",     ["Total Liabilities Net Minority Interest"],    _L, True),
        ("Retained Earnings",     ["Retained Earnings"],                          _L, False),
        ("Stockholders Equity",   ["Stockholders Equity","Common Stock Equity"],  _L, True),
    ]),
    ("Key Metrics", [
        ("Working Capital",       ["Working Capital"],                            _L, False),
        ("Net Debt",              ["Net Debt"],                                   _L, False),
        ("Invested Capital",      ["Invested Capital"],                           _L, False),
        ("Tangible Book Value",   ["Tangible Book Value"],                        _L, False),
    ]),
]

CASHFLOW_GROUPS = [
    ("Operating Activities", [
        ("Net Income (Cont.)",    ["Net Income From Continuing Operations"],      _L, False),
        ("D&A",                   ["Depreciation And Amortization",
                                   "Depreciation Amortization Depletion"],        _L, False),
        ("Stock-Based Comp.",     ["Stock Based Compensation"],                   _L, False),
        ("Deferred Tax",          ["Deferred Tax","Deferred Income Tax"],         _L, False),
        ("Chg Working Capital",   ["Change In Working Capital"],                  _L, False),
        ("  Chg Receivables",     ["Change In Receivables",
                                   "Changes In Account Receivables"],             _L, False),
        ("  Chg Inventory",       ["Change In Inventory"],                        _L, False),
        ("  Chg Payables",        ["Change In Payable",
                                   "Change In Account Payable"],                  _L, False),
        ("Other Non-Cash",        ["Other Non Cash Items"],                       _L, False),
        ("Operating Cash Flow",   ["Operating Cash Flow",
                                   "Cash Flow From Continuing Operating Activities"], _L, True),
    ]),
    ("Investing Activities", [
        ("Capital Expenditure",   ["Capital Expenditure"],                        _L, False),
        ("Purchase of PPE",       ["Purchase Of PPE"],                            _L, False),
        ("Purchase Investments",  ["Purchase Of Investment"],                     _L, False),
        ("Sale of Investments",   ["Sale Of Investment"],                         _L, False),
        ("Business Acquisitions", ["Purchase Of Business"],                       _L, False),
        ("Investing Cash Flow",   ["Investing Cash Flow",
                                   "Cash Flow From Continuing Investing Activities"], _L, True),
    ]),
    ("Financing Activities", [
        ("Dividends Paid",        ["Cash Dividends Paid",
                                   "Common Stock Dividend Paid"],                 _L, False),
        ("Stock Repurchases",     ["Repurchase Of Capital Stock",
                                   "Common Stock Payments"],                      _L, False),
        ("LT Debt Issuance",      ["Long Term Debt Issuance"],                    _L, False),
        ("LT Debt Repayment",     ["Long Term Debt Payments"],                    _L, False),
        ("Net Debt Issuance",     ["Net Issuance Payments Of Debt"],              _L, False),
        ("Other Financing",       ["Net Other Financing Charges"],                _L, False),
        ("Financing Cash Flow",   ["Financing Cash Flow",
                                   "Cash Flow From Continuing Financing Activities"], _L, True),
    ]),
    ("Summary", [
        ("Free Cash Flow",        ["Free Cash Flow"],                             _L, True),
        ("End Cash Position",     ["End Cash Position"],                          _L, False),
        ("Net Change in Cash",    ["Changes In Cash"],                            _L, False),
    ]),
]


def _df_row(df, *names):
    if df is None or (hasattr(df, "empty") and df.empty):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def _stmt_table(title: str, groups: list, df, ccy: str = "$") -> html.Div:
    if df is None or (hasattr(df, "empty") and df.empty):
        return html.Div()

    cols        = list(df.columns[:4])
    # One extra (older) column beyond the four displayed lets the oldest shown
    # period also carry a period-over-period change for the horizontal analysis.
    cmp_cols    = list(df.columns[:len(cols) + 1])
    year_labels = [str(c)[:7] for c in cols]  # "2024-12" for quarterly, "2024" for annual

    # Revenue series drives the income-statement margin rows (value ÷ revenue).
    rev = _df_row(df, "Total Revenue", "Operating Revenue")

    LABEL_W = "185px"

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

    def _change_el(curr, prior):
        """Small % change vs the prior (older) period — horizontal analysis."""
        try:
            curr, prior = float(curr), float(prior)
        except (TypeError, ValueError):
            return None
        if curr != curr or prior != prior or prior == 0:  # NaN / no base
            return None
        ch = (curr - prior) / abs(prior) * 100.0
        color = T4 if abs(ch) < 0.05 else (ACCENT if ch > 0 else RED)
        return html.Div(f"{'+' if ch >= 0 else '-'}{abs(ch):.1f}%", style=dict(
            fontSize="10px", color=color, marginTop="1px",
            fontFamily="'JetBrains Mono',monospace"))

    def _data_row(label, keys, fmt_fn, is_total, no_border=False):
        series = _df_row(df, *keys)
        if series is None:
            return None
        fn = fmt_fn or _fmt_large
        cells = [html.Div(label, style=dict(
            flex=f"0 0 {LABEL_W}", minWidth=LABEL_W, paddingLeft="8px",
            fontSize="12.5px",
            fontWeight="600" if is_total else "400",
            color=T1 if is_total else T3))]
        for idx, c in enumerate(cols):
            try:
                val = series[c]
            except Exception:
                val = None
            cell_children = [html.Div(fn(val, ccy), style=dict(
                fontSize="12.5px",
                fontWeight="600" if is_total else "400",
                color=T1 if is_total else T2,
                fontFamily="'JetBrains Mono',monospace"))]
            if idx + 1 < len(cmp_cols):
                try:
                    prior_val = series[cmp_cols[idx + 1]]
                except Exception:
                    prior_val = None
                ch_el = _change_el(val, prior_val)
                if ch_el is not None:
                    cell_children.append(ch_el)
            cells.append(html.Div(cell_children, style=dict(
                flex="1", display="flex", flexDirection="column",
                alignItems="flex-end")))
        return html.Div(cells, style=dict(
            display="flex", gap="16px",
            padding="5px 0 1px" if no_border else "5px 0",
            borderBottom="none" if no_border else "1px solid rgba(255,255,255,0.03)",
            background="rgba(255,255,255,0.015)" if is_total else "transparent"))

    def _margin_row(label, keys, is_total):
        """Indented '% of revenue' sub-row shown directly beneath a profit line."""
        num = _df_row(df, *keys)
        if num is None or rev is None:
            return None
        series = num / rev.where(rev != 0)  # NaN where revenue is 0
        cells = [html.Div(label, style=dict(
            flex=f"0 0 {LABEL_W}", minWidth=LABEL_W, paddingLeft="20px",
            fontSize="11px", fontStyle="italic", color=T4))]
        for c in cols:
            try:
                v = series[c]
            except Exception:
                v = None
            txt = "—" if v is None or v != v else f"{float(v) * 100:.1f}%"
            cells.append(html.Div(txt, style=dict(
                flex="1", textAlign="right", fontSize="11px", color=T4,
                fontFamily="'JetBrains Mono',monospace")))
        return html.Div(cells, style=dict(
            display="flex", gap="16px", padding="0 0 5px",
            borderBottom="1px solid rgba(255,255,255,0.03)",
            background="rgba(255,255,255,0.015)" if is_total else "transparent"))

    rows = [_hdr_row()]
    any_data = False
    for grp_label, row_defs in groups:
        grp_rows = []
        for row_def in row_defs:
            label, keys, fmt_fn, is_total = row_def[:4]
            margin_label = row_def[4] if len(row_def) > 4 else None
            r = _data_row(label, keys, fmt_fn, is_total, no_border=bool(margin_label))
            if r:
                grp_rows.append(r)
                any_data = True
                if margin_label:
                    m = _margin_row(margin_label, keys, is_total)
                    if m:
                        grp_rows.append(m)
        if grp_rows:
            rows.append(_group_hdr(grp_label))
            rows.extend(grp_rows)

    if not any_data:
        return html.Div()

    return html.Div([
        html.Div(title, style=dict(
            fontSize="12px", fontWeight="700", color=T2,
            letterSpacing="0.3px", marginBottom="10px")),
        html.Div(rows, style=dict(overflowX="auto")),
    ])


# ── Financials metrics (valuation + profitability tiles) ──────────────

def _metric_tile(label, value):
    return html.Div([
        html.Div(label, style=dict(
            fontSize="10.5px", color=T4, fontWeight="500", letterSpacing="0.3px")),
        html.Div(value, style=dict(
            fontSize="15px", fontWeight="600", color=T1, marginTop="5px",
            fontFamily="'JetBrains Mono',monospace")),
    ], style=dict(padding="12px 14px",
                  background="rgba(255,255,255,0.03)",
                  borderRadius="10px",
                  border="1px solid rgba(255,255,255,0.06)"))


def _section_label(text):
    return html.Div(text, style=dict(
        fontSize="11px", fontWeight="600", color=T4,
        letterSpacing="0.5px", textTransform="uppercase", marginBottom="10px"))


def _build_financials_metrics(info: dict) -> html.Div:
    def _v(k):
        return info.get(k)

    prof_items = [
        ("Gross Margin", _fmt_pct(_v("grossMargins"))),
        ("Op. Margin",   _fmt_pct(_v("operatingMargins"))),
        ("Net Margin",   _fmt_pct(_v("profitMargins"))),
        ("ROE",          _fmt_pct(_v("returnOnEquity"))),
        ("ROA",          _fmt_pct(_v("returnOnAssets"))),
        ("Rev Growth",   _fmt_pct(_v("revenueGrowth"))),
        ("Earn. Growth", _fmt_pct(_v("earningsGrowth"))),
        ("Beta",         _fmt_ratio(_v("beta"), "")),
    ]

    def _grid(items):
        live = [(l, v) for l, v in items if v != "—"] or items
        return html.Div([_metric_tile(l, v) for l, v in live],
            style=dict(display="grid",
                       gridTemplateColumns="repeat(auto-fit, minmax(110px, 1fr))",
                       gap="10px"))

    return html.Div([
        _section_label("Profitability & Growth"),
        _grid(prof_items),
    ])


# ── Price card helper ─────────────────────────────────────────────────

def _sec_price_card(label, value, sub="", color=None):
    children = [
        html.Div(label, style=dict(fontSize="11.5px", color=T4, fontWeight="500")),
        html.Div(value, style=dict(
            fontFamily="'Space Grotesk',sans-serif", fontSize="18px",
            fontWeight="600", color=color or T1, marginTop="6px")),
    ]
    if sub:
        children.append(html.Div(sub, style=dict(
            fontSize="11.5px", color=color or T4, marginTop="4px",
            fontFamily="'JetBrains Mono',monospace")))
    return html.Div(children, style=dict(
        background=BG_CARD, border=f"1px solid {BORDER}",
        borderRadius="12px", padding="16px 18px"))


# ── Callbacks ─────────────────────────────────────────────────────────

# ── Toggle button active state ────────────────────────────────────────

@app.callback(
    Output("fin-stmt-income",   "className"),
    Output("fin-stmt-balance",  "className"),
    Output("fin-stmt-cashflow", "className"),
    Input("fin-stmt-store",   "data"),
    Input("sec-detail-store", "data"),
)
def _stmt_btn_classes(stmt, _sym):
    b = "sec-range-btn"
    return (
        f"{b} active" if stmt == "income"   else b,
        f"{b} active" if stmt == "balance"  else b,
        f"{b} active" if stmt == "cashflow" else b,
    )


@app.callback(
    Output("fin-period-annual",    "className"),
    Output("fin-period-quarterly", "className"),
    Input("fin-period-store",  "data"),
    Input("sec-detail-store",  "data"),
)
def _period_btn_classes(period, _sym):
    b = "sec-range-btn"
    return (
        f"{b} active" if period == "annual"    else b,
        f"{b} active" if period == "quarterly" else b,
    )


@app.callback(
    Output("fin-stmt-store", "data"),
    Input("fin-stmt-income",   "n_clicks"),
    Input("fin-stmt-balance",  "n_clicks"),
    Input("fin-stmt-cashflow", "n_clicks"),
    prevent_initial_call=True,
)
def _set_fin_stmt(*_):
    from dash import ctx
    tid = ctx.triggered_id
    return {"fin-stmt-income": "income",
            "fin-stmt-balance": "balance",
            "fin-stmt-cashflow": "cashflow"}.get(tid, no_update)


@app.callback(
    Output("fin-period-store", "data"),
    Input("fin-period-annual",    "n_clicks"),
    Input("fin-period-quarterly", "n_clicks"),
    prevent_initial_call=True,
)
def _set_fin_period(*_):
    from dash import ctx
    tid = ctx.triggered_id
    return {"fin-period-annual": "annual",
            "fin-period-quarterly": "quarterly"}.get(tid, no_update)


# ── Statement table ───────────────────────────────────────────────────

@app.callback(
    Output("sec-fin-table", "children"),
    Input("fin-stmt-store",   "data"),
    Input("fin-period-store", "data"),
    Input("sec-detail-store", "data"),
)
def update_fin_table(stmt, period, symbol):
    if not symbol:
        return html.Div()
    yf_data = _fetch_yf_financials(symbol)
    if not yf_data:
        return html.Div("Financial data unavailable.",
            style=dict(color=T4, fontSize="12.5px", padding="16px 0"))

    prefix  = "q_" if period == "quarterly" else ""
    df      = yf_data.get(f"{prefix}{stmt}")
    groups  = {"income": INCOME_GROUPS,
               "balance": BALANCE_GROUPS,
               "cashflow": CASHFLOW_GROUPS}.get(stmt, INCOME_GROUPS)
    title   = (f"{'Income Statement' if stmt=='income' else 'Balance Sheet' if stmt=='balance' else 'Cash Flow Statement'}"
               f" ({'Quarterly' if period=='quarterly' else 'Annual'})")

    ccy = ccy_symbol((yf_data.get("info") or {}).get("currency"))
    tbl = _stmt_table(title, groups, df, ccy=ccy)
    if not tbl.children:
        return html.Div("No data available for this period.",
            style=dict(color=T4, fontSize="12.5px", padding="16px 0"))
    return tbl


# ── Main security detail ──────────────────────────────────────────────

@app.callback(
    Output("sec-name",             "children"),
    Output("sec-subtitle",          "children"),
    Output("sec-price-header",      "children"),
    Output("sec-position-badge",    "children"),
    Output("sec-price-cards",       "children"),
    Output("sec-financials-metrics","children"),
    Output("sec-financials-card",   "style"),
    Output("sec-news-card",         "children"),
    Output("sec-loading-overlay",   "style", allow_duplicate=True),
    Input("sec-detail-store", "data"),
    prevent_initial_call=True,
)
def populate_security_detail(symbol):
    _HIDE = dict(display="none")
    if not symbol:
        return (no_update,) * 8 + (_HIDE,)

    _CARD_SHOW = dict(background=BG_CARD, border=f"1px solid {BORDER}",
                      borderRadius="14px", padding="20px", display="block")

    # Run yfinance in parallel with the IBKR wait.
    yf_result = {}
    def _do_yf():
        yf_result.update(_fetch_yf_financials(symbol))
    yf_thread = threading.Thread(target=_do_yf, daemon=True)
    yf_thread.start()

    d = _load_detail(symbol)
    if not d:
        yf_thread.join(timeout=1)
        return (no_update,) * 8 + (_HIDE,)

    yf_thread.join(timeout=10)

    info  = d.get("info", {})
    price = d.get("price", {})
    pos   = d.get("position")
    sym   = ccy_symbol(info.get("currency"))

    name = info.get("name", symbol)
    subtitle_parts = [info.get("exchange", ""), info.get("currency", "")]
    if info.get("sector"):
        subtitle_parts.append(info["sector"])
    subtitle = " · ".join(p for p in subtitle_parts if p)

    pos_badge = html.Div()
    if pos and pos.get("shares", 0) > 0:
        sh = pos["shares"]
        sh_str = f"{sh:.0f}" if sh == int(sh) else f"{sh:.2f}"
        pos_badge = html.Div([
            html.Div("Held", style=dict(fontSize="10px", fontWeight="600",
                color=ACCENT, background="rgba(54,211,153,0.13)",
                padding="3px 10px", borderRadius="6px",
                display="inline-block")),
            html.Div(f"{sh_str} shares", style=dict(
                fontSize="12px", color=T3, marginTop="4px",
                textAlign="center")),
        ], style=dict(display="flex", flexDirection="column",
                      alignItems="flex-end"))

    last    = price.get("last", 0)
    chg     = price.get("change", 0)
    chg_pct = price.get("changePct", 0)
    chg_color = ACCENT if chg >= 0 else RED

    price_header = html.Div([
        html.Div(price_money(last, sym), style=dict(
            fontFamily="'JetBrains Mono',monospace",
            fontSize="22px", fontWeight="600", color=T1,
            textAlign="right")),
        html.Div(f"{chg:+.2f}   {chg_pct:+.2f}%", style=dict(
            fontSize="12.5px", color=chg_color,
            fontFamily="'JetBrains Mono',monospace",
            textAlign="right", marginTop="3px")),
    ])

    yf_info = yf_result.get("info", {})
    val_cards = [
        _sec_price_card("Market Cap",   _fmt_large(yf_info.get("marketCap"), sym)),
        _sec_price_card("P/E (TTM)",    _fmt_ratio(yf_info.get("trailingPE"))),
        _sec_price_card("Forward P/E",  _fmt_ratio(yf_info.get("forwardPE"))),
        _sec_price_card("EV/EBITDA",    _fmt_ratio(yf_info.get("enterpriseToEbitda"))),
        _sec_price_card("P/B",          _fmt_ratio(yf_info.get("priceToBook"))),
        _sec_price_card("P/S",          _fmt_ratio(yf_info.get("priceToSalesTrailing12Months"))),
        _sec_price_card("EV/Revenue",   _fmt_ratio(yf_info.get("enterpriseToRevenue"))),
        _sec_price_card("Div Yield",    _fmt_div_yield(yf_info.get("dividendYield"))),
    ]

    fin_metrics = _build_financials_metrics(yf_info)

    news_children = [html.Div("Latest News", style=dict(
        fontSize="13px", fontWeight="600", color=T2, marginBottom="12px"))]
    try:
        sym_news = _fetch_symbol_news(symbol)
        if not sym_news:
            sym_news = _fetch_news()[:3]
        for item in sym_news[:5]:
            link = item.get("link", "")
            title_el = html.A(item.get("title", ""), href=link, target="_blank",
                style=dict(fontSize="13px", fontWeight="500", color=T1,
                    lineHeight="1.4", textDecoration="none"))
            news_children.append(html.Div([
                title_el,
                html.Div(item.get("date", "")[:16], style=dict(
                    fontSize="11px", color=T5, marginTop="3px")),
            ], style=dict(padding="8px 0",
                          borderBottom="1px solid rgba(255,255,255,0.05)")))
    except Exception:
        news_children.append(html.Div("News unavailable",
            style=dict(color=T4, fontSize="12.5px")))
    news_card = html.Div(news_children)

    return (name, subtitle, price_header, pos_badge, val_cards,
            fin_metrics, _CARD_SHOW, news_card, _HIDE)


# Show the loading overlay the instant a symbol is selected — client-side so
# there is no server round-trip before it appears. It stays up until
# populate_security_detail returns and hides it, so the previous security's
# data is never left on screen while the new one loads.
app.clientside_callback(
    """
    function(symbol) {
        if (!symbol) {
            return [{display: 'none'}, ''];
        }
        return [{
            position: 'absolute', top: '0', left: '0', right: '0', bottom: '0',
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: '18px',
            background: '#0a0c10', zIndex: '50'
        }, 'Loading ' + symbol + '…'];
    }
    """,
    Output("sec-loading-overlay", "style"),
    Output("sec-loading-text", "children"),
    Input("sec-detail-store", "data"),
    prevent_initial_call=True,
)


# ── Price chart range ─────────────────────────────────────────────────

# Selecting a range writes the choice to sec-range-store; the chart and the
# button highlight both read from that store so they can't drift out of sync.
# Loading a new security resets the range to the 1Y default.
@app.callback(
    Output("sec-range-store", "data"),
    Input({"type":"sec-range-btn","index":ALL}, "n_clicks"),
    Input("sec-detail-store", "data"),
    prevent_initial_call=True,
)
def _set_sec_range(_btn_clicks, _symbol):
    from dash import ctx
    tid = ctx.triggered_id
    if tid == "sec-detail-store":
        return "1Y"
    if isinstance(tid, dict):
        return tid.get("index", "1Y")
    return no_update


@app.callback(
    Output({"type":"sec-range-btn","index":ALL}, "className"),
    Input("sec-range-store", "data"),
    State({"type":"sec-range-btn","index":ALL}, "id"),
)
def _sec_range_btn_classes(active, ids):
    b = "sec-range-btn"
    active = active or "1Y"
    return [f"{b} active" if i["index"] == active else b for i in ids]


# Kalman overlay toggle — flips the boolean store; the chart callback reads it.
@app.callback(
    Output("sec-kalman-store", "data"),
    Input("sec-kalman-btn", "n_clicks"),
    State("sec-kalman-store", "data"),
    prevent_initial_call=True,
)
def _toggle_kalman(_n, on):
    return not bool(on)


@app.callback(
    Output("sec-kalman-btn", "className"),
    Input("sec-kalman-store", "data"),
)
def _kalman_btn_class(on):
    return "sec-range-btn active" if on else "sec-range-btn"


@app.callback(
    Output("sec-chart-body", "children"),
    Input("sec-range-store", "data"),
    Input("sec-detail-store", "data"),
    Input("sec-kalman-store", "data"),
    prevent_initial_call=True,
)
def on_sec_range_change(range_val, symbol, kalman):
    if not symbol:
        return no_update
    range_val = range_val or "1Y"
    d = _load_detail(symbol)
    if not d:
        return no_update
    bar_map = {
        "1D": d.get("min5_1d", []),
        "5D": d.get("hourly_5d", []),
        "3M": d.get("daily_3m", []),
        "1Y": d.get("daily_1y", []),
        "5Y": d.get("daily_5y", []),
    }
    trades = _get_symbol_trades(symbol)
    ccy = ccy_symbol((d.get("info") or {}).get("currency"))
    return _build_candlestick_chart(
        bar_map.get(range_val, d.get("daily_1y", [])),
        trades=trades, kalman=bool(kalman), ccy=ccy)


# ── DCF button → store ticker ────────────────────────────────────────

@app.callback(
    Output("dcf-ticker-store", "data"),
    Input("sec-dcf-btn", "n_clicks"),
    State("sec-detail-store", "data"),
    prevent_initial_call=True,
)
def _open_dcf(_n, symbol):
    return symbol
