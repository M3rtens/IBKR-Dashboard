"""Dashboard page: news, the portfolio performance chart and the global
markets card (rotating globe + best/worst movers).

The data that fills this page's component ids is produced by the global
``refresh`` callback in :mod:`dashboard.shell`.
"""

from dash import dcc, html, Input, Output, State, no_update, ALL

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, T2, T3, T4, _RANGES, _BENCH


def chart_section():
    range_btns = html.Div([
        html.Div([
            html.Button(r, id={"type":"range-btn","index":r},
                n_clicks=0,
                style=dict(border="none",background="transparent",
                    fontFamily="'JetBrains Mono',monospace",fontSize="12px",
                    fontWeight="600",padding="5px 10px",borderRadius="7px",
                    cursor="pointer",color=T3))
            for r,_ in _RANGES
        ], style=dict(display="flex",gap="2px",
                      background="rgba(255,255,255,0.04)",
                      borderRadius="9px",padding="3px")),
    ])
    bench_btns = html.Div([
        html.Span("vs",style=dict(fontSize="11px",color=T4,fontWeight="600",
                                   letterSpacing="0.5px",textTransform="uppercase")),
        *[html.Button([
            html.Span(style=dict(width="13px",height="3px",borderRadius="2px",
                                 background=_BENCH[k]["color"],display="inline-block")),
            " " + _BENCH[k]["label"],
          ], id={"type":"bench-btn","index":k}, n_clicks=0,
             style=dict(display="flex",alignItems="center",gap="6px",
                 border="1px solid rgba(255,255,255,0.07)",
                 background="transparent",color=T2,fontSize="12px",
                 fontWeight="500",padding="5px 9px",borderRadius="8px",
                 cursor="pointer",fontFamily="inherit"))
          for k in _BENCH],
    ], style=dict(display="flex",alignItems="center",gap="7px"))
    mode_btns = html.Div([
        html.Button("$", id={"type":"mode-btn","index":"value"}, n_clicks=0,
            style=dict(border="none",background="transparent",
                fontFamily="'JetBrains Mono',monospace",fontSize="12px",
                fontWeight="600",padding="5px 10px",borderRadius="7px",
                cursor="pointer",color=T3)),
        html.Button("%", id={"type":"mode-btn","index":"pct"}, n_clicks=0,
            style=dict(border="none",background="transparent",
                fontFamily="'JetBrains Mono',monospace",fontSize="12px",
                fontWeight="600",padding="5px 10px",borderRadius="7px",
                cursor="pointer",color=T3)),
    ], style=dict(display="flex",gap="2px",
                  background="rgba(255,255,255,0.04)",
                  borderRadius="9px",padding="3px"))
    basis_select = html.Div([
        dcc.Dropdown(
            id="return-basis-dropdown",
            options=[
                {"label": "Value",  "value": "money"},
                {"label": "Return", "value": "time"},
            ],
            value="money",
            clearable=False,
            searchable=False,
            className="return-basis-dropdown",
        ),
    ], id="return-basis-wrap", style=dict(
        position="absolute",
        top="16px",
        right="20px",
        width="155px",
        zIndex="20",
    ))

    perf_chart = html.Div([
        html.Div([
            html.Div([
                html.Div("Portfolio Value",id="chart-title",style=dict(fontSize="13px",color=T3,fontWeight="500")),
                html.Div([
                    html.Div(id="chart-val",style=dict(
                        fontFamily="'Space Grotesk',sans-serif",fontSize="24px",fontWeight="600")),
                    html.Div(id="chart-ret",style=dict(
                        fontFamily="'JetBrains Mono',monospace",fontSize="13.5px",fontWeight="600")),
                    html.Div(id="chart-period",style=dict(fontSize="12.5px",color=T4)),
                ], style=dict(display="flex",alignItems="baseline",gap="10px",marginTop="5px")),
            ]),
            html.Div([
                bench_btns,
                mode_btns,
                range_btns,
            ], style=dict(marginLeft="auto",display="flex",alignItems="center",
                           gap="14px",justifyContent="flex-end",
                           position="relative",zIndex="10")),
        ], style=dict(display="flex",alignItems="flex-start",gap="16px",flexWrap="wrap")),
        basis_select,
        html.Div(dcc.Graph(id="main-chart", config={"displayModeBar":False},
                           style={"height":"280px"}),
                 style=dict(position="relative",marginTop="14px",zIndex="1")),
    ], style=dict(background=BG_CARD,border=f"1px solid {BORDER}",
                  borderRadius="14px",padding="20px 20px 14px",
                  minWidth="0", overflow="hidden", position="relative"))

    return perf_chart


def news_card():
    return html.Div([
        html.Div([
            html.Div("Latest News", style=dict(
                fontSize="14px", fontWeight="600",
                fontFamily="'Space Grotesk',sans-serif")),
        ], style=dict(marginBottom="12px")),
        html.Div(id="news-rows",
            style=dict(display="grid",
                       gridTemplateColumns="minmax(0,1fr) minmax(0,1fr) minmax(0,1fr)",
                       columnGap="16px", rowGap="0px")),
    ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                  borderRadius="14px", padding="14px 18px"))


def dashboard_page():
    markets_card = html.Div([
        html.Div("Global Markets", style=dict(
            fontSize="14px", fontWeight="600",
            fontFamily="'Space Grotesk',sans-serif", marginBottom="6px")),
        html.Div(id="world-map-body",
                 style=dict(flex="1", minWidth="0", display="flex",
                            alignItems="center", overflow="hidden")),
    ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                  borderRadius="14px", padding="16px 20px", minWidth="0",
                  overflow="hidden", display="flex", flexDirection="column"))
    return html.Div([
        news_card(),
        html.Div([chart_section(), markets_card],
            style=dict(display="grid",
                       gridTemplateColumns="5fr 3fr",
                       gap="18px", alignItems="stretch")),
        html.Div(id="portfolio-metrics-card"),
    ], style=dict(padding="24px 28px 40px", display="flex",
                  flexDirection="column", gap="18px"))


# --- Callbacks -------------------------------------------------------

# Rotate the globe clockwise (viewed from the north pole). The angle is kept in
# a window-level accumulator so it survives map re-renders on refresh. Every
# tick reasserts the full projection (animated lon + fixed lat/scale), which
# also neutralises any user attempt to drag-rotate or zoom the globe — hover
# tooltips are unaffected.
app.clientside_callback(
    """
    function(n) {
        var gd = document.querySelector('#world-map-graph .js-plotly-plot');
        if (!gd || !window.Plotly) { return ''; }
        // Only animate while the globe is actually on screen. Hidden pages stay
        // in the DOM (display:none), so without this the relayout below would
        // run 10x/sec on every tab — hogging the browser main thread and making
        // tab switches feel frozen. offsetParent is null when the node (or any
        // ancestor) is display:none, so this cheaply skips the work off-tab.
        if (gd.offsetParent === null) { return ''; }
        // A freshly-inserted graph often renders at zero size: the build
        // callback fills world-map-body before its flex/grid card is laid out,
        // and Plotly only sizes on a resize event. Since unchanged data makes
        // the build callback return no_update, that blank globe is never
        // redrawn — hence "map missing until a manual page refresh". Resize
        // once each time we first see a new graph node so it fills its card.
        if (window.__globeNode !== gd) {
            window.__globeNode = gd;
            window.Plotly.Plots.resize(gd);
        }
        window.__globeLon = (window.__globeLon || 0) - 0.6;
        window.Plotly.relayout(gd, {
            'geo.projection.rotation.lon': window.__globeLon,
            'geo.projection.rotation.lat': 12,
            'geo.projection.scale': 1
        });
        return '';
    }
    """,
    Output("globe-rotate-dummy", "children"),
    Input("globe-rotate", "n_intervals"),
)


@app.callback(
    Output("range-store","data"),
    [Input({"type":"range-btn","index":r},"n_clicks") for r,_ in _RANGES],
    prevent_initial_call=True,
)
def update_range(*_):
    from dash import ctx
    if not ctx.triggered_id: return no_update
    return ctx.triggered_id["index"]


@app.callback(
    Output("bench-store","data"),
    [Input({"type":"bench-btn","index":k},"n_clicks") for k in _BENCH],
    State("bench-store","data"),
    prevent_initial_call=True,
)
def toggle_bench(*args):
    from dash import ctx
    *_, current = args
    if not ctx.triggered_id: return no_update
    key = ctx.triggered_id["index"]
    current = list(current or [])
    return [x for x in current if x!=key] if key in current else current+[key]


@app.callback(
    Output("chart-mode-store","data"),
    Input({"type":"mode-btn","index":ALL},"n_clicks"),
    prevent_initial_call=True,
)
def update_chart_mode(clicks):
    from dash import ctx
    if not ctx.triggered_id: return no_update
    return ctx.triggered_id["index"]
