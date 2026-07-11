"""DCF Model page — driver-based unlevered FCFF valuation.

The model values the enterprise by projecting unlevered free cash flow to the
firm (FCFF) off a small set of drivers, discounting at WACC, and adding a
Gordon-growth terminal value. Financing flows (dividends, buybacks, debt
issuance) are deliberately excluded — FCFF is measured before returns to
capital providers, and the capital structure is already priced in through WACC.

Flow:  select security → seed assumptions & drivers from yfinance historicals →
analyst overrides any driver → live recompute of the FCFF build, the EV→equity
bridge, and a WACC × terminal-growth sensitivity grid.
"""

from dash import dcc, html, Input, Output, State, no_update, ALL

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5
from dashboard.formatters import ccy_symbol


# ── yfinance fetch ───────────────────────────────────────────────────

def _fetch_yf_info(symbol: str) -> dict:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        return {
            "info":     t.info or {},
            "income":   t.income_stmt,
            "balance":  t.balance_sheet,
            "cashflow": t.cashflow,
        }
    except Exception:
        return {}


def _df_val(df, *names):
    """Most recent value from a yfinance statement for the first matching row."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return None
    for n in names:
        if n in df.index:
            row = df.loc[n]
            if len(row) > 0:
                try:
                    return float(row.iloc[0])
                except (TypeError, ValueError):
                    return None
    return None


def _df_val_at(df, col_idx, *names):
    """Value at a given column index (0 = most recent) for the first match."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return None
    for n in names:
        if n in df.index:
            row = df.loc[n]
            if len(row) > col_idx:
                try:
                    return float(row.iloc[col_idx])
                except (TypeError, ValueError):
                    return None
    return None


def _safe(v, default):
    """Coerce to a finite float, falling back to ``default`` for None/NaN."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default   # NaN is truthy, so guard it explicitly


def _year_labels(base_year, n):
    """Projection year captions, e.g. 2027 E … relative to the last actuals."""
    return [f"{base_year + i + 1} E" for i in range(n)]


# ── Formatters ───────────────────────────────────────────────────────

def _fmt_money(v, sym="$", dp=None):
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
    return f"{sign}{sym}{a:.{dp if dp is not None else 0}f}"


def _fmt_pct(v, dp=1, signed=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    sign = ("+" if f > 0 else ("-" if f < 0 else "")) if signed else ("-" if f < 0 else "")
    return f"{sign}{abs(f):.{dp}f}%"


def _fmt_price(v, sym="$"):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    return f"{'-' if f < 0 else ''}{sym}{abs(f):,.2f}"


# ── Shared style tokens & builders ───────────────────────────────────

MONO = "'JetBrains Mono', monospace"
SANS = "'Space Grotesk', sans-serif"

CARD_STYLE = dict(background=BG_CARD, border=f"1px solid {BORDER}",
                  borderRadius="14px", padding="20px")
FIELD_LABEL = dict(fontSize="10.5px", color=T4, fontWeight="500",
                   letterSpacing="0.3px")


def _card(children, **overrides):
    return html.Div(children, style={**CARD_STYLE, **overrides})


def _section_title(text, **overrides):
    style = dict(fontSize="13px", fontWeight="600", color=T2, marginBottom="14px")
    style.update(overrides)
    return html.Div(text, style=style)


def _toggle_group(*buttons):
    return html.Div(buttons, style=dict(
        display="flex", gap="2px", background="rgba(255,255,255,0.04)",
        borderRadius="7px", padding="2px"))


def _num_input(input_id, value=None, placeholder="", step=0.1, width="100%"):
    return dcc.Input(
        id=input_id, type="number", placeholder=placeholder, value=value,
        debounce=True, step=step,
        style=dict(
            background="rgba(255,255,255,0.04)",
            border="1px solid rgba(255,255,255,0.08)",
            borderRadius="5px", color=T1, fontFamily=MONO,
            fontSize="12.5px", padding="6px 8px", width=width,
            textAlign="right", outline="none"))


def _stat_tile(label, value, color=T1, sub=None):
    body = [
        html.Div(label, style=FIELD_LABEL),
        html.Div(value, style=dict(
            fontSize="18px", fontWeight="600", color=color,
            fontFamily=MONO, marginTop="4px")),
    ]
    if sub:
        body.append(html.Div(sub, style=dict(
            fontSize="10px", color=T5, marginTop="2px")))
    return html.Div(body, style=dict(flex="1", minWidth="120px"))


# ── Assumption + driver definitions ──────────────────────────────────

# (label, input_id, default, step, placeholder)
_ASSUMPTION_FIELDS = [
    ("WACC (%)",            "dcf-wacc",     9.0,  0.1, "9.0"),
    ("Terminal Growth (%)", "dcf-tgr",      2.5,  0.1, "2.5"),
    ("Tax Rate (%)",        "dcf-tax",      25.0, 0.5, "25.0"),
    ("Shares Out (M)",      "dcf-shares",   1000, 1,   "1000"),
    ("Net Debt (M)",        "dcf-net-debt", 0,    1,   "0"),
]

# (driver_id, label, seed_key)  — seed_key indexes the model store's seed block.
_DRIVERS = [
    ("rev-growth",  "Revenue growth",  "growth"),
    ("ebit-margin", "EBIT margin",     "margin"),
    ("da-pct",      "D&A (% rev)",     "da"),
    ("capex-pct",   "CapEx (% rev)",   "capex"),
    ("nwc-pct",     "ΔNWC (% rev)",    "nwc"),
]

_PERIODS = {"3Y": 3, "5Y": 5, "10Y": 10}

LABEL_W = "150px"
HIST_W = "70px"


# ── Layout ───────────────────────────────────────────────────────────

def dcf_page():
    period_store = dcc.Store(id="dcf-period-store", data="5Y")
    model_store = dcc.Store(id="dcf-model-store")   # seeds + base revenue + fx

    # ── Header ──
    header = html.Div([
        html.Div([
            html.Div(id="dcf-ticker-name", style=dict(
                fontFamily=SANS, fontSize="22px", fontWeight="600",
                letterSpacing="0.2px")),
            html.Div("DCF Model · Unlevered FCFF", style=dict(
                fontSize="13px", color=T4, marginTop="2px")),
        ], style=dict(flex="1")),
        html.Div(id="dcf-header-verdict", style=dict(
            display="flex", gap="22px", alignItems="center")),
        html.Button("Back", id="dcf-back-btn", n_clicks=0,
            className="sec-range-btn", style=dict(fontSize="12px")),
    ], style=dict(display="flex", alignItems="center", gap="24px",
                  marginBottom="4px"))

    # ── Assumptions + period ──
    def _field(label, input_id, value, step, placeholder):
        return html.Div([
            html.Div(label, style={**FIELD_LABEL, "marginBottom": "6px"}),
            _num_input(input_id, value=value, placeholder=placeholder, step=step),
        ], style=dict(flex="1", minWidth="110px"))

    period_toggle = html.Div([
        _section_title("Projection Period", marginBottom="0", marginRight="14px"),
        _toggle_group(*[
            html.Button(p, id={"type": "dcf-period-btn", "index": p}, n_clicks=0,
                className="sec-range-btn" + (" active" if p == "5Y" else ""))
            for p in _PERIODS
        ]),
    ], style=dict(display="flex", alignItems="center"))

    assumptions_card = _card([
        html.Div([
            _section_title("Assumptions", marginBottom="0"),
            period_toggle,
        ], style=dict(display="flex", justifyContent="space-between",
                      alignItems="center", marginBottom="14px")),
        html.Div([_field(*f) for f in _ASSUMPTION_FIELDS],
                 style=dict(display="flex", gap="14px", flexWrap="wrap")),
    ])

    # ── Drivers (grid built by callback) ──
    drivers_card = _card([
        _section_title("Projection Drivers"),
        html.Div(id="dcf-drivers"),
    ])

    # ── FCFF build (built by callback) ──
    projection_card = _card([
        _section_title("Free Cash Flow Build"),
        html.Div(id="dcf-projection-table"),
    ])

    # ── Valuation bridge (built by callback) ──
    valuation_card = _card([
        _section_title("Valuation"),
        html.Div(id="dcf-valuation"),
    ])

    # ── Sensitivity (built by callback) ──
    sensitivity_card = _card([
        _section_title("Sensitivity — Implied Share Price"),
        html.Div(id="dcf-sensitivity"),
    ])

    return html.Div([
        period_store, model_store,
        header,
        assumptions_card,
        drivers_card,
        projection_card,
        html.Div([valuation_card, sensitivity_card], style=dict(
            display="grid", gridTemplateColumns="minmax(0,1fr) minmax(0,1fr)",
            gap="18px")),
    ], style=dict(padding="24px 28px 40px", display="flex",
                  flexDirection="column", gap="18px",
                  height="100%", overflowY="auto"))


# ── Core valuation maths ─────────────────────────────────────────────

def _project(base_rev, drivers, tax, n_years):
    """Build the per-year FCFF projection.

    ``drivers`` is a dict of lists keyed by driver id, each list length
    ``n_years`` holding the driver value (in percent) for that year.
    Returns a list of per-year dicts plus discounting is applied by the caller.
    """
    rows = []
    rev = base_rev
    for i in range(n_years):
        g = (drivers["rev-growth"][i] or 0) / 100.0
        margin = (drivers["ebit-margin"][i] or 0) / 100.0
        da_p = (drivers["da-pct"][i] or 0) / 100.0
        capex_p = (drivers["capex-pct"][i] or 0) / 100.0
        nwc_p = (drivers["nwc-pct"][i] or 0) / 100.0

        rev = rev * (1 + g)
        ebit = rev * margin
        taxes = ebit * tax if ebit > 0 else 0.0
        nopat = ebit - taxes
        da = rev * da_p
        capex = rev * capex_p
        dnwc = rev * nwc_p
        fcff = nopat + da - capex - dnwc

        rows.append(dict(
            revenue=rev, growth=g * 100, ebit=ebit, margin=margin * 100,
            taxes=taxes, nopat=nopat, da=da, capex=capex, dnwc=dnwc, fcff=fcff))
    return rows


def _value(base_rev, drivers, wacc, tgr, tax, shares_m, net_debt_m, n_years):
    """Full valuation. Returns projection rows and the EV→equity bridge."""
    rows = _project(base_rev, drivers, tax, n_years)

    pv_fcff = []
    for i, r in enumerate(rows):
        df = 1.0 / (1 + wacc) ** (i + 1)
        r["df"] = df
        r["pv"] = r["fcff"] * df
        pv_fcff.append(r["pv"])

    sum_pv = sum(pv_fcff)

    # Terminal value — Gordon growth on the final-year FCFF.
    if rows and wacc > tgr:
        tv = rows[-1]["fcff"] * (1 + tgr) / (wacc - tgr)
    else:
        tv = 0.0
    pv_tv = tv * (1.0 / (1 + wacc) ** n_years) if rows else 0.0

    ev = sum_pv + pv_tv
    # Net debt entered in millions; scale to the statement's absolute units.
    equity = ev - net_debt_m * 1e6
    per_share = equity / (shares_m * 1e6) if shares_m else 0.0
    tv_share = (pv_tv / ev) if ev else 0.0

    return dict(rows=rows, sum_pv=sum_pv, tv=tv, pv_tv=pv_tv, ev=ev,
                equity=equity, per_share=per_share, tv_share=tv_share)


def _implied_price(base_rev, drivers, wacc, tgr, tax, shares_m, net_debt_m, n_years):
    """Lightweight per-share value for the sensitivity grid."""
    return _value(base_rev, drivers, wacc, tgr, tax, shares_m,
                  net_debt_m, n_years)["per_share"]


# ── Seed callback: fetch once, populate assumptions + driver seeds ────

@app.callback(
    Output("dcf-ticker-name", "children"),
    Output("dcf-model-store", "data"),
    Output("dcf-wacc", "value"),
    Output("dcf-tgr", "value"),
    Output("dcf-tax", "value"),
    Output("dcf-shares", "value"),
    Output("dcf-net-debt", "value"),
    Input("dcf-ticker-store", "data"),
    prevent_initial_call=True,
)
def _seed_model(symbol):
    if not symbol:
        return "", None, no_update, no_update, no_update, no_update, no_update

    yf = _fetch_yf_info(symbol)
    info = yf.get("info", {}) or {}
    income = yf.get("income")
    cashflow = yf.get("cashflow")

    name = info.get("longName") or info.get("shortName") or symbol
    ccy = ccy_symbol(info.get("currency") or info.get("financialCurrency"))

    base_rev = _safe(_df_val(income, "Total Revenue", "Operating Revenue"), 1e9)
    prev_rev = _safe(_df_val_at(income, 1, "Total Revenue", "Operating Revenue"), 0)
    ebit = _safe(_df_val(income, "EBIT", "Operating Income"), 0)
    pretax = _safe(_df_val(income, "Pretax Income", "Pretax Income Loss"), 0)
    tax_prov = _safe(_df_val(income, "Tax Provision", "Income Tax Expense"), 0)
    da = _safe(_df_val(cashflow, "Depreciation And Amortization",
               "Depreciation Amortization Depletion", "Reconciled Depreciation"), 0)
    capex = _safe(_df_val(cashflow, "Capital Expenditure", "Purchase Of PPE"), 0)

    # Base fiscal year from the most recent actuals column; projections run from
    # the following year. Fall back to the current calendar year.
    base_year = None
    try:
        base_year = int(str(income.columns[0])[:4])
    except Exception:
        pass
    if not base_year:
        import datetime
        base_year = datetime.datetime.now().year

    # Historical anchors → sensible default drivers (percent).
    growth = ((base_rev / prev_rev - 1) * 100) if prev_rev else 6.0
    growth = max(min(growth, 25.0), 0.0)                      # clamp runaway y/y
    margin = (ebit / base_rev * 100) if (ebit and base_rev) else 18.0
    da_pct = (abs(da) / base_rev * 100) if (da and base_rev) else 4.0
    capex_pct = (abs(capex) / base_rev * 100) if (capex and base_rev) else 5.0
    eff_tax = (tax_prov / pretax * 100) if (pretax and tax_prov and pretax > 0) else 25.0
    eff_tax = max(min(eff_tax, 40.0), 0.0)

    # Assumptions seeded from the info block where available.
    shares = info.get("sharesOutstanding")
    shares_m = round(shares / 1e6, 1) if shares else 1000
    total_debt = info.get("totalDebt") or 0
    total_cash = info.get("totalCash") or 0
    net_debt_m = round((total_debt - total_cash) / 1e6, 1)
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

    store = dict(
        base_rev=base_rev, ccy=ccy, price=price, name=name, base_year=base_year,
        seed=dict(growth=round(growth, 1), margin=round(margin, 1),
                  da=round(da_pct, 1), capex=round(capex_pct, 1), nwc=2.0),
    )
    #        name              store  wacc       tgr        tax
    return (f"{name} ({symbol})", store, no_update, no_update, round(eff_tax, 1),
            shares_m, net_debt_m)


# ── Drivers grid — rebuilt on period / seed change ───────────────────

@app.callback(
    Output("dcf-drivers", "children"),
    Input("dcf-period-store", "data"),
    Input("dcf-model-store", "data"),
    prevent_initial_call=True,
)
def _build_drivers(period, model):
    if not model:
        return html.Div("Select a security to build a DCF model.",
            style=dict(color=T4, fontSize="12.5px", padding="12px 0"))

    n = _PERIODS.get(period or "5Y", 5)
    seed = model.get("seed", {})
    ccy = model.get("ccy", "$")
    year_labels = _year_labels(model.get("base_year") or 2025, n)

    # Header row: label | Hist | Y1..Yn
    hdr = [
        html.Div(style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W)),
        html.Div("Hist.", style=dict(flex=f"0 0 {HIST_W}", minWidth=HIST_W,
            textAlign="right", fontSize="10.5px", color=T5, fontWeight="600")),
    ]
    for y in year_labels:
        hdr.append(html.Div(y, style=dict(flex="1", textAlign="right",
            fontSize="11px", color=T4, fontWeight="600")))
    rows = [html.Div(hdr, style=dict(display="flex", gap="12px", padding="4px 0",
        borderBottom="1px solid rgba(255,255,255,0.08)", marginBottom="4px"))]

    for driver_id, label, seed_key in _DRIVERS:
        hist_val = seed.get(seed_key)
        cells = [
            html.Div(label, style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W,
                fontSize="12.5px", color=T3, paddingLeft="4px")),
            html.Div(_fmt_pct(hist_val), style=dict(flex=f"0 0 {HIST_W}",
                minWidth=HIST_W, textAlign="right", fontSize="11.5px",
                color=T5, fontFamily=MONO, paddingTop="6px")),
        ]
        for i in range(n):
            cells.append(html.Div(
                _num_input({"type": "dcf-driver", "index": f"{driver_id}-{i}"},
                           value=hist_val, step=0.1),
                style=dict(flex="1")))
        rows.append(html.Div(cells, style=dict(display="flex", gap="12px",
            padding="4px 0", alignItems="center")))

    base = model.get("base_rev")
    return html.Div([
        html.Div(f"Base revenue (LTM): {ccy}{_fmt_money(base, '')}",
            style=dict(fontSize="11px", color=T4, marginBottom="10px")),
        html.Div(rows, style=dict(overflowX="auto")),
    ])


# ── Live compute: FCFF build + valuation + sensitivity ───────────────

def _gather_drivers(ids, values, n):
    """Turn pattern-matched driver inputs into {driver_id: [v0..v(n-1)]}."""
    out = {d[0]: [None] * n for d in _DRIVERS}
    for id_dict, v in zip(ids, values):
        base, _, idx = id_dict["index"].rpartition("-")
        try:
            i = int(idx)
        except ValueError:
            continue
        if base in out and 0 <= i < n:
            out[base][i] = v
    return out


def _num_row(label, values, ccy, kind="normal", pct=False, dp_price=False):
    """One right-aligned numeric row in a build table."""
    weight = "600" if kind in ("total", "accent") else "400"
    color = {"total": T1, "accent": ACCENT, "muted": T5}.get(kind, T2)
    label_color = T1 if kind in ("total", "accent") else (T5 if kind == "muted" else T3)
    cells = [html.Div(label, style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W,
        fontSize="12px" if kind != "muted" else "11px",
        fontWeight=weight, color=label_color, paddingLeft="4px"))]
    for v in values:
        if pct:
            txt = _fmt_pct(v)
        elif dp_price:
            txt = f"{v:.3f}" if isinstance(v, (int, float)) else "—"
        else:
            txt = _fmt_money(v, ccy)
        cells.append(html.Div(txt, style=dict(flex="1", textAlign="right",
            fontSize="12px" if kind != "muted" else "11px",
            fontWeight=weight, color=color, fontFamily=MONO)))
    return html.Div(cells, style=dict(display="flex", gap="12px", padding="4px 0",
        background="rgba(255,255,255,0.02)" if kind in ("total", "accent") else "transparent"))


@app.callback(
    Output("dcf-projection-table", "children"),
    Output("dcf-valuation", "children"),
    Output("dcf-sensitivity", "children"),
    Output("dcf-header-verdict", "children"),
    Input({"type": "dcf-driver", "index": ALL}, "value"),
    Input("dcf-wacc", "value"),
    Input("dcf-tgr", "value"),
    Input("dcf-tax", "value"),
    Input("dcf-shares", "value"),
    Input("dcf-net-debt", "value"),
    Input("dcf-period-store", "data"),
    State({"type": "dcf-driver", "index": ALL}, "id"),
    State("dcf-model-store", "data"),
    prevent_initial_call=True,
)
def _compute(driver_vals, wacc, tgr, tax, shares_m, net_debt_m,
             period, driver_ids, model):
    if not model or not driver_ids:
        return no_update, no_update, no_update, no_update

    n = _PERIODS.get(period or "5Y", 5)
    # Driver grid may briefly hold the previous period's count during rebuild.
    if len(driver_ids) != len(_DRIVERS) * n:
        return no_update, no_update, no_update, no_update

    base_rev = _safe(model.get("base_rev"), 1e9)
    ccy = model.get("ccy", "$")
    price = _safe(model.get("price"), 0)
    year_labels = _year_labels(model.get("base_year") or 2025, n)

    wacc = (wacc if wacc is not None else 9.0) / 100.0
    tgr = (tgr if tgr is not None else 2.5) / 100.0
    tax = (tax if tax is not None else 25.0) / 100.0
    shares_m = shares_m if shares_m else 1000
    net_debt_m = net_debt_m if net_debt_m is not None else 0

    drivers = _gather_drivers(driver_ids, driver_vals, n)
    val = _value(base_rev, drivers, wacc, tgr, tax, shares_m, net_debt_m, n)
    rows = val["rows"]

    # ── FCFF build table ──
    hdr_cells = [html.Div(style=dict(flex=f"0 0 {LABEL_W}", minWidth=LABEL_W))]
    for lbl in year_labels:
        hdr_cells.append(html.Div(lbl, style=dict(flex="1", textAlign="right",
            fontSize="11px", color=T4, fontWeight="600")))
    build = html.Div([
        html.Div(hdr_cells, style=dict(display="flex", gap="12px", padding="4px 0",
            borderBottom="1px solid rgba(255,255,255,0.08)", marginBottom="4px")),
        _num_row("Revenue",        [r["revenue"] for r in rows], ccy, "total"),
        _num_row("  growth %",     [r["growth"] for r in rows], ccy, "muted", pct=True),
        _num_row("EBIT",           [r["ebit"] for r in rows], ccy),
        _num_row("  margin %",     [r["margin"] for r in rows], ccy, "muted", pct=True),
        _num_row("Less: taxes",    [-r["taxes"] for r in rows], ccy),
        _num_row("NOPAT",          [r["nopat"] for r in rows], ccy, "total"),
        _num_row("Plus: D&A",      [r["da"] for r in rows], ccy),
        _num_row("Less: CapEx",    [-r["capex"] for r in rows], ccy),
        _num_row("Less: ΔNWC",     [-r["dnwc"] for r in rows], ccy),
        _num_row("Unlevered FCFF", [r["fcff"] for r in rows], ccy, "accent"),
        _num_row("Discount factor",[r["df"] for r in rows], ccy, "muted", dp_price=True),
        _num_row("PV of FCFF",     [r["pv"] for r in rows], ccy, "total"),
    ], style=dict(overflowX="auto"))

    # ── Valuation bridge ──
    upside = (val["per_share"] / price - 1) * 100 if price else None
    up_color = ACCENT if (upside or 0) >= 0 else RED
    tv_warn = val["tv_share"] > 0.75
    bridge = html.Div([
        html.Div([
            _stat_tile("Σ PV of FCFF",   f"{ccy}{_fmt_money(val['sum_pv'], '')}"),
            _stat_tile("PV of Terminal", f"{ccy}{_fmt_money(val['pv_tv'], '')}",
                       sub=f"{val['tv_share']*100:.0f}% of EV"),
            _stat_tile("Enterprise Value", f"{ccy}{_fmt_money(val['ev'], '')}", T1),
        ], style=dict(display="flex", gap="18px", marginBottom="16px")),
        html.Div([
            _stat_tile("Less: Net Debt", f"{ccy}{_fmt_money(net_debt_m*1e6, '')}"),
            _stat_tile("Equity Value",   f"{ccy}{_fmt_money(val['equity'], '')}", T1),
            _stat_tile("Implied / Share", _fmt_price(val["per_share"], ccy), ACCENT),
        ], style=dict(display="flex", gap="18px")),
        html.Div(
            f"⚠ Terminal value is {val['tv_share']*100:.0f}% of enterprise value — "
            "result is highly sensitive to terminal assumptions."
            if tv_warn else "",
            style=dict(fontSize="10.5px", color="#f6c453", marginTop="14px")),
    ])

    # ── Header verdict badge ──
    if price:
        verdict = [
            _stat_tile("Current", _fmt_price(price, ccy), T2),
            _stat_tile("Implied", _fmt_price(val["per_share"], ccy), ACCENT),
            _stat_tile("Upside", _fmt_pct(upside, signed=True), up_color),
        ]
    else:
        verdict = [_stat_tile("Implied", _fmt_price(val["per_share"], ccy), ACCENT)]

    # ── Sensitivity grid ──
    sens = _sensitivity(base_rev, drivers, wacc, tgr, tax, shares_m,
                        net_debt_m, n, price, ccy)

    return build, bridge, sens, verdict


# ── Sensitivity grid: implied price over WACC × terminal growth ──────

def _heat(t):
    """Red → amber → green background for a normalised value ``t`` in [0, 1]."""
    stops = [(255, 107, 107), (245, 196, 83), (54, 211, 153)]  # low → mid → high
    if t <= 0.5:
        (r0, g0, b0), (r1, g1, b1), f = stops[0], stops[1], t / 0.5
    else:
        (r0, g0, b0), (r1, g1, b1), f = stops[1], stops[2], (t - 0.5) / 0.5
    r = int(r0 + (r1 - r0) * f)
    g = int(g0 + (g1 - g0) * f)
    b = int(b0 + (b1 - b0) * f)
    return f"rgba({r},{g},{b},0.22)"


def _sensitivity(base_rev, drivers, wacc, tgr, tax, shares_m, net_debt_m,
                 n, price, ccy):
    wacc_steps = [wacc + d for d in (-0.010, -0.005, 0.0, 0.005, 0.010)]
    tgr_steps = [tgr + d for d in (-0.010, -0.005, 0.0, 0.005, 0.010)]

    # Precompute the whole grid so the colour gradient can span its own range.
    matrix = [[(_implied_price(base_rev, drivers, w, g, tax, shares_m,
                               net_debt_m, n) if w > g else float("nan"))
               for g in tgr_steps] for w in wacc_steps]
    finite = [v for row in matrix for v in row if v == v]
    vmin, vmax = (min(finite), max(finite)) if finite else (0.0, 1.0)
    span = (vmax - vmin) or 1.0

    def cell_bg(imp):
        if imp != imp:                       # NaN → uncoloured
            return "transparent"
        return _heat((imp - vmin) / span)    # low = red, high = green

    # Corner + terminal-growth column headers.
    hdr = [html.Div("WACC \\ g", style=dict(flex=f"0 0 {HIST_W}", minWidth=HIST_W,
        fontSize="10px", color=T5, fontWeight="600", paddingLeft="4px"))]
    for g in tgr_steps:
        hdr.append(html.Div(f"{g*100:.1f}%", style=dict(flex="1", textAlign="center",
            fontSize="11px", color=T4, fontWeight="600")))
    grid = [html.Div(hdr, style=dict(display="flex", gap="6px", padding="4px 0",
        borderBottom="1px solid rgba(255,255,255,0.08)", marginBottom="4px"))]

    for wi, w in enumerate(wacc_steps):
        cells = [html.Div(f"{w*100:.1f}%", style=dict(flex=f"0 0 {HIST_W}",
            minWidth=HIST_W, fontSize="11px", color=T4, fontWeight="600",
            paddingLeft="4px", display="flex", alignItems="center"))]
        for gi, g in enumerate(tgr_steps):
            imp = matrix[wi][gi]
            is_base = abs(w - wacc) < 1e-9 and abs(g - tgr) < 1e-9
            cells.append(html.Div(_fmt_price(imp, ccy), style=dict(
                flex="1", textAlign="center", fontSize="11.5px",
                fontFamily=MONO, padding="7px 4px", borderRadius="4px",
                color=T1, fontWeight="700" if is_base else "400",
                border=f"1px solid {ACCENT}" if is_base else "1px solid transparent",
                background=cell_bg(imp))))
        grid.append(html.Div(cells, style=dict(display="flex", gap="6px",
            padding="2px 0")))

    note = "Rows = WACC · Columns = terminal growth · centre = base case"
    if price:
        note += "  ·  green = higher implied value, red = lower"
    return html.Div([
        html.Div(note, style=dict(fontSize="10.5px", color=T5, marginBottom="10px")),
        html.Div(grid, style=dict(overflowX="auto")),
    ])


# ── Period toggle ────────────────────────────────────────────────────

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
    return [f"{b} active" if p == active else b for p in _PERIODS]


# ── Back button ──────────────────────────────────────────────────────

@app.callback(
    Output("sec-detail-store", "data", allow_duplicate=True),
    Output("dcf-ticker-store", "data", allow_duplicate=True),
    Input("dcf-back-btn", "n_clicks"),
    State("dcf-ticker-store", "data"),
    prevent_initial_call=True,
)
def _dcf_back(_n, symbol):
    # Return to the security view and clear the DCF ticker, so re-opening the
    # DCF page for the same symbol registers as a change and re-navigates.
    return symbol, None
