"""Macro-economic dashboard page powered by the FRED API.

A country selector switches the whole dashboard between the United States
(full indicator set) and Australia, the euro area, the United Kingdom, Japan
and China (the subset FRED covers for each). Series that FRED does not carry
— or carries only with a long lag — are omitted for that country rather than
shown as empty panels. Every tile shows its observation date so staleness is
visible at a glance.
"""

import os
import time
import datetime

import requests
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, ctx, no_update

from dashboard.app_instance import app
from dashboard.theme import BG_CARD, BORDER, T1, T2, T3, T4, T5, ACCENT, RED


# ── FRED API ──────────────────────────────────────────────────────────────────
_FRED_BASE = "https://api.stlouisfed.org/fred"
_fred_cache: dict = {}
_FRED_TTL   = 3600  # 1-hour cache


def _fred_key() -> str:
    return os.environ.get("FRED_API_KEY", "")


def _fetch_fred(series_id: str, limit: int = 1500) -> list:
    """Fetch up to `limit` recent FRED observations (oldest→newest).

    Returns list of {"date": str, "value": float}.
    Falls back to last cached data on error.
    """
    now    = time.time()
    ckey   = (series_id, limit)
    cached = _fred_cache.get(ckey)

    if cached and now - cached["ts"] < _FRED_TTL:
        return cached["data"]

    api_key = _fred_key()
    if not api_key:
        return cached["data"] if cached else []

    try:
        r = requests.get(
            f"{_FRED_BASE}/series/observations",
            params=dict(series_id=series_id, api_key=api_key,
                        file_type="json", sort_order="desc",
                        limit=limit),
            timeout=15,
        )
        r.raise_for_status()
        data = []
        for o in r.json().get("observations", []):
            try:
                data.append({"date": o["date"], "value": float(o["value"])})
            except (ValueError, KeyError, TypeError):
                pass  # skip "." missing values
        data.reverse()  # API returned newest-first; restore chronological order
        _fred_cache[ckey] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"[FRED] fetch failed for {series_id}: {type(e).__name__}: {e}")
        return cached["data"] if cached else []


# ── Derived-series helpers ────────────────────────────────────────────────────
def _yoy(obs: list) -> list:
    """Monthly level series → year-over-year % change."""
    out = []
    for i in range(12, len(obs)):
        base = obs[i - 12]["value"]
        if base:
            out.append({"date": obs[i]["date"],
                        "value": (obs[i]["value"] / base - 1) * 100})
    return out


def _yoy_q(obs: list) -> list:
    """Quarterly level series → year-over-year % change."""
    out = []
    for i in range(4, len(obs)):
        base = obs[i - 4]["value"]
        if base:
            out.append({"date": obs[i]["date"],
                        "value": (obs[i]["value"] / base - 1) * 100})
    return out


def _mom(obs: list) -> list:
    """Monthly level series → month-over-month absolute change."""
    out = []
    for i in range(1, len(obs)):
        out.append({"date": obs[i]["date"],
                    "value": obs[i]["value"] - obs[i - 1]["value"]})
    return out


def _recent(obs: list, years: int = 5) -> list:
    """Filter observations to the last N years."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=365 * years)).isoformat()
    return [o for o in obs if o["date"] >= cutoff]


def _scale(obs: list, factor: float) -> list:
    """Multiply every value by factor (e.g. /1000 to convert K → M)."""
    return [{"date": o["date"], "value": o["value"] * factor} for o in obs]


# ── Plotly helpers ────────────────────────────────────────────────────────────
def _base_layout(**overrides) -> dict:
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=8, t=28, b=0),
        font=dict(color=T4, size=10,
                  family="-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)", font=dict(size=10.5, color=T3),
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a1f2e", bordercolor=BORDER,
                        font=dict(size=11, color=T1)),
        xaxis=dict(
            showgrid=False, showline=False, zeroline=False,
            tickfont=dict(size=10, color=T4), tickformat="%b '%y",
        ),
        yaxis=dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.04)",
            showline=False, zeroline=False,
            tickfont=dict(size=10, color=T4), ticksuffix="%",
        ),
    )
    base.update(overrides)
    return base


def _trace(obs: list, name: str, color: str,
           width: float = 1.8, dash=None, step: bool = False,
           yaxis: str = None) -> go.Scatter:
    dates  = [o["date"] for o in obs]
    values = [o["value"] for o in obs]
    line_d = dict(color=color, width=width)
    if dash:
        line_d["dash"] = dash
    kwargs = dict(
        x=dates, y=values, name=name, mode="lines",
        line=line_d,
        line_shape="hv" if step else "linear",
        hovertemplate=f"{name}: %{{y:.2f}}<extra></extra>",
    )
    if yaxis:
        kwargs["yaxis"] = yaxis
    return go.Scatter(**kwargs)


def _bar(obs: list, name: str, pos_color: str, yaxis: str = None) -> go.Bar:
    """Bar trace — green for positive values, red for negative."""
    dates  = [o["date"] for o in obs]
    values = [o["value"] for o in obs]
    kwargs = dict(
        x=dates, y=values, name=name,
        marker_color=[pos_color if v >= 0 else RED for v in values],
        hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>",
    )
    if yaxis:
        kwargs["yaxis"] = yaxis
    return go.Bar(**kwargs)


def _ref_line(obs: list, y_val: float, name: str) -> go.Scatter:
    """Horizontal dashed reference line spanning the data range."""
    if not obs:
        return go.Scatter(x=[], y=[], name=name)
    dates = [obs[0]["date"], obs[-1]["date"]]
    return go.Scatter(
        x=dates, y=[y_val, y_val],
        mode="lines", name=name,
        line=dict(color="rgba(255,255,255,0.22)", dash="dot", width=1.2),
        hoverinfo="skip",
        showlegend=True,
    )


def _empty_fig(msg: str = "Loading…") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=12, color=T4))
    fig.update_layout(**_base_layout())
    return fig


# ── Gauge helpers ─────────────────────────────────────────────────────────────
def _build_gauge_tiles(specs: list) -> list:
    """
    specs: list of (label, obs, suffix, delta_periods, invert_delta_color, decimals)
    invert=True  → falling value is green (inflation, unemployment, claims).
    """
    tiles = []
    for spec in specs:
        label, obs, suffix, periods, invert = spec[:5]
        decimals = spec[5] if len(spec) > 5 else 2

        if not obs:
            tiles.append(html.Div([
                html.Div(label, style=dict(
                    fontSize="10px", fontWeight="700", color=T5,
                    letterSpacing="0.8px", textTransform="uppercase",
                    marginBottom="8px")),
                html.Div("—", style=dict(
                    fontSize="20px", fontWeight="700", color=T4,
                    fontFamily="'JetBrains Mono',monospace")),
            ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                          borderRadius="12px", padding="14px 16px")))
            continue

        last       = obs[-1]
        val        = last["value"]
        label_date = last["date"]
        try:
            label_date = datetime.date.fromisoformat(label_date).strftime("%b %Y")
        except Exception:
            pass

        val_str  = f"{val:.{decimals}f}{suffix}"
        delta_el = html.Div()
        if len(obs) > periods:
            prior = obs[-(periods + 1)]["value"]
            delta = val - prior
            sign  = "+" if delta >= 0 else ""
            d_str = f"{sign}{delta:.{decimals}f}{suffix}"
            if invert:
                col = ACCENT if delta <= 0 else RED
            else:
                col = T4
            delta_el = html.Div(d_str, style=dict(
                fontSize="11px", fontWeight="500", color=col, marginTop="2px"))

        tiles.append(html.Div([
            html.Div(label, style=dict(
                fontSize="10px", fontWeight="700", color=T5,
                letterSpacing="0.8px", textTransform="uppercase",
                marginBottom="8px")),
            html.Div(val_str, style=dict(
                fontSize="20px", fontWeight="700", color=T1,
                fontFamily="'JetBrains Mono',monospace")),
            delta_el,
            html.Div(label_date, style=dict(
                fontSize="10px", color=T5, marginTop="3px")),
        ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                      borderRadius="12px", padding="14px 16px")))

    return tiles


# ── Card / grid layout ──────────────────────────────────────────────────────
def _fig_card(title: str, fig: go.Figure, height: int = 220) -> html.Div:
    """A titled card wrapping a pre-built figure."""
    return html.Div([
        html.Div(title, style=dict(
            fontSize="12.5px", fontWeight="600", color=T2,
            marginBottom="10px")),
        dcc.Graph(figure=fig, config={"displayModeBar": False},
                  style={"height": f"{height}px"}),
    ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                  borderRadius="14px", padding="16px 20px 10px",
                  minWidth="0", overflow="hidden"))


def _grid(cards: list) -> html.Div:
    """Lay cards out in a responsive two-column grid."""
    return html.Div(cards, style=dict(
        display="grid", gridTemplateColumns="1fr 1fr",
        gap="16px", marginBottom="16px"))


# ── Country registry ──────────────────────────────────────────────────────────
# code, display name, flag emoji
COUNTRIES = [
    ("WORLD", "World Overview", "🌍"),
    ("US", "United States",  "🇺🇸"),
    ("AU", "Australia",       "🇦🇺"),
    ("EA", "Euro Area",       "🇪🇺"),
    ("UK", "United Kingdom",  "🇬🇧"),
    ("JP", "Japan",           "🇯🇵"),
    ("CN", "China",           "🇨🇳"),
]
_COUNTRY_CODES = [c[0] for c in COUNTRIES]

# International series (non-US). Any concept set to None is not carried by FRED
# for that country and is skipped. FX is quoted as noted; `fx_lbl` labels it and
# `fx_dec` sets its display precision.
#   policy   — central-bank / overnight rate (monthly, %)
#   short3m  — 3-month interbank rate (monthly, %)
#   y10      — 10-year government bond yield (monthly, %)
#   cpi_m    — headline CPI level, monthly  → YoY computed
#   cpi_q    — headline CPI level, quarterly → YoY computed
#   gdp_q    — real GDP level, quarterly     → YoY computed
#   unrate   — harmonised unemployment rate (monthly, %)
#   cli      — OECD composite leading indicator (amplitude-adj, ~100 = trend)
#   fx       — exchange rate vs USD
# short3m + cli are available fresh across these countries; the OECD MEI series
# for euro-area CLI, plus JP CPI and CN GDP/10Y/unemployment, are discontinued
# on FRED (long lag), so those concepts stay unset for the affected countries.
INTL = {
    "AU": dict(policy="IRSTCI01AUM156N", short3m="IR3TIB01AUM156N",
               y10="IRLTLT01AUM156N",
               cpi_q="AUSCPIALLQINMEI", gdp_q="NGDPRSAXDCAUQ",
               unrate="LRUNTTTTAUM156S", cli="AUSLOLITOAASTSAM",
               fx="DEXUSAL", fx_lbl="AUD/USD", fx_dec=4),
    "EA": dict(policy="ECBDFR", short3m="IR3TIB01EZM156N",
               y10="IRLTLT01EZM156N",
               cpi_m="CP0000EZ19M086NEST", gdp_q="CLVMNACSCAB1GQEA19",
               unrate=None, cli=None,
               fx="DEXUSEU", fx_lbl="EUR/USD", fx_dec=4),
    "UK": dict(policy="IRSTCI01GBM156N", short3m="IR3TIB01GBM156N",
               y10="IRLTLT01GBM156N",
               cpi_m="GBRCPIALLMINMEI", gdp_q="NGDPRSAXDCGBQ",
               unrate="LRHUTTTTGBM156S", cli="GBRLOLITOAASTSAM",
               fx="DEXUSUK", fx_lbl="GBP/USD", fx_dec=4),
    "JP": dict(policy="IRSTCI01JPM156N", short3m="IR3TIB01JPM156N",
               y10="IRLTLT01JPM156N",
               cpi_m=None, gdp_q="JPNRGDPEXP",
               unrate="LRHUTTTTJPM156S", cli="JPNLOLITOAASTSAM",
               fx="DEXJPUS", fx_lbl="JPY/USD", fx_dec=2),
    "CN": dict(policy="INTDSRCNM193N", short3m="IR3TIB01CNM156N",
               y10=None,
               cpi_m="CHNCPIALLMINMEI", gdp_q=None,
               unrate=None, cli="CHNLOLITOAASTSAM",
               fx="DEXCHUS", fx_lbl="CNY/USD", fx_dec=3),
}


# ── US builder (full indicator set) ─────────────────────────────────────────
def _build_us():
    """Return (gauge_tiles, [chart cards]) for the United States."""
    # Rates
    fedfunds  = _fetch_fred("FEDFUNDS",     120)
    dgs2      = _fetch_fred("DGS2",         2600)
    dgs10     = _fetch_fred("DGS10",        2600)
    dgs30     = _fetch_fred("DGS30",        2600)
    t10y2y    = _fetch_fred("T10Y2Y",       2600)
    t10y3m    = _fetch_fred("T10Y3M",       2600)
    t5yifr    = _fetch_fred("T5YIFR",       2600)
    # Inflation
    cpi       = _fetch_fred("CPIAUCSL",     240)
    pce       = _fetch_fred("PCEPILFE",     240)
    t10yie    = _fetch_fred("T10YIE",       2600)
    # Credit
    ig_oas    = _fetch_fred("BAMLC0A0CM",   2600)
    hy_oas    = _fetch_fred("BAMLH0A0HYM2", 2600)
    nfci      = _fetch_fred("NFCI",         520)     # financial conditions, weekly
    # Labour
    unrate    = _fetch_fred("UNRATE",       240)
    payems    = _fetch_fred("PAYEMS",       240)
    icsa      = _fetch_fred("ICSA",         500)
    jtsjol    = _fetch_fred("JTSJOL",       240)
    # Growth
    gdpc1     = _fetch_fred("GDPC1",        120)
    indpro    = _fetch_fred("INDPRO",       240)
    usslind   = _fetch_fred("USSLIND",      240)
    # Housing & consumer
    houst     = _fetch_fred("HOUST",        240)     # housing starts, thousands
    rsafs     = _fetch_fred("RSAFS",        240)     # retail sales, $M
    umcsent   = _fetch_fred("UMCSENT",      240)     # consumer sentiment
    # FX & Liquidity
    dtwexbgs  = _fetch_fred("DTWEXBGS",     2600)
    m2sl      = _fetch_fred("M2SL",         240)

    # Derived series
    cpi_yoy    = _yoy(cpi)
    pce_yoy    = _yoy(pce)
    nfp_mom    = _mom(payems)
    icsa_k     = _scale(icsa, 1 / 1_000)
    jtsjol_m   = _scale(jtsjol, 1 / 1_000)
    gdp_yoy    = _yoy_q(gdpc1)
    indpro_yoy = _yoy(indpro)
    lei_yoy    = _yoy(usslind)
    m2_yoy     = _yoy(m2sl)
    retail_yoy = _yoy(rsafs)

    # Gauges — two rows of six
    gauge_specs = [
        ("Fed Funds",       fedfunds,  "%",  3,  False, 2),
        ("10Y UST",         dgs10,     "%",  63, False, 2),
        ("10Y–2Y Spread",   t10y2y,    "pp", 63, False, 2),
        ("CPI (YoY)",       cpi_yoy,   "%",  3,  True,  2),
        ("Core PCE (YoY)",  pce_yoy,   "%",  3,  True,  2),
        ("Unemployment",    unrate,    "%",  3,  True,  2),
        ("NFP MoM",         nfp_mom,   "K",  1,  False, 0),
        ("Init. Claims",    icsa_k,    "K",  4,  True,  0),
        ("JOLTS Openings",  jtsjol_m,  "M",  3,  False, 2),
        ("Real GDP (YoY)",  gdp_yoy,   "%",  2,  False, 2),
        ("USD Index",       dtwexbgs,  "",   63, False, 1),
        ("M2 (YoY)",        m2_yoy,    "%",  3,  False, 2),
    ]
    gauges = _build_gauge_tiles(gauge_specs)

    cards = []

    # Interest Rates
    r_ff, r_2y, r_10y, r_30y = (_recent(fedfunds, 5), _recent(dgs2, 5),
                                _recent(dgs10, 5), _recent(dgs30, 5))
    if r_ff or r_10y or r_2y:
        fig = go.Figure([
            _trace(r_ff,  "Fed Funds", "#7fb2ff", step=True),
            _trace(r_2y,  "2Y UST",    "#e8b86d"),
            _trace(r_10y, "10Y UST",   ACCENT),
            _trace(r_30y, "30Y UST",   "#c084fc"),
        ])
        fig.update_layout(**_base_layout())
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Interest Rates", fig))

    # Inflation
    r_cpi, r_pce = _recent(cpi_yoy, 5), _recent(pce_yoy, 5)
    r_10ie, r_5y5y = _recent(t10yie, 5), _recent(t5yifr, 5)
    if r_cpi or r_pce or r_10ie:
        traces = [
            _trace(r_cpi,  "CPI YoY",       RED),
            _trace(r_pce,  "Core PCE YoY",  "#fb923c"),
            _trace(r_10ie, "10Y Breakeven", "#c084fc"),
            _trace(r_5y5y, "5Y5Y Forward",  "#f472b6"),
        ]
        ref = r_cpi or r_pce
        if ref:
            traces.append(_ref_line(ref, 2.0, "Fed Target 2%"))
        fig = go.Figure(traces)
        fig.update_layout(**_base_layout())
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Inflation", fig))

    # Growth & Activity
    r_gdp, r_indpro, r_lei = (_recent(gdp_yoy, 5), _recent(indpro_yoy, 5),
                              _recent(lei_yoy, 5))
    if r_gdp or r_indpro or r_lei:
        ref = r_gdp or r_indpro or r_lei
        fig = go.Figure([
            _trace(r_gdp,    "Real GDP YoY",      ACCENT,    width=2.2),
            _trace(r_indpro, "Indust. Prod. YoY", "#e8b86d"),
            _trace(r_lei,    "LEI YoY",           "#c084fc", dash="dash"),
            _ref_line(ref, 0.0, "Zero"),
        ])
        fig.update_layout(**_base_layout())
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Growth & Activity", fig))

    # Credit Spreads & Financial Conditions
    r_ig, r_hy, r_nfci = _recent(ig_oas, 5), _recent(hy_oas, 5), _recent(nfci, 5)
    if r_ig or r_hy:
        fig = go.Figure([
            _trace(r_ig,   "IG OAS", ACCENT),
            _trace(r_hy,   "HY OAS", RED),
            _trace(r_nfci, "NFCI (rhs)", "#e8b86d", dash="dash", yaxis="y2"),
        ])
        fig.update_layout(**_base_layout(
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        zeroline=False, tickfont=dict(size=10, color=T4),
                        ticksuffix=""),
        ))
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Credit Spreads & Financial Conditions", fig))

    # Labour Market
    r_ur, r_jolts = _recent(unrate, 10), _recent(jtsjol_m, 10)
    if r_ur:
        fig = go.Figure([
            _trace(r_ur,    "Unemployment Rate",  "#e879f9"),
            _ref_line(r_ur, 4.0, "~Full Employment 4%"),
            _trace(r_jolts, "JOLTS Openings (M)", "#7fb2ff",
                   dash="dash", yaxis="y2"),
        ])
        fig.update_layout(**_base_layout(
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        zeroline=False, tickfont=dict(size=10, color=T4),
                        ticksuffix="M"),
        ))
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Labour Market", fig))

    # FX & Liquidity
    r_m2, r_usd = _recent(m2_yoy, 5), _recent(dtwexbgs, 5)
    if r_m2 or r_usd:
        fig = go.Figure([
            _trace(r_m2,  "M2 YoY",    "#34d399"),
            _trace(r_usd, "USD Index", "#e8b86d", yaxis="y2"),
        ])
        fig.update_layout(**_base_layout(
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        zeroline=False, tickfont=dict(size=10, color=T4),
                        ticksuffix=""),
        ))
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("FX & Liquidity", fig))

    # Yield Curve & Recession Signal
    r_c2y, r_c3m = _recent(t10y2y, 8), _recent(t10y3m, 8)
    if r_c2y or r_c3m:
        ref = r_c2y or r_c3m
        fig = go.Figure([
            _trace(r_c2y, "10Y–2Y", ACCENT),
            _trace(r_c3m, "10Y–3M", "#7fb2ff"),
            _ref_line(ref, 0.0, "Inversion (0)"),
        ])
        fig.update_layout(**_base_layout(yaxis=dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.04)",
            showline=False, zeroline=False,
            tickfont=dict(size=10, color=T4), ticksuffix="pp")))
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Yield Curve & Recession Signal", fig))

    # Housing & Consumer
    r_houst, r_retail, r_sent = (_recent(houst, 8), _recent(retail_yoy, 8),
                                 _recent(umcsent, 8))
    if r_houst or r_retail or r_sent:
        fig = go.Figure([
            _trace(r_retail, "Retail Sales YoY", "#34d399"),
            _trace(r_houst,  "Housing Starts (K, rhs)", "#e8b86d", yaxis="y2"),
            _trace(r_sent,   "U-Mich Sentiment (rhs2)", "#c084fc",
                   dash="dash", yaxis="y3"),
        ])
        fig.update_layout(**_base_layout(
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        zeroline=False, tickfont=dict(size=10, color=T4),
                        ticksuffix="K"),
            yaxis3=dict(overlaying="y", side="right", showgrid=False,
                        zeroline=False, showticklabels=False, position=1.0),
        ))
    else:
        fig = _empty_fig("No data")
    cards.append(_fig_card("Housing & Consumer", fig))

    return gauges, cards


# ── International builder (per-country subset) ───────────────────────────────
def _pct_axis_layout(**overrides) -> dict:
    return _base_layout(**overrides)


def _plain_axis_layout(suffix: str = "") -> dict:
    return _base_layout(yaxis=dict(
        showgrid=True, gridcolor="rgba(255,255,255,0.04)",
        showline=False, zeroline=False,
        tickfont=dict(size=10, color=T4), ticksuffix=suffix))


def _build_country(code: str):
    """Return (gauge_tiles, [chart cards]) for a non-US country from `INTL`."""
    cfg = INTL[code]

    def _get(concept, limit):
        sid = cfg.get(concept)
        return _fetch_fred(sid, limit) if sid else []

    policy  = _get("policy",  200)
    short3m = _get("short3m", 200)
    y10     = _get("y10",     200)
    unrate  = _get("unrate",  240)
    fx      = _get("fx",      2600)
    gdp     = _get("gdp_q",   60)
    cpi_m   = _get("cpi_m",   240)
    cpi_q   = _get("cpi_q",   80)
    cli     = _get("cli",     240)

    gdp_yoy = _yoy_q(gdp) if gdp else []
    if cpi_m:
        cpi_yoy = _yoy(cpi_m)
    elif cpi_q:
        cpi_yoy = _yoy_q(cpi_q)
    else:
        cpi_yoy = []

    # ── Gauges — only tiles backed by a configured series ──────────────────
    fx_val   = fx[-1]["value"] if fx else 0
    fx_dec   = cfg.get("fx_dec", 4)
    specs = []
    if cfg.get("policy"):
        specs.append(("Policy Rate",   policy,  "%", 1,  False, 2))
    if cfg.get("short3m"):
        specs.append(("3M Rate",       short3m, "%", 1,  False, 2))
    if cfg.get("y10"):
        specs.append(("10Y Yield",     y10,     "%", 1,  False, 2))
    if cpi_yoy:
        specs.append(("CPI (YoY)",     cpi_yoy, "%", 1,  True,  1))
    if gdp_yoy:
        specs.append(("Real GDP (YoY)", gdp_yoy, "%", 1,  False, 1))
    if cfg.get("cli") and cli:
        specs.append(("Lead. Indicator", cli,   "",  1,  False, 1))
    if cfg.get("unrate"):
        specs.append(("Unemployment",  unrate,  "%", 1,  True,  2))
    if cfg.get("fx"):
        specs.append((cfg["fx_lbl"],   fx,      "",  21, False, fx_dec))
    gauges = _build_gauge_tiles(specs)

    # ── Charts — only those with data ──────────────────────────────────────
    cards = []

    # Interest rates: policy + 3M + 10Y
    r_pol, r_3m, r_y10 = (_recent(policy, 10), _recent(short3m, 10),
                          _recent(y10, 10))
    if r_pol or r_3m or r_y10:
        traces = []
        if r_pol:
            traces.append(_trace(r_pol, "Policy Rate", "#7fb2ff", step=True))
        if r_3m:
            traces.append(_trace(r_3m, "3M Rate", "#e8b86d"))
        if r_y10:
            traces.append(_trace(r_y10, "10Y Yield", ACCENT))
        fig = go.Figure(traces)
        fig.update_layout(**_pct_axis_layout())
        cards.append(_fig_card("Interest Rates", fig))

    # Inflation: CPI YoY vs 2% target
    r_cpi = _recent(cpi_yoy, 6)
    if r_cpi:
        fig = go.Figure([
            _trace(r_cpi, "CPI YoY", RED),
            _ref_line(r_cpi, 2.0, "2% Target"),
        ])
        fig.update_layout(**_pct_axis_layout())
        cards.append(_fig_card("Inflation (CPI YoY)", fig))

    # Growth: real GDP YoY
    r_gdp = _recent(gdp_yoy, 6)
    if r_gdp:
        fig = go.Figure([
            _trace(r_gdp, "Real GDP YoY", ACCENT, width=2.2),
            _ref_line(r_gdp, 0.0, "Zero"),
        ])
        fig.update_layout(**_pct_axis_layout())
        cards.append(_fig_card("Growth (Real GDP YoY)", fig))

    # Leading indicator: OECD CLI vs trend (100)
    r_cli = _recent(cli, 5)
    if r_cli:
        fig = go.Figure([
            _trace(r_cli, "OECD CLI", "#c084fc", width=2.2),
            _ref_line(r_cli, 100.0, "Trend = 100"),
        ])
        fig.update_layout(**_plain_axis_layout(""))
        cards.append(_fig_card("Leading Indicator (OECD CLI)", fig))

    # Labour: unemployment rate
    r_ur = _recent(unrate, 10)
    if r_ur:
        fig = go.Figure([_trace(r_ur, "Unemployment Rate", "#e879f9")])
        fig.update_layout(**_pct_axis_layout())
        cards.append(_fig_card("Labour Market", fig))

    # FX vs USD
    r_fx = _recent(fx, 5)
    if r_fx:
        fig = go.Figure([_trace(r_fx, cfg["fx_lbl"], "#e8b86d")])
        fig.update_layout(**_plain_axis_layout(""))
        cards.append(_fig_card(f"Exchange Rate ({cfg['fx_lbl']})", fig))

    if not cards:
        cards.append(_fig_card("Coverage",
                               _empty_fig("No FRED data available")))
    return gauges, cards


# ── World overview (culmination of all countries) ───────────────────────────
_WORLD_CODES  = ["US", "AU", "EA", "UK", "JP", "CN"]
_WORLD_COLORS = {
    "US": "#7fb2ff", "AU": ACCENT, "EA": "#e8b86d",
    "UK": "#c084fc", "JP": "#f472b6", "CN": "#fb923c",
}
# (concept key, chart title, y-suffix, reference-line value or None)
_WORLD_INDICATORS = [
    ("policy", "Policy Rates",                 "%", None),
    ("y10",    "10-Year Yields",               "%", None),
    ("cpi",    "Inflation (CPI YoY)",          "%", 2.0),
    ("gdp",    "Real GDP (YoY)",               "%", 0.0),
    ("unrate", "Unemployment",                 "%", None),
    ("cli",    "Leading Indicator (OECD CLI)", "",  100.0),
]


def _core_series(code: str) -> dict:
    """Fetch + derive the shared macro indicators for one country, for the
    cross-country World view.

    Returns a dict of obs-lists keyed by concept (policy, y10, cpi, gdp, unrate,
    cli). CPI and GDP are pre-converted to YoY %. Empty list where FRED does not
    carry the series for that country.
    """
    if code == "US":
        return dict(
            policy=_fetch_fred("FEDFUNDS", 200),
            y10   =_fetch_fred("GS10",     200),           # monthly 10Y (matches others)
            cpi   =_yoy(_fetch_fred("CPIAUCSL", 240)),
            gdp   =_yoy_q(_fetch_fred("GDPC1", 120)),
            unrate=_fetch_fred("UNRATE",   240),
            cli   =_fetch_fred("USALOLITOAASTSAM", 240),
        )
    cfg = INTL[code]

    def _g(concept, limit):
        sid = cfg.get(concept)
        return _fetch_fred(sid, limit) if sid else []

    if cfg.get("cpi_m"):
        cpi = _yoy(_g("cpi_m", 240))
    elif cfg.get("cpi_q"):
        cpi = _yoy_q(_g("cpi_q", 80))
    else:
        cpi = []

    return dict(
        policy=_g("policy", 200),
        y10   =_g("y10",    200),
        cpi   =cpi,
        gdp   =_yoy_q(_g("gdp_q", 60)) if cfg.get("gdp_q") else [],
        unrate=_g("unrate", 240),
        cli   =_g("cli",    240),
    )


def _build_world():
    """Return (gauge_tiles, [chart cards]) for the global overview.

    Average gauges summarise the latest reading across every country FRED
    covers, and each indicator gets one comparison chart overlaying all
    countries so divergences (rate cuts, inflation, growth) read at a glance.
    """
    data = {c: _core_series(c) for c in _WORLD_CODES}

    def _latest(c, key):
        s = data[c].get(key) or []
        return s[-1]["value"] if s else None

    def _avg(key):
        vals = [v for v in (_latest(c, key) for c in _WORLD_CODES) if v is not None]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    def _world_tile(label, key, suffix, decimals):
        val, n = _avg(key)
        value_str = "—" if val is None else f"{val:.{decimals}f}{suffix}"
        return html.Div([
            html.Div(label, style=dict(
                fontSize="10px", fontWeight="700", color=T5,
                letterSpacing="0.8px", textTransform="uppercase",
                marginBottom="8px")),
            html.Div(value_str, style=dict(
                fontSize="20px", fontWeight="700",
                color=T1 if val is not None else T4,
                fontFamily="'JetBrains Mono',monospace")),
            html.Div(f"avg · {n}/{len(_WORLD_CODES)} countries", style=dict(
                fontSize="10px", color=T5, marginTop="3px")),
        ], style=dict(background=BG_CARD, border=f"1px solid {BORDER}",
                      borderRadius="12px", padding="14px 16px"))

    gauges = [
        _world_tile("Avg Policy Rate",  "policy", "%", 2),
        _world_tile("Avg 10Y Yield",    "y10",    "%", 2),
        _world_tile("Avg CPI (YoY)",    "cpi",    "%", 1),
        _world_tile("Avg GDP (YoY)",    "gdp",    "%", 1),
        _world_tile("Avg Unemployment", "unrate", "%", 2),
        _world_tile("Avg Lead. Ind.",   "cli",    "",  1),
    ]

    cards = []
    for key, title, suffix, ref in _WORLD_INDICATORS:
        traces, all_dates = [], []
        for c in _WORLD_CODES:
            s = _recent(data[c].get(key) or [], 5)
            if s:
                traces.append(_trace(s, c, _WORLD_COLORS[c]))
                all_dates += [o["date"] for o in s]
        if not traces:
            continue
        if ref is not None and all_dates:
            span = [{"date": min(all_dates)}, {"date": max(all_dates)}]
            ref_lbl = ("2% Target" if key == "cpi"
                       else "Trend = 100" if key == "cli" else "Zero")
            traces.append(_ref_line(span, ref, ref_lbl))
        fig = go.Figure(traces)
        fig.update_layout(**(_pct_axis_layout() if suffix == "%"
                             else _plain_axis_layout("")))
        cards.append(_fig_card(title, fig))

    return gauges, cards


# ── Country selector ──────────────────────────────────────────────────────────
def _country_pills() -> html.Div:
    btns = []
    for code, name, flag in COUNTRIES:
        btns.append(html.Button(
            f"{flag}  {code}",
            id={"type": "macro-country", "index": code}, n_clicks=0,
            title=name,
            style=dict(border="none", background="transparent", color=T3,
                       fontFamily="'JetBrains Mono',monospace", fontSize="12px",
                       fontWeight="600", padding="6px 12px", borderRadius="7px",
                       cursor="pointer")))
    return html.Div(btns, style=dict(
        display="inline-flex", gap="2px",
        background="rgba(255,255,255,0.04)", borderRadius="9px", padding="3px"))


# ── Layout ────────────────────────────────────────────────────────────────────
def macro_page() -> html.Div:
    return html.Div([
        # Country selector + source note
        html.Div([
            _country_pills(),
            html.Span("Source: Federal Reserve Economic Data (FRED)",
                      style=dict(marginLeft="auto", fontSize="11px", color=T5)),
        ], style=dict(display="flex", alignItems="center",
                      gap="14px", marginBottom="18px")),

        # Gauge tiles + charts, wrapped in a loading overlay so switching
        # country shows a spinner while FRED series are fetched.
        dcc.Loading(
            id="macro-loading",
            type="circle",
            color=ACCENT,
            children=[
                # Gauge tiles (auto-wrapping six-column grid)
                html.Div(id="macro-gauges", style=dict(
                    display="grid", gridTemplateColumns="repeat(6, 1fr)",
                    gap="12px", marginBottom="18px")),

                # Charts (built per country)
                html.Div(id="macro-charts"),
            ],
        ),

        dcc.Store(id="macro-country-store", data="WORLD"),
        dcc.Interval(id="macro-refresh", interval=3_600_000, n_intervals=0),
    ], style=dict(padding="24px 28px 40px"))


# ── Callbacks ─────────────────────────────────────────────────────────────────
@app.callback(
    Output("macro-country-store", "data"),
    [Input({"type": "macro-country", "index": c}, "n_clicks")
     for c in _COUNTRY_CODES],
    prevent_initial_call=True,
)
def _set_country(*_):
    if not ctx.triggered_id:
        return no_update
    return ctx.triggered_id["index"]


# Pill highlight lives in its own callback keyed only on the country store, so
# the newly selected country lights up the instant it is clicked — the slow
# FRED build below no longer gates the highlight, which previously only moved
# once the charts had finished loading.
@app.callback(
    *[Output({"type": "macro-country", "index": c}, "style")
      for c in _COUNTRY_CODES],
    Input("macro-country-store", "data"),
)
def _highlight_country(country):
    country = country if country in _COUNTRY_CODES else "WORLD"
    active = dict(border="none", background="rgba(96,165,250,0.16)",
                  color=ACCENT, fontFamily="'JetBrains Mono',monospace",
                  fontSize="12px", fontWeight="700", padding="6px 12px",
                  borderRadius="7px", cursor="pointer")
    inactive = dict(border="none", background="transparent", color=T3,
                    fontFamily="'JetBrains Mono',monospace", fontSize="12px",
                    fontWeight="600", padding="6px 12px", borderRadius="7px",
                    cursor="pointer")
    return tuple(active if c == country else inactive for c in _COUNTRY_CODES)


@app.callback(
    Output("macro-gauges", "children"),
    Output("macro-charts", "children"),
    Input("macro-refresh",        "n_intervals"),
    Input("macro-country-store",  "data"),
)
def refresh_macro(_n, country):
    country = country if country in _COUNTRY_CODES else "WORLD"

    key = _fred_key()
    if not key:
        msg = _empty_fig("FRED_API_KEY not set — add it to your .env file")
        return [], _grid([_fig_card("Macro", msg)])

    if country == "WORLD":
        gauges, cards = _build_world()
    elif country == "US":
        gauges, cards = _build_us()
    else:
        gauges, cards = _build_country(country)

    # Pack cards into rows of two
    rows = [_grid(cards[i:i + 2]) for i in range(0, len(cards), 2)]
    return gauges, rows
