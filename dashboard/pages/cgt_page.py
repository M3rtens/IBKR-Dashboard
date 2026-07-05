"""Capital Gains Tax page: financial-year date pickers, the realised
gains/losses report and an XLSX export.
"""

import os

import pandas as pd
from dash import dcc, html, Input, Output, State, no_update

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, RED, T1, T2, T3, T4
from dashboard.formatters import money


def cgt_page():
    import datetime as _dt
    today = _dt.date.today()
    # Default to the trailing 12 months: end = today, start = one year earlier.
    fy_end = today
    try:
        fy_start = today.replace(year=today.year - 1)
    except ValueError:  # 29 Feb → 28 Feb in a non-leap prior year
        fy_start = today.replace(year=today.year - 1, day=28)
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Start Date", style=dict(fontSize="12px",color=T4,
                    fontWeight="600",marginBottom="6px",letterSpacing="0.3px")),
                html.Div(
                    dcc.DatePickerSingle(
                        id="cgt-start",
                        date=fy_start.isoformat(),
                        display_format="DD/MM/YYYY",
                        className="cgt-date-picker",
                    ),
                    style=dict(
                        background="#1e2330",
                        border="1px solid rgba(255,255,255,0.10)",
                        borderRadius="9px",
                        padding="0 10px",
                        fontSize="13px",
                    ),
                ),
            ], style=dict(flex="1")),
            html.Div([
                html.Div("End Date", style=dict(fontSize="12px",color=T4,
                    fontWeight="600",marginBottom="6px",letterSpacing="0.3px")),
                html.Div(
                    dcc.DatePickerSingle(
                        id="cgt-end",
                        date=fy_end.isoformat(),
                        display_format="DD/MM/YYYY",
                        className="cgt-date-picker",
                    ),
                    style=dict(
                        background="#1e2330",
                        border="1px solid rgba(255,255,255,0.10)",
                        borderRadius="9px",
                        padding="0 10px",
                        fontSize="13px",
                    ),
                ),
            ], style=dict(flex="1")),
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
        dcc.Download(id="cgt-download"),
    ], style=dict(padding="24px 28px 40px",display="flex",
                  flexDirection="column",gap="16px"))


# --- Callbacks -------------------------------------------------------

@app.callback(
    Output("cgt-summary","children"),
    Output("cgt-table-wrap","children"),
    Output("cgt-data-store","data"),
    Input("cgt-generate","n_clicks"),
    State("cgt-start","date"),
    State("cgt-end","date"),
    prevent_initial_call=True,
)
def generate_cgt_report(n, start_date, end_date):
    if not start_date or not end_date:
        return html.Div("Please select a date range.", style=dict(color=RED)), html.Div(), None
    from services.flex_query import load_flex_data
    import datetime as _dt
    flex_token = os.environ.get("IBKR_FLEX_TOKEN", "")
    flex_qid = os.environ.get("IBKR_FLEX_QUERY_ID", "")
    if not flex_token or not flex_qid:
        return html.Div("Flex Query credentials not configured.", style=dict(color=RED)), html.Div(), None
    try:
        fd = load_flex_data(flex_token, flex_qid)
    except Exception as e:
        return html.Div(f"Failed to load Flex data: {e}", style=dict(color=RED)), html.Div(), None
    trades = fd.trades
    if trades.empty:
        return html.Div("No trade data found.", style=dict(color=T4)), html.Div(), None
    start_dt = _dt.date.fromisoformat(start_date)
    end_dt = _dt.date.fromisoformat(end_date)
    sells = trades[
        (trades["action"] == "SELL") &
        (trades["asset_class"] != "CASH") &
        (trades["date"].dt.date >= start_dt) &
        (trades["date"].dt.date <= end_dt)
    ].copy()
    if sells.empty:
        return (html.Div("No sell transactions in the selected period.",
                    style=dict(color=T4,fontSize="13px",padding="20px 28px")),
                html.Div(), None)
    sells = sells.sort_values("date")
    rows = []
    for _, t in sells.iterrows():
        qty = abs(t["quantity"])
        sell_price = t["price"]
        cost_basis = abs(t["cost"])
        proceeds = t["proceeds"]
        commission = abs(t["commission"])
        gain_usd = t["realized_pnl"]
        fx = t["fx_rate"] if t["fx_rate"] else 1.0
        gain_aud = gain_usd * fx
        rows.append({
            "Date": t["date"].strftime("%d/%m/%Y") if hasattr(t["date"], "strftime") else str(t["date"])[:10],
            "Symbol": t["symbol"],
            "Description": str(t["description"])[:40],
            "Qty": qty,
            "Sell Price": round(sell_price, 4),
            "Cost Basis": round(cost_basis, 4),
            "Proceeds": round(proceeds, 4),
            "Commission": round(commission, 4),
            "Gain/Loss (USD)": round(gain_usd, 2),
            "FX Rate": round(fx, 4),
            "Gain/Loss (AUD)": round(gain_aud, 2),
        })
    df = pd.DataFrame(rows)
    total_gain = df["Gain/Loss (AUD)"].sum()
    total_gains = df.loc[df["Gain/Loss (AUD)"] > 0, "Gain/Loss (AUD)"].sum()
    total_losses = df.loc[df["Gain/Loss (AUD)"] < 0, "Gain/Loss (AUD)"].sum()
    # Commission is stored per-row in USD; convert with each row's FX rate so the
    # total is in AUD, matching the gain/loss tiles beside it.
    total_commission = float((df["Commission"] * df["FX Rate"]).sum())
    def _sum_cell(label, val, color=T1, display=None):
        # ``display`` overrides the default AUD-money formatting (used for the
        # transaction count and the plain commission total).
        if display is None:
            display = money(val, signed=True)
        return html.Div([
            html.Div(label, style=dict(fontSize="12px",color=T4,fontWeight="500")),
            html.Div(display,
                style=dict(fontFamily="'JetBrains Mono',monospace",fontSize="18px",
                    fontWeight="600",color=color,marginTop="4px")),
        ])
    summary = html.Div([
        _sum_cell("Realised Gains", total_gains, ACCENT),
        _sum_cell("Realised Losses", total_losses, RED),
        _sum_cell("Net Capital Gain", total_gain,
                  ACCENT if total_gain >= 0 else RED),
        _sum_cell("Commission", total_commission,
                  display=f"${total_commission:,.2f}"),
        _sum_cell("Transactions", len(df), display=f"{len(df):,}"),
    ], style=dict(display="grid",gridTemplateColumns="repeat(5,1fr)",gap="14px",
                  padding="0 28px"))
    col_style = dict(fontWeight="600",color=T4,
        textTransform="uppercase",letterSpacing="0.4px",padding="10px 14px",
        borderBottom=f"1px solid {BORDER}",textAlign="right",
        fontFamily="'JetBrains Mono',monospace")
    col_style_left = {**col_style, "textAlign":"left"}
    hdr_s = dict(fontSize="11px")
    header = html.Div([
        html.Div("Date", style=dict(**col_style_left,**hdr_s)),
        html.Div("Symbol", style=dict(**col_style_left,**hdr_s)),
        html.Div("Description", style=dict(**col_style_left,**hdr_s)),
        html.Div("Qty", style=dict(**col_style,**hdr_s)),
        html.Div("Sell Price", style=dict(**col_style,**hdr_s)),
        html.Div("Cost Basis", style=dict(**col_style,**hdr_s)),
        html.Div("Proceeds", style=dict(**col_style,**hdr_s)),
        html.Div("Comm.", style=dict(**col_style,**hdr_s)),
        html.Div("Gain/Loss USD", style=dict(**col_style,**hdr_s)),
        html.Div("FX Rate", style=dict(**col_style,**hdr_s)),
        html.Div("Gain/Loss AUD", style=dict(**col_style,**hdr_s)),
    ], style=dict(display="grid",
        gridTemplateColumns="80px 70px 1fr 60px 80px 80px 80px 60px 100px 70px 100px",
        gap="0"))
    data_rows = []
    for _, r in df.iterrows():
        gl = r["Gain/Loss (AUD)"]
        gl_color = ACCENT if gl >= 0 else RED
        row = html.Div([
            html.Div(r["Date"], style={**col_style_left,"fontSize":"12px","color":T3}),
            html.Div(r["Symbol"], style={**col_style_left,"fontSize":"12px","fontWeight":"600"}),
            html.Div(r["Description"], style={**col_style_left,"fontSize":"12px","color":T3,"overflow":"hidden","textOverflow":"ellipsis","whiteSpace":"nowrap"}),
            html.Div(f'{r["Qty"]:.4f}', style={**col_style,"fontSize":"12px"}),
            html.Div(f'{r["Sell Price"]:.2f}', style={**col_style,"fontSize":"12px"}),
            html.Div(f'{r["Cost Basis"]:.2f}', style={**col_style,"fontSize":"12px"}),
            html.Div(f'{r["Proceeds"]:.2f}', style={**col_style,"fontSize":"12px"}),
            html.Div(f'{r["Commission"]:.2f}', style={**col_style,"fontSize":"12px"}),
            html.Div(money(r["Gain/Loss (USD)"], signed=True),
                style={**col_style,"fontSize":"12px","color":(ACCENT if r["Gain/Loss (USD)"]>=0 else RED)}),
            html.Div(f'{r["FX Rate"]:.4f}', style={**col_style,"fontSize":"12px"}),
            html.Div(money(r["Gain/Loss (AUD)"], signed=True),
                style={**col_style,"fontSize":"12px","fontWeight":"600","color":gl_color}),
        ], style=dict(display="grid",
            gridTemplateColumns="80px 70px 1fr 60px 80px 80px 80px 60px 100px 70px 100px",
            gap="0",borderBottom="1px solid rgba(255,255,255,0.04)",
            padding="0"))
        data_rows.append(row)
    table = html.Div([header] + data_rows,
        style=dict(maxHeight="500px",overflowY="auto"))
    return summary, table, df.to_dict("records")


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
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Capital Gains")
    return dcc.send_bytes(output.getvalue(), filename="capital_gains_report.xlsx")
