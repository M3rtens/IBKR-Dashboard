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

def _filter_dropdown(cid: str, noun: str):
    """A disclosure (click-to-open) menu of checkboxes.

    Renders as a compact button showing the current selection; clicking it
    opens a checklist you tick / untick in place, rather than editing chips in
    the input. Styled by the ``.filter-dd`` rules in assets/style.css.
    """
    return html.Details([
        html.Summary(html.Span(f"All {noun}", id=f"{cid}-summary")),
        html.Div(dcc.Checklist(id=cid, options=[], value=[]),
                 className="filter-dd-panel"),
    ], className="filter-dd")


def security_page():
    sec_range_store = dcc.Store(id="sec-range-store", data="1Y")
    sec_kalman_store = dcc.Store(id="sec-kalman-store", data=False)
    fin_stmt_store  = dcc.Store(id="fin-stmt-store",  data="income")
    fin_period_store = dcc.Store(id="fin-period-store", data="annual")
    dcf_ticker_store = dcc.Store(id="dcf-ticker-store")
    sec_filings_store = dcc.Store(id="sec-filings-store")

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
        sec_filings_store,
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
        # ── Analyst Ratings card (hidden until a security is loaded) ──
        html.Div(id="sec-analyst-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Institutional Holders card (hidden until a security is loaded) ──
        html.Div(id="sec-institutional-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Earnings Estimates card (hidden until a security is loaded) ──
        html.Div(id="sec-earnings-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Earnings History card (hidden until a security is loaded) ──
        html.Div(id="sec-earnings-history-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Insider Activity card (hidden until a security is loaded) ──
        html.Div(id="sec-insider-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Analyst Revisions card (hidden until a security is loaded) ──
        html.Div(id="sec-revisions-card", style=dict(
            background=BG_CARD, border=f"1px solid {BORDER}",
            borderRadius="14px", padding="20px", display="none")),
        # ── Filings & Announcements card (SEC filings for US names, ASX
        #    announcements for .AX; hidden until a security is loaded) ──
        html.Div([
            html.Div([
                html.Div("Filings & Announcements", id="sec-filings-title",
                    style=dict(fontSize="13px", fontWeight="600", color=T2)),
                html.Div([
                    _filter_dropdown("sec-filings-year", "years"),
                    _filter_dropdown("sec-filings-type", "types"),
                ], style=dict(display="flex", gap="10px", alignItems="center",
                              justifyContent="flex-end")),
            ], style=dict(display="flex", justifyContent="space-between",
                          alignItems="center", gap="16px", marginBottom="16px")),
            html.Div(id="sec-filings-table"),
        ], id="sec-sec-filings-card", style=dict(
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


# ── Shared section primitives ─────────────────────────────────────────
# Every section below the financial statements renders as a card: a title,
# then one or more sub-blocks (a labelled group of tiles, or a table). These
# helpers keep spacing, colour and typography identical across all of them.

_TILE_BG     = "rgba(255,255,255,0.03)"
_TILE_BORDER = "1px solid rgba(255,255,255,0.06)"
_ROW_BORDER  = "1px solid rgba(255,255,255,0.03)"
_HDR_BORDER  = "1px solid rgba(255,255,255,0.08)"
_MONO        = "'JetBrains Mono',monospace"


def _card_title(title, meta=None):
    """Card heading, matching the 'Financials' / 'Price Chart' card titles."""
    left = html.Div(title, style=dict(fontSize="13px", fontWeight="600", color=T2))
    if meta is None:
        return left
    return html.Div([left, html.Div(meta, style=dict(fontSize="12px", color=T4))],
        style=dict(display="flex", justifyContent="space-between",
                   alignItems="baseline"))


def _card(title, blocks, meta=None):
    """Wrap a card title and its sub-blocks with consistent vertical spacing."""
    return html.Div([_card_title(title, meta), *blocks],
        style=dict(display="flex", flexDirection="column", gap="18px"))


def _sub_block(label, body):
    """A labelled sub-section (section label + body) inside a card."""
    return html.Div([_section_label(label), body])


def _stat_tile(label, value, color=None, sub=None):
    """Single metric tile: label, a mono value, and an optional sub-line."""
    children = [
        html.Div(label, style=dict(
            fontSize="10.5px", color=T4, fontWeight="500", letterSpacing="0.3px")),
        html.Div(value, style=dict(
            fontSize="15px", fontWeight="600", color=color or T1, marginTop="5px",
            fontFamily=_MONO)),
    ]
    if sub:
        children.append(html.Div(sub, style=dict(
            fontSize="10px", color=T5, marginTop="3px")))
    return html.Div(children, style=dict(
        padding="12px 14px", background=_TILE_BG,
        borderRadius="10px", border=_TILE_BORDER))


def _tile_grid(tiles, min_w="120px"):
    return html.Div(tiles, style=dict(
        display="grid",
        gridTemplateColumns=f"repeat(auto-fit, minmax({min_w}, 1fr))",
        gap="10px"))


def _table(columns, rows, max_height=None):
    """Compact list table shared by every holder / estimate / history block.

    ``columns`` is a list of ``(label, flex, align)`` tuples; ``rows`` is a list
    of rows, each a list of cells aligned to ``columns``. A cell may be a plain
    string, a ``(text, colour)`` tuple, or a Dash component (e.g. a link).
    """
    header = html.Div([
        html.Div(lbl, style=dict(flex=flex, fontSize="10.5px", fontWeight="600",
            color=T4, textAlign=align, letterSpacing="0.5px"))
        for lbl, flex, align in columns
    ], style=dict(display="flex", gap="12px", padding="6px 0 10px",
                  borderBottom=_HDR_BORDER))

    row_els = []
    for r in rows:
        cells = []
        for (lbl, flex, align), cell in zip(columns, r):
            text, color = cell if isinstance(cell, tuple) else (cell, None)
            cells.append(html.Div(text, style=dict(
                flex=flex, fontSize="12.5px", color=color or T2, textAlign=align,
                fontFamily=_MONO if align == "right" else "inherit",
                overflow="hidden", textOverflow="ellipsis", whiteSpace="nowrap")))
        row_els.append(html.Div(cells, style=dict(
            display="flex", gap="12px", padding="8px 0", borderBottom=_ROW_BORDER)))

    body_style = dict(overflowY="auto", maxHeight=max_height) if max_height else {}
    return html.Div([header, html.Div(row_els, style=body_style)])


# ── Analyst ratings section ──────────────────────────────────────────

def _build_analyst_ratings(yf_data: dict) -> html.Div:
    """Recommendation distribution (with consensus + bar) and price targets."""
    recommendations = yf_data.get("recommendations")
    price_targets = yf_data.get("analyst_price_targets")

    blocks = []
    total = 0

    # Recommendation distribution — latest month is the first row.
    if recommendations is not None and not (hasattr(recommendations, "empty") and recommendations.empty):
        try:
            latest = recommendations.iloc[0]
            ratings = {
                "Strong Buy":  int(latest.get("strongBuy", 0)),
                "Buy":         int(latest.get("buy", 0)),
                "Hold":        int(latest.get("hold", 0)),
                "Sell":        int(latest.get("sell", 0)),
                "Strong Sell": int(latest.get("strongSell", 0)),
            }
            total = sum(ratings.values())
        except Exception:
            ratings, total = {}, 0

        if total > 0:
            colors = {"Strong Buy": "#22c55e", "Buy": "#4ade80", "Hold": "#facc15",
                      "Sell": "#fb923c", "Strong Sell": "#ef4444"}
            weighted = sum(w * ratings[k] for k, w in (
                ("Strong Buy", 5), ("Buy", 4), ("Hold", 3),
                ("Sell", 2), ("Strong Sell", 1))) / total
            consensus = ("Strong Buy" if weighted >= 4.2 else "Buy" if weighted >= 3.5
                         else "Hold" if weighted >= 2.5 else "Sell" if weighted >= 1.5
                         else "Strong Sell")
            bar = html.Div([
                html.Div(style=dict(width=f"{ratings[k] / total * 100:.2f}%",
                    background=colors[k])) for k in ratings if ratings[k] > 0
            ], style=dict(display="flex", gap="2px", height="8px",
                          borderRadius="4px", overflow="hidden",
                          background="rgba(255,255,255,0.04)"))
            consensus_pill = html.Div([
                html.Span("Consensus", style=dict(fontSize="10.5px", color=T4,
                    textTransform="uppercase", letterSpacing="0.5px")),
                html.Span(consensus, style=dict(fontSize="13px", fontWeight="600",
                    color=colors[consensus], marginLeft="8px")),
            ])
            tiles = _tile_grid(
                [_stat_tile(k, str(ratings[k]), color=colors[k]) for k in ratings],
                min_w="90px")
            blocks.append(_sub_block("Recommendations", html.Div([
                consensus_pill,
                html.Div(bar, style=dict(margin="12px 0 14px")),
                tiles,
            ])))

    # Price targets — analyst_price_targets is a dict of dollar figures.
    if price_targets and isinstance(price_targets, dict):
        def _num(v):
            try:
                f = float(v)
                return None if f != f else f
            except (TypeError, ValueError):
                return None
        cur, mean, median, high, low = (
            _num(price_targets.get(k))
            for k in ("current", "mean", "median", "high", "low"))
        if any(v is not None for v in (cur, mean, median, high, low)):
            def _p(v):
                return "—" if v is None else f"${v:,.2f}"
            upside = ((mean - cur) / cur * 100) if (mean is not None and cur) else None
            up_color = ACCENT if (upside or 0) >= 0 else RED
            up_sub = f"{upside:+.1f}% vs current" if upside is not None else None
            blocks.append(_sub_block("Price Targets", _tile_grid([
                _stat_tile("Current", _p(cur)),
                _stat_tile("Mean", _p(mean), color=up_color, sub=up_sub),
                _stat_tile("Median", _p(median)),
                _stat_tile("High", _p(high), color=ACCENT),
                _stat_tile("Low", _p(low), color=RED),
            ], min_w="105px")))

    if not blocks:
        return html.Div()
    return _card("Analyst Ratings", blocks,
                 meta=f"{total} analysts" if total else None)


# ── Institutional holders section ────────────────────────────────────

def _build_institutional_holders(yf_data: dict) -> html.Div:
    """Ownership breakdown plus top institutional and mutual-fund holders."""
    info = yf_data.get("info", {})
    holders = yf_data.get("institutional_holders")
    major = yf_data.get("major_holders")
    funds = yf_data.get("mutualfund_holders")

    def _has(df):
        return df is not None and not (hasattr(df, "empty") and df.empty)

    has_holders, has_major, has_funds = _has(holders), _has(major), _has(funds)
    if not (has_holders or has_major or has_funds):
        return html.Div()

    shares_out = info.get("sharesOutstanding")

    # major_holders is indexed by breakdown key (insidersPercentHeld,
    # institutionsPercentHeld, institutionsFloatPercentHeld, institutionsCount)
    # with a single 'Value' column — not a row per breakdown.
    insider_pct = inst_pct = float_pct = inst_count = None
    if has_major:
        try:
            col = "Value" if "Value" in major.columns else major.columns[0]
            vals = {str(k): major.loc[k, col] for k in major.index}

            def _pct(key):
                try:
                    return float(vals.get(key)) * 100
                except (TypeError, ValueError):
                    return None
            insider_pct = _pct("insidersPercentHeld")
            inst_pct = _pct("institutionsPercentHeld")
            float_pct = _pct("institutionsFloatPercentHeld")
            try:
                c = float(vals.get("institutionsCount"))
                inst_count = int(c) if c == c else None
            except (TypeError, ValueError):
                inst_count = None
        except Exception:
            pass

    # Fallback: derive institutional % from the holdings table if needed.
    if inst_pct is None and has_holders and shares_out and "Shares" in holders.columns:
        try:
            inst_pct = holders["Shares"].sum() / shares_out * 100
        except Exception:
            pass

    blocks = []

    breakdown = []
    if inst_pct is not None:
        breakdown.append(_stat_tile("Institutional", f"{inst_pct:.1f}%"))
    if float_pct is not None:
        breakdown.append(_stat_tile("Inst. of Float", f"{float_pct:.1f}%"))
    if insider_pct is not None:
        breakdown.append(_stat_tile("Insiders", f"{insider_pct:.2f}%"))
    if inst_count is not None:
        breakdown.append(_stat_tile("# Institutions", f"{inst_count:,}"))
    if breakdown:
        blocks.append(_sub_block("Ownership Breakdown",
            _tile_grid(breakdown, min_w="130px")))

    def _holder_rows(df):
        rows = []
        for _, r in df.iterrows():
            name = str(r.get("Holder", ""))
            shares, value = r.get("Shares"), r.get("Value")
            try:
                sh = f"{float(shares) / 1e6:.1f}M"
            except (TypeError, ValueError):
                sh = "—"
            try:
                po = f"{float(r.get('pctHeld')) * 100:.2f}%"
            except (TypeError, ValueError):
                po = (f"{float(shares) / shares_out * 100:.2f}%"
                      if shares_out and shares else "—")
            try:
                v = float(value)
                vv = f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"
            except (TypeError, ValueError):
                vv = "—"
            try:
                cv = float(r.get("pctChange")) * 100
                chg = (f"{cv:+.2f}%", ACCENT if cv >= 0 else RED)
            except (TypeError, ValueError):
                chg = ("—", T4)
            rows.append([name, sh, po, vv, chg])
        return rows

    cols = [("Holder", "2", "left"), ("Shares", "1", "right"),
            ("% Out", "1", "right"), ("Value", "1", "right"),
            ("Chg", "0 0 70px", "right")]
    if has_holders:
        blocks.append(_sub_block("Top Institutional Holders",
            _table(cols, _holder_rows(holders), max_height="280px")))
    if has_funds:
        blocks.append(_sub_block("Top Mutual Fund Holders",
            _table([("Fund", "2", "left"), *cols[1:]],
                   _holder_rows(funds), max_height="280px")))

    if not blocks:
        return html.Div()
    return _card("Ownership & Holders", blocks)


# ── Earnings estimates section ───────────────────────────────────────

def _build_earnings_estimates(yf_data: dict) -> html.Div:
    """Upcoming earnings date plus EPS and revenue estimate tables."""
    earnings_est = yf_data.get("earnings_estimate")
    revenue_est = yf_data.get("revenue_estimate")
    calendar = yf_data.get("calendar")

    def _has(df):
        return df is not None and not (hasattr(df, "empty") and df.empty)

    blocks = []

    # Upcoming earnings — from the calendar dict.
    if calendar and isinstance(calendar, dict):
        ed = calendar.get("Earnings Date")
        e_lo, e_hi = calendar.get("Earnings Low"), calendar.get("Earnings High")
        r_lo, r_hi = calendar.get("Revenue Low"), calendar.get("Revenue High")
        date_display = "—"
        if ed:
            try:
                d0 = ed[0] if isinstance(ed, list) else ed
                date_display = (d0.strftime("%b %d, %Y")
                                if hasattr(d0, "strftime") else str(d0)[:10])
            except Exception:
                date_display = str(ed)[:10]
        # The calendar ranges are for the upcoming quarter, so the reference
        # must be the same quarter a year ago (from the current-quarter '0q'
        # estimate row) — not a trailing-twelve-month figure.
        def _year_ago(df, col):
            try:
                if df is not None and "0q" in df.index:
                    v = float(df.loc["0q", col])
                    return None if v != v else v
            except (TypeError, ValueError, KeyError):
                pass
            return None
        ya_eps = _year_ago(earnings_est, "yearAgoEps")
        ya_rev = _year_ago(revenue_est, "yearAgoRevenue")
        eps_sub = f"Year-ago Q: ${ya_eps:.2f}" if ya_eps is not None else None
        rev_sub = f"Year-ago Q: {_fmt_large(ya_rev)}" if ya_rev is not None else None

        cal_tiles = [_stat_tile("Next Earnings", date_display, color=ACCENT)]
        if e_lo and e_hi:
            cal_tiles.append(_stat_tile("Next-Q EPS Est.",
                f"${e_lo:.2f} – ${e_hi:.2f}", sub=eps_sub))
        if r_lo and r_hi:
            cal_tiles.append(_stat_tile("Next-Q Revenue Est.",
                f"{_fmt_large(r_lo)} – {_fmt_large(r_hi)}", sub=rev_sub))
        blocks.append(_sub_block("Upcoming", _tile_grid(cal_tiles, min_w="150px")))

    def _est_rows(df, is_rev):
        labels = {"0q": "Current Quarter", "+1q": "Next Quarter",
                  "0y": "Current Year", "+1y": "Next Year", "LTG": "Long-term"}
        rows = []
        for idx in df.index:
            r = df.loc[idx]

            def _v(x):
                try:
                    f = float(x)
                    if f != f:
                        return "—"
                    return _fmt_large(f) if is_rev else f"${f:.2f}"
                except (TypeError, ValueError):
                    return "—"
            try:
                gf = float(r.get("growth")) * 100
                gcell = (("—", T4) if gf != gf
                         else (f"{gf:+.1f}%", ACCENT if gf >= 0 else RED))
            except (TypeError, ValueError):
                gcell = ("—", T4)
            try:
                nn = str(int(float(r.get("numberOfAnalysts"))))
            except (TypeError, ValueError):
                nn = "—"
            rows.append([labels.get(str(idx), str(idx)), (_v(r.get("avg")), T1),
                         _v(r.get("low")), _v(r.get("high")), nn, gcell])
        return rows

    ecols = [("Period", "0 0 120px", "left"), ("Avg", "1", "right"),
             ("Low", "1", "right"), ("High", "1", "right"),
             ("# Est", "0 0 55px", "right"), ("Growth", "1", "right")]
    if _has(earnings_est):
        blocks.append(_sub_block("EPS Estimates",
            _table(ecols, _est_rows(earnings_est, False))))
    if _has(revenue_est):
        blocks.append(_sub_block("Revenue Estimates",
            _table(ecols, _est_rows(revenue_est, True))))

    if not blocks:
        return html.Div()
    return _card("Earnings Estimates", blocks)


# ── Earnings history section ─────────────────────────────────────────

def _eps_surprise_chart(recs: list) -> "dcc.Graph":
    """Grouped estimate-vs-actual EPS bars, actual coloured green (beat) or
    red (miss), with the surprise % labelled above each quarter.

    ``recs`` is drawn in the order given (most-recent first → newest on the
    left); each is a dict with label / estimate / actual / surprise.
    """
    import plotly.graph_objects as go

    x = [r["label"] for r in recs]
    est = [r["estimate"] for r in recs]
    act = [r["actual"] for r in recs]
    beat = [(a is not None and e is not None and a >= e) for a, e in zip(act, est)]
    act_colors = [ACCENT if b else RED for b in beat]
    labels = [("" if r["surprise"] is None else f"{r['surprise'] * 100:+.1f}%")
              for r in recs]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=est, name="Estimate",
        marker_color="rgba(255,255,255,0.20)",
        hovertemplate="Estimate: $%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Bar(
        x=x, y=act, name="Actual", marker_color=act_colors,
        text=labels, textposition="outside", cliponaxis=False,
        textfont=dict(family="JetBrains Mono, monospace", size=10, color=T3),
        hovertemplate="Actual: $%{y:.2f}<extra></extra>"))
    fig.update_layout(
        template="plotly_dark", barmode="group", bargap=0.35, bargroupgap=0.1,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=44, r=12, t=22, b=28), height=220,
        showlegend=True,
        legend=dict(orientation="h", x=0, y=1.08, xanchor="left", yanchor="bottom",
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=T3)),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1b1f28", bordercolor="rgba(255,255,255,0.1)",
                        font=dict(family="JetBrains Mono, monospace", color=T1, size=12)),
        xaxis=dict(showgrid=False, color=T4, tickfont=dict(size=10)),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", zeroline=False,
                   color=T4, tickfont=dict(size=10), tickprefix="$"),
        font=dict(family="JetBrains Mono, monospace", color=T3))
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style=dict(height="220px"))


def _earnings_events(yf_data: dict, recs: list) -> list:
    """Past earnings releases as {date_str, beat, surprise_pct, label}.

    Prefers ``earnings_dates`` (accurate release datetimes + reported/estimate),
    merging in newer ``earnings_history`` quarter-end dates the feed lacks.
    """
    import pandas as pd

    def _num(x):
        try:
            v = float(x)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    ed = yf_data.get("earnings_dates")
    events = []
    if ed is not None and not (hasattr(ed, "empty") and ed.empty):
        try:
            for idx in ed.index:
                r = ed.loc[idx]
                rep = _num(r.get("Reported EPS"))
                est = _num(r.get("EPS Estimate"))
                if rep is None:            # future / not yet reported
                    continue
                spv = _num(r.get("Surprise(%)"))   # already a percentage
                if est is not None:
                    beat = rep >= est
                elif spv is not None:
                    beat = spv >= 0
                else:
                    beat = True
                ds = (idx.strftime("%Y-%m-%d")
                      if hasattr(idx, "strftime") else str(idx)[:10])
                lbl = (idx.strftime("%b %d, %Y")
                       if hasattr(idx, "strftime") else ds)
                events.append({"date_str": ds, "beat": bool(beat),
                               "surprise_pct": spv, "label": lbl})
        except Exception:
            events = []

    # yfinance's ``earnings_dates`` feed lags — its newest reported release can
    # be ~a year old, while ``earnings_history`` carries the most recent quarters
    # (at fiscal quarter-end dates). Merge in any history records newer than the
    # last dates-feed event so the recent releases still get markers.
    last_ev = None
    if events:
        try:
            last_ev = max(pd.to_datetime(e["date_str"]) for e in events)
        except Exception:
            last_ev = None

    for d in recs:
        if d["actual"] is None or d["estimate"] is None:
            continue
        dt = d["date"]
        dts = pd.to_datetime(dt)
        if last_ev is not None and dts <= last_ev:
            continue                         # already covered by the dates feed
        events.append({
            "date_str": (dt.strftime("%Y-%m-%d")
                         if hasattr(dt, "strftime") else str(dt)[:10]),
            "beat": d["actual"] >= d["estimate"],
            "surprise_pct": (d["surprise"] * 100
                             if d["surprise"] is not None else None),
            "label": d["label"],
        })
    return events


def _earnings_price_chart(price_bars: list, events: list, ccy: str = "$"):
    """Price line over the earnings window with a dashed marker at each release
    (green = beat, red = miss), so pre-event action and post drift are visible.
    """
    import plotly.graph_objects as go
    import pandas as pd

    pts = [(b.get("date"), b.get("close")) for b in (price_bars or [])
           if b.get("close") is not None and b.get("date")]
    if len(pts) < 2:
        return None
    xs_all = pd.to_datetime([p[0] for p in pts])
    ys_all = [float(p[1]) for p in pts]

    # Focus the window on the span of the earnings events (+ padding for
    # pre-event action and post-earnings drift). yfinance's earnings feed can
    # lag the price feed, so a fixed trailing window may contain no releases —
    # clipping to the events guarantees the markers are visible.
    ev_dates = [pd.to_datetime(e["date_str"]) for e in events]
    ev_in = [d for d in ev_dates if xs_all.min() <= d <= xs_all.max()]
    if ev_in:
        lo_clip = min(ev_in) - pd.Timedelta(days=30)
        # Extend to the latest available price so the line runs to today —
        # yfinance's earnings feed lags the price feed, so clipping to the last
        # release would end the chart ~a year short of the current date.
        hi_clip = max(max(ev_in) + pd.Timedelta(days=60), xs_all.max())
        clipped = [(x, y) for x, y in zip(xs_all, ys_all)
                   if lo_clip <= x <= hi_clip]
        xs, ys = ((pd.DatetimeIndex([c[0] for c in clipped]),
                   [c[1] for c in clipped]) if len(clipped) >= 2
                  else (xs_all, ys_all))
    else:
        xs, ys = xs_all, ys_all

    lo_x, hi_x = xs.min(), xs.max()
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.08 or 1.0
    y0, y1 = ymin - pad, ymax + pad
    close_by = dict(zip(xs, ys))
    ordered_x = list(xs)

    fig = go.Figure()
    # Event lines first, so the price line sits on top of them.
    for e in events:
        d = pd.to_datetime(e["date_str"])
        if d < lo_x or d > hi_x:
            continue
        fig.add_trace(go.Scatter(
            x=[d, d], y=[y0, y1], mode="lines",
            line=dict(color=ACCENT if e["beat"] else RED, width=1, dash="dot"),
            opacity=0.5, hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines", line=dict(color="#7fb2ff", width=1.6),
        hovertemplate="%{x|%d %b %Y}<br>" + ccy + "%{y:.2f}<extra></extra>",
        showlegend=False))
    # A dot on the price line at each release, with a beat/miss hover label.
    mx, my, mc, mt = [], [], [], []
    for e in events:
        d = pd.to_datetime(e["date_str"])
        if d < lo_x or d > hi_x:
            continue
        prior = [x for x in ordered_x if x <= d]
        px = prior[-1] if prior else next((x for x in ordered_x if x >= d), None)
        if px is None:
            continue
        mx.append(px); my.append(close_by[px])
        mc.append(ACCENT if e["beat"] else RED)
        res = "Beat" if e["beat"] else "Missed"
        sp = e.get("surprise_pct")
        mt.append(f"{e['label']} · {res}"
                  + (f" ({sp:+.1f}%)" if sp is not None else ""))
    if mx:
        fig.add_trace(go.Scatter(
            x=mx, y=my, mode="markers",
            marker=dict(size=9, color=mc, line=dict(width=1.5, color="#0a0c10")),
            hovertext=mt, hoverinfo="text", showlegend=False))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=44, r=12, t=10, b=28), height=200, showlegend=False,
        hoverlabel=dict(bgcolor="#1b1f28", bordercolor="rgba(255,255,255,0.1)",
                        font=dict(family="JetBrains Mono, monospace", color=T1, size=12)),
        xaxis=dict(showgrid=False, color=T4, tickfont=dict(size=10),
                   range=[lo_x, hi_x]),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", zeroline=False,
                   color=T4, tickfont=dict(size=10), tickprefix=ccy, range=[y0, y1]),
        font=dict(family="JetBrains Mono, monospace", color=T3))
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style=dict(height="200px"))


def _build_earnings_history(yf_data: dict, price_bars: list = None) -> html.Div:
    """Price-with-earnings-markers chart, estimate-vs-actual surprise chart,
    and the recent history table."""
    history = yf_data.get("earnings_history")
    if history is None or (hasattr(history, "empty") and history.empty):
        return html.Div()

    try:
        def _f(x):
            try:
                v = float(x)
                return None if v != v else v
            except (TypeError, ValueError):
                return None

        recs = []
        for idx in history.index:
            r = history.loc[idx]
            recs.append({
                "date": idx,
                "label": (idx.strftime("%b '%y")
                          if hasattr(idx, "strftime") else str(idx)[:10]),
                "estimate": _f(r.get("epsEstimate")),
                "actual": _f(r.get("epsActual")),
                "surprise": _f(r.get("surprisePercent")),
            })

        ccy = ccy_symbol((yf_data.get("info") or {}).get("currency"))
        blocks = []

        # Price around earnings — chronological (old→new), events marked.
        events = _earnings_events(yf_data, recs)
        if price_bars and events:
            price_chart = _earnings_price_chart(price_bars, events, ccy=ccy)
            if price_chart is not None:
                blocks.append(_sub_block("Price Around Earnings", price_chart))

        # Estimate vs actual — newest on the LEFT, oldest on the right.
        try:
            chart_recs = sorted(recs, key=lambda d: d["date"], reverse=True)
        except Exception:
            chart_recs = list(recs)
        chart_recs = [d for d in chart_recs
                      if d["estimate"] is not None or d["actual"] is not None]
        if chart_recs:
            blocks.append(_sub_block("Estimate vs Actual EPS",
                _eps_surprise_chart(chart_recs)))

        # Table — as delivered (most recent first).
        def _eps(v):
            return "—" if v is None else f"${v:.2f}"
        trows = []
        for d in recs:
            sp = d["surprise"]
            scell = (("—", T4) if sp is None
                     else (f"{sp * 100:+.1f}%", ACCENT if sp >= 0 else RED))
            trows.append([d["label"], (_eps(d["actual"]), T1),
                          _eps(d["estimate"]), scell])
        cols = [("Quarter", "1", "left"), ("Actual", "1", "right"),
                ("Estimate", "1", "right"), ("Surprise", "1", "right")]
        blocks.append(_sub_block("History", _table(cols, trows)))

        return _card("Earnings History", blocks)
    except Exception:
        return html.Div()


# ── Insider activity section ─────────────────────────────────────────

def _buy_sell_bar(buy, sell):
    """Split bar summarising 6-month insider flow: green = shares bought,
    red = shares sold, with the counts labelled on each side."""
    buy, sell = max(buy or 0, 0), max(sell or 0, 0)
    total = buy + sell
    if total <= 0:
        return None

    def _fmt(v):
        return f"{v / 1e6:,.1f}M" if v >= 1e6 else f"{v / 1e3:,.0f}K"
    caption = html.Div([
        html.Span([html.Span("Bought ", style=dict(color=T4)),
                   html.Span(_fmt(buy), style=dict(color=ACCENT, fontWeight="600"))]),
        html.Span([html.Span(_fmt(sell), style=dict(color=RED, fontWeight="600")),
                   html.Span(" Sold", style=dict(color=T4))]),
    ], style=dict(display="flex", justifyContent="space-between",
                  fontFamily=_MONO, fontSize="11.5px", marginBottom="6px"))
    bar = html.Div([
        html.Div(style=dict(width=f"{buy / total * 100:.1f}%", background=ACCENT)),
        html.Div(style=dict(width=f"{sell / total * 100:.1f}%", background=RED)),
    ], style=dict(display="flex", height="8px", borderRadius="4px",
                  overflow="hidden", background="rgba(255,255,255,0.06)"))
    return html.Div([caption, bar])


def _build_insider_activity(yf_data: dict) -> html.Div:
    """Six-month insider buy/sell summary plus recent transactions."""
    purchases = yf_data.get("insider_purchases")
    transactions = yf_data.get("insider_transactions")

    def _has(df):
        return df is not None and not (hasattr(df, "empty") and df.empty)

    blocks = []

    # 6-month summary — the first column holds the row labels (Purchases,
    # Sales, Net Shares Purchased (Sold), Total Insider Shares Held, …).
    if _has(purchases):
        try:
            tiles = []
            buy_sh = sell_sh = None
            label_col = purchases.columns[0]
            for _, r in purchases.iterrows():
                label = str(r.get(label_col, ""))
                low = label.lower()
                if not label or low.startswith("%"):
                    continue  # percentage rows aren't share counts
                try:
                    shv = float(r.get("Shares"))
                except (TypeError, ValueError):
                    continue
                if shv != shv:
                    continue
                if "sale" in low:
                    color = RED
                    sell_sh = abs(shv)
                elif "purchase" in low and "net" not in low:
                    color = ACCENT
                    buy_sh = abs(shv)
                elif "net" in low:
                    color = ACCENT if shv >= 0 else RED
                else:
                    color = T1
                sub = None
                try:
                    sub = f"{int(float(r.get('Trans')))} transactions"
                except (TypeError, ValueError):
                    pass
                tiles.append(_stat_tile(label, f"{shv / 1e3:,.0f}K Shares",
                                        color=color, sub=sub))
            if tiles:
                body = [_tile_grid(tiles, min_w="150px")]
                bar = _buy_sell_bar(buy_sh, sell_sh)
                if bar is not None:
                    body.append(bar)
                blocks.append(_sub_block("Insider Activity (6 months)",
                    html.Div(body, style=dict(display="flex",
                             flexDirection="column", gap="14px"))))
        except Exception:
            pass

    # Recent transactions — the date column is 'Start Date'.
    if _has(transactions):
        try:
            rows = []
            for _, r in transactions.head(12).iterrows():
                sd = r.get("Start Date")
                date_display = "—"
                if sd is not None and sd == sd:
                    try:
                        date_display = (sd.strftime("%b %d, %Y")
                                        if hasattr(sd, "strftime") else str(sd)[:10])
                    except Exception:
                        date_display = str(sd)[:10]
                insider = str(r.get("Insider") or "")
                ttype = str(r.get("Text") or r.get("Transaction") or "—")
                try:
                    shv = float(r.get("Shares"))
                    scell = ((f"{shv:+,.0f}", ACCENT if shv >= 0 else RED)
                             if shv and shv == shv else ("—", T4))
                except (TypeError, ValueError):
                    scell = ("—", T4)
                # A 0 value means the price wasn't reported (option exercises,
                # gifts, Form 4 entries without a price) — not a $0 trade.
                try:
                    val = float(r.get("Value"))
                    vv = "—" if (val != val or val == 0) else f"${val:,.0f}"
                except (TypeError, ValueError):
                    vv = "—"
                rows.append([date_display, (insider, T2), (ttype, T3), scell, vv])
            cols = [("Date", "0 0 90px", "left"), ("Insider", "1.4", "left"),
                    ("Type", "1", "left"), ("Shares", "1", "right"),
                    ("Value", "1", "right")]
            blocks.append(_sub_block("Recent Transactions",
                _table(cols, rows, max_height="300px")))
        except Exception:
            pass

    if not blocks:
        return html.Div()
    return _card("Insider Activity", blocks)


# ── Analyst revisions section ────────────────────────────────────────

def _build_analyst_revisions(yf_data: dict) -> html.Div:
    """EPS estimate revisions (raised/cut), EPS trend, and stock-vs-index growth."""
    eps_rev = yf_data.get("eps_revisions")
    eps_tr = yf_data.get("eps_trend")
    growth = yf_data.get("growth_estimates")

    def _has(df):
        return df is not None and not (hasattr(df, "empty") and df.empty)

    P = {"0q": "Current Quarter", "+1q": "Next Quarter",
         "0y": "Current Year", "+1y": "Next Year",
         "+5y": "Long-term", "-5y": "Past 5y", "LTG": "Long-term"}
    blocks = []

    def _mini(label, text, color):
        return html.Div([
            html.Div(label, style=dict(fontSize="9px", color=T5, letterSpacing="0.5px")),
            html.Div(text, style=dict(fontSize="13px", fontWeight="600",
                color=color, fontFamily=_MONO)),
        ], style=dict(textAlign="center", padding="6px 8px", flex="1",
                      background=_TILE_BG, borderRadius="6px"))

    def _period_card(idx, minis):
        return html.Div([
            html.Div(P.get(str(idx), str(idx)), style=dict(
                fontSize="12px", color=T2, fontWeight="500", marginBottom="8px")),
            html.Div(minis, style=dict(display="flex", gap="6px")),
        ], style=dict(padding="10px 12px", background="rgba(255,255,255,0.02)",
                      borderRadius="8px", border="1px solid rgba(255,255,255,0.04)"))

    # EPS revisions — how many analysts raised vs cut their estimate, shown
    # explicitly (↑ raised / ↓ cut) with a split bar, over 30 and 7 days.
    def _rev_window(label, up, dn):
        total = up + dn
        bar = html.Div([
            html.Div(style=dict(width=f"{(up / total * 100) if total else 0:.0f}%",
                                background=ACCENT)),
            html.Div(style=dict(width=f"{(dn / total * 100) if total else 0:.0f}%",
                                background=RED)),
        ], style=dict(display="flex", height="5px", borderRadius="3px",
                      overflow="hidden", marginTop="5px",
                      background="rgba(255,255,255,0.06)"))
        head = html.Div([
            html.Span(label, style=dict(fontSize="9.5px", color=T5,
                fontWeight="600", letterSpacing="0.5px")),
            html.Span([
                html.Span(f"↑ {up}", style=dict(color=ACCENT if up else T4,
                    fontWeight="600")),
                html.Span(f"↓ {dn}", style=dict(color=RED if dn else T4,
                    fontWeight="600", marginLeft="12px")),
            ], style=dict(fontFamily=_MONO, fontSize="11.5px")),
        ], style=dict(display="flex", justifyContent="space-between",
                      alignItems="center"))
        return html.Div([head, bar])

    def _rev_card(idx, up7, dn7, up30, dn30):
        if up7 + dn7 + up30 + dn30 == 0:
            body = html.Div("No recent revisions", style=dict(
                fontSize="11px", color=T5, fontStyle="italic", padding="6px 0"))
        else:
            body = html.Div([_rev_window("30 DAYS", up30, dn30),
                             _rev_window("7 DAYS", up7, dn7)],
                style=dict(display="flex", flexDirection="column", gap="10px"))
        return html.Div([
            html.Div(P.get(str(idx), str(idx)), style=dict(
                fontSize="12px", color=T2, fontWeight="600", marginBottom="10px")),
            body,
        ], style=dict(padding="12px 14px", background="rgba(255,255,255,0.02)",
                      borderRadius="8px", border="1px solid rgba(255,255,255,0.04)"))

    if _has(eps_rev):
        try:
            def _i(r, k):
                try:
                    return int(r.get(k, 0))
                except (TypeError, ValueError):
                    return 0
            items = [_rev_card(idx,
                        _i(eps_rev.loc[idx], "upLast7days"),
                        _i(eps_rev.loc[idx], "downLast7days"),
                        _i(eps_rev.loc[idx], "upLast30days"),
                        _i(eps_rev.loc[idx], "downLast30days"))
                     for idx in eps_rev.index]
            blocks.append(_sub_block("EPS Estimate Revisions (↑ raised · ↓ cut)",
                _tile_grid(items, min_w="190px")))
        except Exception:
            pass

    # EPS trend — current estimate vs 90 days ago.
    if _has(eps_tr):
        try:
            rows = []
            for idx in eps_tr.index:
                r = eps_tr.loc[idx]
                try:
                    cf = float(r.get("current"))
                    cur_s = "—" if cf != cf else f"${cf:.2f}"
                except (TypeError, ValueError):
                    cf, cur_s = None, "—"
                try:
                    af = float(r.get("90daysAgo"))
                    if cf is None or af != af or af == 0:
                        chg = ("—", T4)
                    else:
                        d = (cf - af) / abs(af) * 100
                        chg = (f"{d:+.1f}%", ACCENT if d >= 0 else RED)
                except (TypeError, ValueError):
                    chg = ("—", T4)
                rows.append([P.get(str(idx), str(idx)), (cur_s, T1), chg])
            cols = [("Period", "1", "left"), ("Current Est.", "1", "right"),
                    ("90D Change", "1", "right")]
            blocks.append(_sub_block("EPS Trend", _table(cols, rows)))
        except Exception:
            pass

    # Growth estimates — stock vs index (columns are 'stock' / 'index').
    if _has(growth):
        try:
            gcols = list(growth.columns)
            stock_col = next((c for c in ("stock", "stockTrend") if c in gcols), None)
            index_col = next((c for c in ("index", "indexTrend") if c in gcols), None)

            def _gmini(label, val):
                try:
                    f = float(val) * 100
                    return _mini(label, f"{f:+.1f}%", ACCENT if f >= 0 else RED)
                except (TypeError, ValueError):
                    return _mini(label, "—", T4)
            items = []
            for idx in growth.index:
                r = growth.loc[idx]
                minis = []
                if stock_col:
                    minis.append(_gmini("Stock", r.get(stock_col)))
                if index_col:
                    minis.append(_gmini("Index", r.get(index_col)))
                if minis:
                    items.append(_period_card(idx, minis))
            if items:
                blocks.append(_sub_block("Growth Estimates",
                    _tile_grid(items, min_w="130px")))
        except Exception:
            pass

    if not blocks:
        return html.Div()
    return _card("Analyst Revisions", blocks)


# ── SEC filings section ──────────────────────────────────────────────
# The full filing list is stashed in ``sec-filings-store`` and rendered by the
# filter callback below, so the year / type dropdowns can narrow it client-side
# without re-fetching. The card title and dropdowns live in the static layout.

# Dual-class US issuers where Yahoo attaches EDGAR filings to only one ticker.
# When the requested class returns nothing we retry the sibling(s) — same CIK,
# same filings.
_SEC_FILING_ALTS = {
    "GOOGL": ["GOOG"],   "GOOG": ["GOOGL"],
    "BRK-B": ["BRK-A"],  "BRK-A": ["BRK-B"],
    "BRK.B": ["BRK.A"],  "BRK.A": ["BRK.B"],
    "BF-B":  ["BF-A"],   "BF-A":  ["BF-B"],
    "LEN-B": ["LEN"],    "HEI-A": ["HEI"],
    "UHAL-B": ["UHAL"],
}


def _sec_filing_alt_tickers(symbol: str) -> list:
    """Sibling share-class tickers to try when a symbol has no SEC filings."""
    s = (symbol or "").upper()
    alts = list(_SEC_FILING_ALTS.get(s, []))
    # Generic ``ROOT-A`` / ``ROOT.B`` share-class swap (e.g. FOO-B → FOO-A/FOO).
    for sep in ("-", "."):
        if len(s) > 2 and s[-2] == sep and s[-1].isalpha():
            root = s[:-2]
            for cand in (root, f"{root}{sep}A", f"{root}{sep}B", f"{root}{sep}C"):
                if cand != s and cand not in alts:
                    alts.append(cand)
    return alts


def _fetch_sec_filings(symbol: str, primary):
    """``primary`` is the requested ticker's ``sec_filings``; fall back to a
    sibling share class if it carries none."""
    if isinstance(primary, list) and primary:
        return primary
    import yfinance as yf
    for alt in _sec_filing_alt_tickers(symbol):
        try:
            sf = yf.Ticker(alt).sec_filings
            if isinstance(sf, list) and sf:
                return sf
        except Exception:
            continue
    return primary


# ── ASX announcements (unofficial) ────────────────────────────────────
# yfinance's ``sec_filings`` is US EDGAR only. ASX-listed names (``.AX``) file
# continuous-disclosure announcements with the ASX instead. This pulls them from
# the backend that powers ASX's own company pages (MarkitDigital). It is an
# UNDOCUMENTED endpoint with no SLA — intended for internal research only. Keep
# a short in-process cache so a repeat view of the same security is a no-op.
_ASX_ANN_URL = ("https://asx.api.markitdigital.com/asx-research/1.0/"
                "companies/{code}/announcements")
# Public token embedded in ASX's own company pages, used to resolve the PDF.
_ASX_DOC_TOKEN = "83ff96335c2d45a094df02a206a39ff4"
_ASX_DOC_URL = ("https://cdn-api.markitdigital.com/apiman-gateway/ASX/"
                "asx-research/1.0/file/{key}?access_token=" + _ASX_DOC_TOKEN)
_ASX_CACHE = {}          # code -> (fetched_at_epoch, rows)
_ASX_CACHE_TTL = 900     # seconds


def _asx_announcements(code: str, limit: int = 200) -> list:
    """ASX company announcements, shaped like yfinance filing dicts so the
    existing normaliser handles them uniformly. Returns [] on any failure."""
    import time
    code = (code or "").upper()
    if not code:
        return []
    hit = _ASX_CACHE.get(code)
    if hit and (time.time() - hit[0]) < _ASX_CACHE_TTL:
        return hit[1]

    import requests
    from datetime import datetime
    rows = []
    try:
        r = requests.get(_ASX_ANN_URL.format(code=code),
                         params={"pageSize": limit},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        items = (((r.json() or {}).get("data") or {}).get("items") or []
                 if r.status_code == 200 else [])
    except Exception:
        items = []

    for it in items:
        raw = it.get("date")
        try:
            dt = (datetime.fromisoformat(raw.replace("Z", "+00:00"))
                  if isinstance(raw, str) else None)
        except Exception:
            dt = None
        key = it.get("documentKey")
        headline = it.get("headline") or ""
        rows.append({
            "date": dt or (raw[:10] if isinstance(raw, str) else raw),
            "type": (it.get("announcementType") or "").title(),
            "title": (("★ " if it.get("isPriceSensitive") else "") + headline),
            "edgarUrl": _ASX_DOC_URL.format(key=key) if key else "",
            "_source": "asx",
        })

    if rows:                              # only cache good pulls
        _ASX_CACHE[code] = (time.time(), rows)
    return rows


def _sec_filings_payload(yf_data: dict) -> list:
    """Normalise ``sec_filings`` into JSON-serialisable rows for the store.

    Returns every filing yfinance supplies (no cap), newest first.
    """
    filings = yf_data.get("sec_filings")
    if not filings or not isinstance(filings, list):
        return []
    out = []
    for f in filings:
        date = f.get("date")
        try:
            ds = (date.strftime("%Y-%m-%d")
                  if hasattr(date, "strftime") else str(date)[:10])
        except Exception:
            ds = str(date)[:10] if date else ""
        try:
            disp = (date.strftime("%b %d, %Y")
                    if hasattr(date, "strftime") else ds)
        except Exception:
            disp = ds
        year = int(ds[:4]) if ds[:4].isdigit() else None
        out.append({
            "date": ds, "date_disp": disp or "—", "year": year,
            "type": (f.get("type") or "").strip(),
            "title": f.get("title") or "",
            "url": f.get("edgarUrl") or "",
            "source": f.get("_source") or "sec",
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def _sec_filings_table(filings: list) -> html.Div:
    """Render the filings table from normalised store rows."""
    rows = []
    for f in filings:
        ftype = f.get("type", "")
        type_color = (ACCENT if ("10-K" in ftype or "10-Q" in ftype)
                      else "#60a5fa" if "8-K" in ftype else T2)
        title, url = f.get("title", ""), f.get("url", "")
        filing_cell = (html.A(title, href=url, target="_blank",
                              style=dict(color=T2, textDecoration="none"))
                       if url else (title, T3))
        rows.append([f.get("date_disp", "—"), (ftype, type_color), filing_cell])
    if not rows:
        return html.Div("No filings match the selected filters.",
            style=dict(color=T4, fontSize="12.5px", padding="16px 0"))
    cols = [("Date", "0 0 90px", "left"), ("Type", "0 0 60px", "left"),
            ("Filing", "1", "left")]
    return _table(cols, rows, max_height="360px")


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
    Output("sec-analyst-card",      "children"),
    Output("sec-analyst-card",      "style"),
    Output("sec-institutional-card","children"),
    Output("sec-institutional-card","style"),
    Output("sec-earnings-card",     "children"),
    Output("sec-earnings-card",     "style"),
    Output("sec-earnings-history-card","children"),
    Output("sec-earnings-history-card","style"),
    Output("sec-insider-card",      "children"),
    Output("sec-insider-card",      "style"),
    Output("sec-revisions-card",    "children"),
    Output("sec-revisions-card",    "style"),
    Output("sec-filings-store",     "data"),
    Output("sec-sec-filings-card",  "style"),
    Output("sec-loading-overlay",   "style", allow_duplicate=True),
    Input("sec-detail-store", "data"),
    prevent_initial_call=True,
)
def populate_security_detail(symbol):
    _HIDE = dict(display="none")
    _CARD_SHOW = dict(background=BG_CARD, border=f"1px solid {BORDER}",
                      borderRadius="14px", padding="20px", display="block")
    if not symbol:
        return (no_update,) * 22 + (_HIDE,)

    # Run yfinance in parallel with the IBKR wait.
    yf_result = {}
    def _do_yf():
        yf_result.update(_fetch_yf_financials(symbol))
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            yf_result["recommendations"] = t.recommendations
            yf_result["institutional_holders"] = t.institutional_holders
            yf_result["major_holders"] = t.major_holders
            yf_result["mutualfund_holders"] = t.mutualfund_holders
            yf_result["insider_purchases"] = t.insider_purchases
            yf_result["insider_transactions"] = t.insider_transactions
            yf_result["analyst_price_targets"] = t.analyst_price_targets
            yf_result["earnings_history"] = t.earnings_history
            yf_result["earnings_dates"] = t.earnings_dates
            yf_result["earnings_estimate"] = t.earnings_estimate
            yf_result["revenue_estimate"] = t.revenue_estimate
            yf_result["eps_revisions"] = t.eps_revisions
            yf_result["eps_trend"] = t.eps_trend
            yf_result["growth_estimates"] = t.growth_estimates
            yf_result["calendar"] = t.calendar
            if symbol.upper().endswith(".AX"):
                yf_result["sec_filings"] = _asx_announcements(symbol.split(".")[0])
            else:
                yf_result["sec_filings"] = _fetch_sec_filings(symbol, t.sec_filings)
        except Exception:
            pass
    yf_thread = threading.Thread(target=_do_yf, daemon=True)
    yf_thread.start()

    d = _load_detail(symbol)
    if not d:
        yf_thread.join(timeout=1)
        return (no_update,) * 22 + (_HIDE,)

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

    # Build all new sections
    analyst_children = _build_analyst_ratings(yf_result)
    has_analyst = bool(analyst_children.children) if hasattr(analyst_children, 'children') else False
    analyst_style = _CARD_SHOW if has_analyst else _HIDE

    inst_children = _build_institutional_holders(yf_result)
    has_inst = bool(inst_children.children) if hasattr(inst_children, 'children') else False
    inst_style = _CARD_SHOW if has_inst else _HIDE

    earnings_children = _build_earnings_estimates(yf_result)
    has_earnings = bool(earnings_children.children) if hasattr(earnings_children, 'children') else False
    earnings_style = _CARD_SHOW if has_earnings else _HIDE

    earnings_hist_children = _build_earnings_history(
        yf_result, price_bars=(d.get("daily_5y") or d.get("daily_1y")))
    has_earnings_hist = bool(earnings_hist_children.children) if hasattr(earnings_hist_children, 'children') else False
    earnings_hist_style = _CARD_SHOW if has_earnings_hist else _HIDE

    insider_children = _build_insider_activity(yf_result)
    has_insider = bool(insider_children.children) if hasattr(insider_children, 'children') else False
    insider_style = _CARD_SHOW if has_insider else _HIDE

    revisions_children = _build_analyst_revisions(yf_result)
    has_revisions = bool(revisions_children.children) if hasattr(revisions_children, 'children') else False
    revisions_style = _CARD_SHOW if has_revisions else _HIDE

    sec_filings_data = _sec_filings_payload(yf_result)
    sec_filings_style = _CARD_SHOW if sec_filings_data else _HIDE

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
            fin_metrics, _CARD_SHOW, news_card,
            analyst_children, analyst_style,
            inst_children, inst_style,
            earnings_children, earnings_style,
            earnings_hist_children, earnings_hist_style,
            insider_children, insider_style,
            revisions_children, revisions_style,
            sec_filings_data, sec_filings_style,
            _HIDE)


# ── SEC filings filters ───────────────────────────────────────────────
# When a security loads, populate the year / type dropdowns from the stored
# filings and default every option to selected. The table callback then reads
# the current selection and re-renders — no re-fetch needed.

@app.callback(
    Output("sec-filings-title", "children"),
    Input("sec-filings-store", "data"),
)
def _sec_filings_title(data):
    """The card carries US EDGAR filings or ASX announcements depending on the
    security — label it for whichever source is loaded, generic otherwise."""
    if data:
        if any(f.get("source") == "asx" for f in data):
            return "ASX Announcements"
        return "SEC Filings"
    return "Filings & Announcements"


@app.callback(
    Output("sec-filings-year", "options"),
    Output("sec-filings-year", "value"),
    Output("sec-filings-type", "options"),
    Output("sec-filings-type", "value"),
    Input("sec-filings-store", "data"),
)
def _sec_filings_filter_options(data):
    if not data:
        return [], [], [], []
    years = sorted({f["year"] for f in data if f.get("year")}, reverse=True)
    types = sorted({f["type"] for f in data if f.get("type")})
    year_opts = [{"label": str(y), "value": y} for y in years]
    type_opts = [{"label": t, "value": t} for t in types]
    return year_opts, years, type_opts, types  # default: everything selected


def _filter_summary(value, options, noun):
    """Trigger-button text summarising the current checklist selection."""
    total = len(options or [])
    chosen = value or []
    if total == 0 or len(chosen) in (0, total):
        return f"All {noun}"
    if len(chosen) <= 2:
        picked = set(chosen)
        return ", ".join(str(o["label"]) for o in options if o["value"] in picked)
    return f"{len(chosen)} of {total} {noun}"


@app.callback(
    Output("sec-filings-year-summary", "children"),
    Output("sec-filings-type-summary", "children"),
    Input("sec-filings-year", "value"),
    Input("sec-filings-type", "value"),
    State("sec-filings-year", "options"),
    State("sec-filings-type", "options"),
)
def _sec_filings_summaries(year_val, type_val, year_opts, type_opts):
    return (_filter_summary(year_val, year_opts, "years"),
            _filter_summary(type_val, type_opts, "types"))


@app.callback(
    Output("sec-filings-table", "children"),
    Input("sec-filings-year", "value"),
    Input("sec-filings-type", "value"),
    Input("sec-filings-store", "data"),
)
def _render_sec_filings(years, types, data):
    if not data:
        return html.Div()
    # An empty selection is treated as "no filter on this field" so clearing a
    # dropdown widens the view rather than blanking the table.
    ysel, tsel = set(years or []), set(types or [])
    filtered = [f for f in data
                if (not ysel or f.get("year") in ysel)
                and (not tsel or f.get("type") in tsel)]
    return _sec_filings_table(filtered)


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
