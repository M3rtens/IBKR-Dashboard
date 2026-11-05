"""Capital Gains Tax page: financial-year presets, the realised gains/losses
report and an XLSX export.

Figures come from the FIFO lot-matching engine in :mod:`services.cgt`, which
prices every disposed parcel the ATO way — proceeds converted at the
disposal-date FX rate, each parcel's cost base at its own acquisition-date
rate, commissions included — and flags parcels held long enough for the 50%
CGT discount.
"""

import datetime as _dt
import os

import pandas as pd
from dash import dcc, html, Input, Output, State, no_update, ctx

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4, T5
from dashboard.formatters import money, price_money


def _au_fy(today: _dt.date) -> tuple:
    """(start, end) of the Australian financial year containing ``today``."""
    y = today.year if today.month >= 7 else today.year - 1
    return _dt.date(y, 7, 1), _dt.date(y + 1, 6, 30)


def _fy_label(start: _dt.date) -> str:
    return f"FY {str(start.year)[-2:]}/{str(start.year + 1)[-2:]}"


def cgt_page():
    today = _dt.date.today()
    fy_start, _ = _au_fy(today)
    prev_start = fy_start.replace(year=fy_start.year - 1)
    picker_style = dict(
        background="#1e2330", border="1px solid rgba(255,255,255,0.10)",
        borderRadius="9px", padding="0 10px", fontSize="13px")
    label_style = dict(fontSize="12px", color=T4, fontWeight="600",
                       marginBottom="6px", letterSpacing="0.3px")
    fy_btn = dict(border="1px solid rgba(255,255,255,0.12)",
                  background="rgba(255,255,255,0.04)", color=T3,
                  fontWeight="600", fontSize="12px", padding="9px 14px",
                  borderRadius="9px", cursor="pointer", fontFamily="inherit",
                  alignSelf="flex-end")
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Start Date", style=label_style),
                html.Div(dcc.DatePickerSingle(
                    id="cgt-start", date=fy_start.isoformat(),
                    display_format="DD/MM/YYYY", className="cgt-date-picker"),
                    style=picker_style),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("End Date", style=label_style),
                html.Div(dcc.DatePickerSingle(
                    id="cgt-end", date=today.isoformat(),
                    display_format="DD/MM/YYYY", className="cgt-date-picker"),
                    style=picker_style),
            ], style=dict(flex="1")),
            html.Button(_fy_label(fy_start), id="cgt-fy-cur", n_clicks=0,
                        style=fy_btn, title="This financial year (1 Jul – 30 Jun)"),
            html.Button(_fy_label(prev_start), id="cgt-fy-prev", n_clicks=0,
                        style=fy_btn, title="Previous financial year"),
            html.Button("Generate Report", id="cgt-generate", n_clicks=0,
                style=dict(border="none",background=ACCENT,color="#06150f",
                    fontWeight="600",fontSize="13px",padding="10px 20px",
                    borderRadius="9px",cursor="pointer",fontFamily="inherit",
                    alignSelf="flex-end")),
            html.Button("Export XLSX", id="cgt-export", n_clicks=0,
                style=dict(border="1px solid rgba(255,255,255,0.12)",
                    background="transparent",color=T2,
                    fontWeight="600",fontSize="13px",padding="10px 20px",
                    borderRadius="9px",cursor="pointer",fontFamily="inherit",
                    alignSelf="flex-end")),
        ], style=dict(display="flex",gap="16px",alignItems="flex-end",
                      padding="20px 28px",background=BG_CARD,
                      borderRadius="14px",border=f"1px solid {BORDER}")),
        html.Div(id="cgt-summary"),
        html.Div(id="cgt-table-wrap",
            style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                       borderRadius="14px",overflow="hidden")),
        html.Div(id="cgt-income"),
        dcc.Download(id="cgt-download"),
    ], style=dict(padding="24px 28px 40px",display="flex",
                  flexDirection="column",gap="16px"))


# --- Callbacks -------------------------------------------------------

@app.callback(
    Output("cgt-start","date"),
    Output("cgt-end","date"),
    Input("cgt-fy-cur","n_clicks"),
    Input("cgt-fy-prev","n_clicks"),
    prevent_initial_call=True,
)
def set_fy_range(_cur, _prev):
    today = _dt.date.today()
    fy_start, fy_end = _au_fy(today)
    if ctx.triggered_id == "cgt-fy-prev":
        prev_end = fy_start - _dt.timedelta(days=1)
        return fy_start.replace(year=fy_start.year - 1).isoformat(), prev_end.isoformat()
    return fy_start.isoformat(), min(today, fy_end).isoformat()


@app.callback(
    Output("cgt-summary","children"),
    Output("cgt-table-wrap","children"),
    Output("cgt-income","children"),
    Output("cgt-data-store","data"),
    Input("cgt-generate","n_clicks"),
    State("cgt-start","date"),
    State("cgt-end","date"),
    prevent_initial_call=True,
)
def generate_cgt_report(n, start_date, end_date):
    if not start_date or not end_date:
        return html.Div("Please select a date range.", style=dict(color=RED)), html.Div(), html.Div(), None
    from services.flex_query import load_flex_data
    from services import cgt
    flex_token = os.environ.get("IBKR_FLEX_TOKEN", "")
    flex_qid = os.environ.get("IBKR_FLEX_QUERY_ID", "")
    if not flex_token or not flex_qid:
        return html.Div("Flex Query credentials not configured.", style=dict(color=RED)), html.Div(), html.Div(), None
    try:
        fd = load_flex_data(flex_token, flex_qid)
    except Exception as e:
        return html.Div(f"Failed to load Flex data: {e}", style=dict(color=RED)), html.Div(), html.Div(), None
    if fd.trades.empty:
        return html.Div("No trade data found.", style=dict(color=T4)), html.Div(), html.Div(), None

    start_dt = _dt.date.fromisoformat(start_date)
    end_dt = _dt.date.fromisoformat(end_date)
    res = cgt.match_disposals(fd.trades, start_dt, end_dt)
    if res.parcels.empty:
        return (html.Div("No sell transactions in the selected period.",
                    style=dict(color=T4,fontSize="13px",padding="20px 28px")),
                html.Div(), html.Div(), None)

    p = res.parcels.sort_values(["sell_date", "symbol"],
                                ascending=[False, True]).reset_index(drop=True)
    s = cgt.summarise(p)
    eligible_raw = float(p.loc[p["discount_eligible"], "gain_aud"].sum())

    def _sum_cell(label, val, color=T1, display=None, sub=None):
        if display is None:
            display = money(val, signed=True)
        kids = [
            html.Div(label, style=dict(fontSize="12px",color=T4,fontWeight="500")),
            html.Div(display,
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="18px",
                    fontWeight="600",color=color,marginTop="4px")),
        ]
        if sub:
            kids.append(html.Div(sub, style=dict(fontSize="10px",color=T5,
                                                 marginTop="3px")))
        return html.Div(kids)

    est = s["est_taxable"]
    summary_kids = [
        _sum_cell("Realised Gains", s["gains"], ACCENT),
        _sum_cell("Realised Losses", s["losses"], RED),
        _sum_cell("Net Capital Gain", s["net"],
                  ACCENT if s["net"] >= 0 else RED),
        _sum_cell("Discount-Eligible", eligible_raw,
                  sub="gains held > 12 months"),
        _sum_cell("Est. Taxable Gain", est,
                  ACCENT if est >= 0 else RED,
                  sub="after 50% discount" if eligible_raw else "losses offset gains"),
        _sum_cell("Disposals", 0, display=f"{s['disposals']:,}",
                  sub="parcels sold"),
    ]
    if s["unknown_cost"]:
        summary_kids.append(_sum_cell(
            "Estimated Cost", 0, color=RED, display=f"{s['unknown_cost']:,}",
            sub="predate report window"))
    summary = html.Div(summary_kids,
        style=dict(display="grid",gridTemplateColumns="repeat(6,1fr)",gap="14px",
                   padding="0 28px"))

    # -- disposal table (one row per parcel) ------------------------------
    col_style = dict(fontWeight="600",color=T4,
        textTransform="uppercase",letterSpacing="0.4px",padding="10px 12px",
        borderBottom=f"1px solid {BORDER}",textAlign="right",
        fontFamily="'JetBrains Mono',monospace")
    col_left = {**col_style, "textAlign":"left"}
    hdr = dict(fontSize="10.5px")
    grid = ("82px 62px 56px 82px 44px 76px 76px"
            " 96px 96px 100px 36px")
    header = html.Div([
        html.Div("Sold", style=dict(**col_left,**hdr)),
        html.Div("Symbol", style=dict(**col_left,**hdr)),
        html.Div("Qty", style=dict(**col_style,**hdr)),
        html.Div("Bought", style=dict(**col_left,**hdr)),
        html.Div("Held", style=dict(**col_style,**hdr)),
        html.Div("Buy Px", style=dict(**col_style,**hdr)),
        html.Div("Sell Px", style=dict(**col_style,**hdr)),
        html.Div("Cost (AUD)", style=dict(**col_style,**hdr)),
        html.Div("Proceeds (AUD)", style=dict(**col_style,**hdr)),
        html.Div("Gain (AUD)", style=dict(**col_style,**hdr)),
        html.Div("Disc", style=dict(**col_style,**hdr)),
    ], style=dict(display="grid",gridTemplateColumns=grid,gap="0"))

    recs, data_rows = [], []
    for _, r in p.iterrows():
        bought = (r["buy_date"].strftime("%d/%m/%Y")
                  if pd.notna(r["buy_date"]) else "—")
        held = f"{int(r['hold_days'])}d" if r["hold_days"] is not None else "—"
        recs.append({
            "Sell Date": r["sell_date"].strftime("%d/%m/%Y"),
            "Symbol": r["symbol"],
            "Description": str(r["description"])[:40],
            "Currency": r["currency"],
            "Qty": round(float(r["qty"]), 4),
            "Buy Date": bought,
            "Buy Price": round(float(r["buy_price"]), 4),
            "Sell Price": round(float(r["sell_price"]), 4),
            "Buy FX": round(float(r["buy_fx"]), 4) if r["buy_fx"] else None,
            "Sell FX": round(float(r["sell_fx"]), 4),
            "Cost Base (Local)": r["cost_local"],
            "Proceeds (Local)": r["proceeds_local"],
            "Gain/Loss (Local)": r["gain_local"],
            "Cost Base (AUD)": r["cost_aud"],
            "Proceeds (AUD)": r["proceeds_aud"],
            "Gain/Loss (AUD)": r["gain_aud"],
            "Held (days)": r["hold_days"] if r["hold_days"] is not None else "",
            "Discount Eligible": "Yes" if r["discount_eligible"] else "",
            "Cost Basis": "" if r["cost_known"] else "ESTIMATED",
        })
        gl = r["gain_aud"]
        row = html.Div([
            html.Div(r["sell_date"].strftime("%d/%m/%Y"),
                style={**col_left,"fontSize":"12px","color":T3}),
            html.Div(r["symbol"], style={**col_left,"fontSize":"12px","fontWeight":"600"}),
            html.Div(f'{r["qty"]:.4f}'.rstrip("0").rstrip("."),
                style={**col_style,"fontSize":"12px"}),
            html.Div(bought, style={**col_left,"fontSize":"12px","color":T3}),
            html.Div(held, style={**col_style,"fontSize":"12px","color":T3}),
            html.Div(f'{r["buy_price"]:.2f}', style={**col_style,"fontSize":"12px","color":T3}),
            html.Div(f'{r["sell_price"]:.2f}', style={**col_style,"fontSize":"12px","color":T3}),
            html.Div(money(r["cost_aud"]),
                style={**col_style,"fontSize":"12px","color":T3}),
            html.Div(money(r["proceeds_aud"]),
                style={**col_style,"fontSize":"12px","color":T3}),
            html.Div(money(gl, signed=True),
                style={**col_style,"fontSize":"12px","fontWeight":"600",
                       "color":ACCENT if gl >= 0 else RED}),
            html.Div("✓" if r["discount_eligible"] else "",
                style={**col_style,"fontSize":"12px","color":ACCENT}),
        ], style=dict(display="grid",gridTemplateColumns=grid,gap="0",
            borderBottom="1px solid rgba(255,255,255,0.04)"))
        data_rows.append(row)

    if s["unknown_cost"]:
        data_rows.append(html.Div(
            "Rows dated “—” predate the Flex report window — cost basis "
            "estimated from IBKR FIFO at the disposal-date FX rate.",
            style=dict(fontSize="11px",color=T5,padding="10px 12px")))
    table = html.Div([header] + data_rows,
        style=dict(maxHeight="500px",overflowY="auto"))

    # -- ordinary income: dividends received in the window -----------------
    # Dividends are not CGT events — they are ordinary income — so they get
    # their own section. Gross dividend and its withholding-tax line are
    # netted per symbol+pay date but shown in separate columns: the gross is
    # the assessable amount and the withheld tax is the foreign income tax
    # offset claim.
    income_ui, income_recs = html.Div(), []
    divs = fd.dividends
    if not divs.empty:
        d = divs.copy()
        d["_date"] = d["datetime"].dt.date
        d = d[(d["_date"] >= start_dt) & (d["_date"] <= end_dt)]
        if not d.empty:
            if "base_amount" in d.columns:
                d["_aud"] = d["base_amount"].fillna(
                    d["amount"] * d["fx_rate"].where(d["fx_rate"] > 0, 1.0))
            else:
                d["_aud"] = d["amount"] * d["fx_rate"].where(d["fx_rate"] > 0, 1.0)
            is_wht = d["type"] == "Withholding Tax"
            d["_gross_aud"] = d["_aud"].where(~is_wht, 0.0)
            d["_wht_aud"]   = d["_aud"].where(is_wht, 0.0)
            d["_gross_loc"] = d["amount"].where(~is_wht, 0.0)
            d["_wht_loc"]   = d["amount"].where(is_wht, 0.0)
            grp = d.groupby(["symbol", "_date"], as_index=False).agg(
                gross_aud=("_gross_aud", "sum"), wht_aud=("_wht_aud", "sum"),
                gross_loc=("_gross_loc", "sum"), wht_loc=("_wht_loc", "sum"),
                desc=("description", "first"),
            ).sort_values("_date", ascending=False)

            tot_gross = float(grp["gross_aud"].sum())
            tot_wht   = float(grp["wht_aud"].sum())      # negative
            inc_summary = html.Div([
                _sum_cell("Gross Dividends", 0, ACCENT,
                          display=price_money(tot_gross)),
                _sum_cell("Foreign Tax Withheld", 0, RED,
                          display=price_money(tot_wht),
                          sub="foreign income tax offset"),
                _sum_cell("Net Received", 0,
                          display=price_money(tot_gross + tot_wht)),
                _sum_cell("Payments", 0, display=f"{len(grp):,}"),
            ], style=dict(display="grid",gridTemplateColumns="repeat(4,1fr)",
                          gap="14px",padding="16px 28px 4px"))

            inc_grid = "90px 70px 1fr 100px 110px 100px"
            inc_header = html.Div([
                html.Div("Paid", style=dict(**col_left,**hdr)),
                html.Div("Symbol", style=dict(**col_left,**hdr)),
                html.Div("Description", style=dict(**col_left,**hdr)),
                html.Div("Gross (AUD)", style=dict(**col_style,**hdr)),
                html.Div("Withheld (AUD)", style=dict(**col_style,**hdr)),
                html.Div("Net (AUD)", style=dict(**col_style,**hdr)),
            ], style=dict(display="grid",gridTemplateColumns=inc_grid,gap="0"))
            inc_rows = []
            for _, r in grp.iterrows():
                net = r["gross_aud"] + r["wht_aud"]
                income_recs.append({
                    "Pay Date": r["_date"].strftime("%d/%m/%Y"),
                    "Symbol": r["symbol"],
                    "Description": str(r["desc"])[:40],
                    "Gross (Local)": round(float(r["gross_loc"]), 2),
                    "Withheld (Local)": round(float(r["wht_loc"]), 2),
                    "Gross (AUD)": round(float(r["gross_aud"]), 2),
                    "Withheld (AUD)": round(float(r["wht_aud"]), 2),
                    "Net (AUD)": round(float(net), 2),
                })
                inc_rows.append(html.Div([
                    html.Div(r["_date"].strftime("%d/%m/%Y"),
                        style={**col_left,"fontSize":"12px","color":T3}),
                    html.Div(r["symbol"], style={**col_left,"fontSize":"12px","fontWeight":"600"}),
                    html.Div(str(r["desc"])[:40],
                        style={**col_left,"fontSize":"12px","color":T3,
                               "overflow":"hidden","textOverflow":"ellipsis",
                               "whiteSpace":"nowrap"}),
                    html.Div(price_money(r["gross_aud"]),
                        style={**col_style,"fontSize":"12px","color":T3}),
                    html.Div(price_money(r["wht_aud"]),
                        style={**col_style,"fontSize":"12px","color":RED}),
                    html.Div(price_money(net),
                        style={**col_style,"fontSize":"12px","fontWeight":"600"}),
                ], style=dict(display="grid",gridTemplateColumns=inc_grid,gap="0",
                    borderBottom="1px solid rgba(255,255,255,0.04)")))
            income_ui = html.Div([
                html.Div("Ordinary Income — Dividends", style=dict(
                    fontSize="14px",fontWeight="600",padding="16px 28px 0",
                    fontFamily="'Space Grotesk',sans-serif")),
                html.Div("Assessable income, not a CGT event — report separately from capital gains",
                    style=dict(fontSize="11px",color=T5,padding="4px 28px 0")),
                inc_summary,
                html.Div([inc_header] + inc_rows,
                    style=dict(maxHeight="300px",overflowY="auto",
                               marginTop="10px")),
            ], style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                          borderRadius="14px",overflow="hidden",
                          paddingBottom="10px"))

    return summary, table, income_ui, {"disposals": recs, "income": income_recs}


@app.callback(
    Output("cgt-download","data"),
    Input("cgt-export","n_clicks"),
    State("cgt-data-store","data"),
    prevent_initial_call=True,
)
def export_cgt_xlsx(n, data):
    if not data:
        return no_update
    import io
    from services import cgt as _cgt
    df = pd.DataFrame(data.get("disposals") or [])
    income = pd.DataFrame(data.get("income") or [])

    gl = df["Gain/Loss (AUD)"]
    eligible = df["Discount Eligible"] == "Yes"
    gains   = float(gl[gl > 0].sum())
    losses  = float(gl[gl < 0].sum())
    elig_raw = float(gl[(gl > 0) & eligible].sum())
    est = _cgt.summarise(pd.DataFrame({
        "gain_aud": gl,
        "discount_eligible": eligible,
        "cost_known": df["Cost Basis"] != "ESTIMATED",
    }))["est_taxable"]

    # Totals row at the foot of the disposals sheet.
    totals = {c: "" for c in df.columns}
    totals.update({
        "Symbol": "TOTAL",
        "Cost Base (AUD)": round(df["Cost Base (AUD)"].sum(), 2),
        "Proceeds (AUD)": round(df["Proceeds (AUD)"].sum(), 2),
        "Gain/Loss (AUD)": round(gl.sum(), 2),
    })
    disposals = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)

    dates = pd.to_datetime(df["Sell Date"], format="%d/%m/%Y", errors="coerce")
    period = (f"{dates.min().strftime('%d/%m/%Y')} – "
              f"{dates.max().strftime('%d/%m/%Y')}") if dates.notna().any() else "—"
    n_est = int((df["Cost Basis"] == "ESTIMATED").sum())
    summary = pd.DataFrame([
        ("Report period",          period),
        ("Account base currency",  "AUD"),
        ("Disposal parcels",       len(df)),
        ("Realised gains (AUD)",   round(gains, 2)),
        ("Realised losses (AUD)",  round(losses, 2)),
        ("Net capital gain (AUD)", round(gains + losses, 2)),
        ("Discount-eligible gains (AUD)", round(elig_raw, 2)),
        ("Est. taxable gain after 50% discount (AUD)", round(est, 2)),
        ("", ""),
        ("Method",                 "FIFO lot matching over IBKR Flex trades. "
                                   "Proceeds converted at the disposal-date FX "
                                   "rate; each parcel's cost base converted at "
                                   "its acquisition-date FX rate (ATO cost-base "
                                   "method). Commissions included in cost base "
                                   "and deducted from proceeds."),
        ("Cross-check",            "Local-currency parcel gains reconcile with "
                                   "IBKR fifoPnlRealized per disposal."),
        ("CGT discount",           "“Yes” marks parcels held > 12 months with a "
                                   "gain — eligible for the 50% discount for "
                                   "individuals/trusts. Losses are offset against "
                                   "non-discount gains first in the estimate."),
        ("Estimated cost bases",   (f"{n_est} parcel(s) predate the report "
                                    "window; cost basis estimated from IBKR FIFO "
                                    "at the disposal-date FX rate. Re-run with a "
                                    "Flex Query covering the full history.")
                                   if n_est else "None — all parcels matched to "
                                   "recorded acquisitions."),
    ], columns=["Item", "Value"])

    # Ordinary income block: dividends are assessable income, not CGT events.
    if not income.empty:
        inc_totals = {c: "" for c in income.columns}
        inc_totals.update({
            "Symbol": "TOTAL",
            "Gross (AUD)": round(income["Gross (AUD)"].sum(), 2),
            "Withheld (AUD)": round(income["Withheld (AUD)"].sum(), 2),
            "Net (AUD)": round(income["Gross (AUD)"].sum()
                               + income["Withheld (AUD)"].sum(), 2),
        })
        income_out = pd.concat([income, pd.DataFrame([inc_totals])],
                               ignore_index=True)
        gross_aud = float(income["Gross (AUD)"].sum())
        wht_aud   = float(income["Withheld (AUD)"].sum())
        summary = pd.concat([summary, pd.DataFrame([
            ("", ""),
            ("Ordinary income", ""),
            ("Gross dividends (AUD)",  round(gross_aud, 2)),
            ("Foreign tax withheld (AUD)", round(wht_aud, 2)),
            ("Net dividends received (AUD)", round(gross_aud + wht_aud, 2)),
            ("Dividend note",          "Dividends are ordinary income, not "
                                       "capital gains — declare as foreign "
                                       "source income; the withheld tax may be "
                                       "claimable as a foreign income tax offset."),
        ], columns=["Item", "Value"])], ignore_index=True)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="Summary")
        disposals.to_excel(writer, index=False, sheet_name="Disposals")
        if not income.empty:
            income_out.to_excel(writer, index=False, sheet_name="Income")
    fname = f"capital_gains_{dates.min().strftime('%Y%m%d')}_{dates.max().strftime('%Y%m%d')}.xlsx" \
            if dates.notna().any() else "capital_gains_report.xlsx"
    return dcc.send_bytes(output.getvalue(), filename=fname)
