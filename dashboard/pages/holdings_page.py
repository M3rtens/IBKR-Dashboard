"""Holdings page: the sortable holdings table, asset-allocation donut and the
side rail (today's movers + dividend income).

The component ids defined here are populated by the global ``refresh`` callback
in :mod:`dashboard.shell`.
"""

from dash import dcc, html, Input, Output, State, no_update, ALL

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, ACCENT, T2, T3, T4


# Sortable columns: (sort_key, label, text-align, flex, width)
_HOLD_COLS = [
    ("ticker",     "Holding",      "left",  "1.7",  None),
    ("weight",     "Weight",       "right", "0.8",  None),
    ("value",      "Value",        "right", "1",    None),
    ("day_pct",    "Day",          "right", "0.85", None),
    ("ret",        "Total return", "right", "1.15", None),
    ("div_yield",  "Yield",        "right", "0.7",  None),
]

def _build_holdings_header(sort_col="value", sort_asc=False):
    cells = []
    for key, label, align, flex, _ in _HOLD_COLS:
        active = key == sort_col
        arrow  = "^" if sort_asc else "v"
        cells.append(html.Div(
            [label, html.Span(arrow if active else "",
                              style=dict(fontSize="9px", opacity="0.65"))],
            id={"type":"sort-hdr","index":key}, n_clicks=0,
            style=dict(
                fontSize="11px", fontWeight="600", letterSpacing="0.4px",
                textTransform="uppercase", cursor="pointer", userSelect="none",
                textAlign=align, flex=flex,
                color=T2 if active else T4,
                display="flex", alignItems="center", gap="2px",
                justifyContent="flex-end" if align=="right" else "flex-start",
            )
        ))
    # Sparkline column ??not sortable
    cells.append(html.Div("30d", style=dict(
        fontSize="11px", color=T4, fontWeight="600", letterSpacing="0.4px",
        textTransform="uppercase", userSelect="none",
        textAlign="right", width="70px", flexShrink="0",
    )))
    return html.Div(cells, style=dict(display="flex", padding="0 20px 9px",
                                      gap="8px", borderBottom=f"1px solid {BORDER}"))


def holdings_table():
    return html.Div([
        html.Div([
            html.Div("Holdings",style=dict(fontSize="14px",fontWeight="600",
                fontFamily="'Space Grotesk',sans-serif")),
            html.Div(id="holdings-count",style=dict(fontSize="12px",color=T4)),
        ], style=dict(display="flex",alignItems="center",gap="12px",padding="17px 20px 14px")),
        html.Div(id="holdings-header"),
        html.Div(id="holdings-rows"),
    ], style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                  borderRadius="14px",overflow="hidden"))


def side_rail():
    movers_card = html.Div([
        html.Div([
            html.Div("Today's Movers",style=dict(
                fontSize="14px",fontWeight="600",
                fontFamily="'Space Grotesk',sans-serif")),
            html.Div([
                html.Button("$", id={"type":"movers-btn","index":"value"}, n_clicks=0,
                    style=dict(border="none",background="transparent",
                        fontFamily="'JetBrains Mono',monospace",fontSize="11px",
                        fontWeight="600",padding="3px 7px",borderRadius="5px",
                        cursor="pointer",color=T3)),
                html.Button("%", id={"type":"movers-btn","index":"pct"}, n_clicks=0,
                    style=dict(border="none",background="transparent",
                        fontFamily="'JetBrains Mono',monospace",fontSize="11px",
                        fontWeight="600",padding="3px 7px",borderRadius="5px",
                        cursor="pointer",color=T3)),
            ], style=dict(display="flex",gap="1px",
                          background="rgba(255,255,255,0.04)",
                          borderRadius="7px",padding="2px")),
        ], style=dict(display="flex",alignItems="center",justifyContent="space-between",
                      marginBottom="13px")),
        html.Div(id="movers-rows",style=dict(display="flex",flexDirection="column",gap="13px")),
    ], style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                  borderRadius="14px",padding="18px 20px"))

    income_card = html.Div([
        html.Div([
            html.Div("Dividend Income",style=dict(
                fontSize="14px",fontWeight="600",fontFamily="'Space Grotesk',sans-serif")),
            html.Div(id="income-total",style=dict(
                marginLeft="auto",fontFamily="'JetBrains Mono',monospace",
                fontSize="13px",fontWeight="600",color=ACCENT)),
        ], style=dict(display="flex",alignItems="center")),
        html.Div(id="income-bars",
            style=dict(display="flex",alignItems="flex-end",gap="7px",
                       height="64px",margin="16px 0 6px")),
        html.Div([
            html.Div("Upcoming",style=dict(fontSize="11px",color=T4,
                fontWeight="600",letterSpacing="0.4px",textTransform="uppercase")),
            html.Div(id="dividend-rows",
                style=dict(display="flex",flexDirection="column",gap="11px",marginTop="11px")),
        ], style=dict(borderTop=f"1px solid {BORDER}",
                      marginTop="12px",paddingTop="13px")),
    ], style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                  borderRadius="14px",padding="18px 20px"))

    return html.Div([movers_card, income_card],
        style=dict(display="flex",flexDirection="column",gap="18px"))


def holdings_page():
    alloc = html.Div([
        html.Div("Asset Allocation",style=dict(
            fontSize="14px",fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif")),
        html.Div(dcc.Graph(id="donut-chart",config={"displayModeBar":False},
                           style={"height":"156px"}),
                 style=dict(display="flex",alignItems="center",
                            justifyContent="center",margin="6px 0 14px")),
        html.Div(id="alloc-legend",
                 style=dict(display="flex",flexDirection="column",gap="11px")),
    ], style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                  borderRadius="14px",padding="20px"))

    return html.Div([
        html.Div([
            holdings_table(),
            html.Div([alloc, side_rail()],
                style=dict(display="flex",flexDirection="column",
                           gap="18px",minWidth="0")),
        ], style=dict(display="grid",
            gridTemplateColumns="2fr 1fr",
            gap="18px",alignItems="start")),
    ], style=dict(padding="24px 28px 40px",display="flex",
                  flexDirection="column",gap="18px"))


# --- Callbacks -------------------------------------------------------

@app.callback(
    Output("sort-store","data"),
    Input({"type":"sort-hdr","index":ALL},"n_clicks"),
    State("sort-store","data"),
    prevent_initial_call=True,
)
def update_sort(clicks, current):
    from dash import ctx
    if not ctx.triggered_id or not any(c for c in (clicks or []) if c):
        return no_update
    col     = ctx.triggered_id["index"]
    current = current or {"col":"value","asc":False}
    if col == current.get("col"):
        return {"col": col, "asc": not current.get("asc", False)}
    # New column: default desc for numerics, asc for ticker (A?뭒)
    return {"col": col, "asc": col == "ticker"}


@app.callback(
    Output("movers-mode-store","data"),
    Input({"type":"movers-btn","index":ALL},"n_clicks"),
    prevent_initial_call=True,
)
def update_movers_mode(clicks):
    from dash import ctx
    if not ctx.triggered_id: return no_update
    return ctx.triggered_id["index"]
