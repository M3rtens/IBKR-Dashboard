# IBKR Portfolio Dashboard

A real-time portfolio dashboard for Interactive Brokers, built with
[Dash](https://dash.plotly.com/) and [ib_insync](https://github.com/erdewit/ib_insync).

It shows account metrics, allocation, per-position P&L and history, plus
security research, macro and market context, capital-gains reporting and
portfolio optimisation — auto-refreshing on an interval.



https://github.com/user-attachments/assets/c487652a-66b7-437e-a4dc-0f892f2f3fa1

<img width="1917" height="1060" alt="Security-1" src="https://github.com/user-attachments/assets/2e4d3133-241f-499d-8fb3-964c5718df9b" />


<img width="1917" height="1062" alt="Security-2" src="https://github.com/user-attachments/assets/ed3977df-f0b5-435d-88b6-4df3288bc761" />

<img width="1917" height="1066" alt="Holdings" src="https://github.com/user-attachments/assets/ffa44cd8-b6c5-44ee-8efa-3a6c221fa9b0" />

<img width="1917" height="1065" alt="Backtest" src="https://github.com/user-attachments/assets/3b8c2a9f-bbe9-4d96-a4f3-1c20b6748ea4" />

<img width="1917" height="1062" alt="Optimization" src="https://github.com/user-attachments/assets/9e705207-2d2c-4a3d-b793-9495e82ed89b" />


## Features

- **Dashboard** — account metrics (Net Liquidation, Cash, Unrealised/Realised
  P&L, Day P&L, Gross Position Value), allocation donut, holdings table,
  today's movers, income/dividends, a news ticker, a rotating world-map globe
  and a market-cap-weighted portfolio-metrics card.
- **Holdings** — sortable, filterable positions with P&L colour coding and
  sparklines.
- **Security detail** — candlestick price chart with an optional log-space
  **Kalman-smoothed** overlay, multi-range selector (1D–5Y), buy/sell trade
  markers, per-symbol news, and full financials (income statement, balance
  sheet, cash flow). Prices and figures render in the security's own currency.
- **Daily Market Summary** — a cross-asset board (equity indices, rates &
  volatility, commodities, currencies, crypto) with multi-horizon returns, a
  per-asset-class narrative read and **click-to-sort** columns.
- **Macro** — per-country macro dashboard powered by FRED (rates, inflation,
  growth, labour, leading indicators, FX), plus a **World overview** that
  aggregates every country onto comparison charts.
- **Performance** and **Backtest** pages.
- **Capital Gains Tax** — an Australian CGT report from Flex Query trade data.
- **Optimisation** — mean-variance, max-Sharpe, min-variance, risk parity and
  hierarchical risk parity, with a current-vs-optimised comparison and backtest.
- **Demo mode** — runs with mock data, no TWS connection needed.

## Data sources

- **Portfolio** (positions, account, history, dividends, trades) — IBKR API and
  Flex Query. Always sourced from IBKR regardless of the market-data setting.
- **Market data** (sparklines, daily moves, benchmarks, the market board,
  security detail) — yfinance by default, or the IBKR API (`MARKET_DATA_SOURCE`).
- **Macro** — the [FRED API](https://fred.stlouisfed.org/docs/api/fred/)
  (requires a free `FRED_API_KEY`).

## Setup

```bash
pip install -r requirements.txt
```

Configuration can be supplied via the environment or a `.env` file in the
project root (loaded automatically at startup).

## Running

### Against a live IBKR connection

1. Start **TWS** or **IB Gateway** and enable the API
   (*Configure → API → Settings → Enable ActiveX and Socket Clients*).
2. Confirm the socket port matches `ConnectionConfig` in `services/ibkr_client.py`:
   - `7497` — TWS paper trading (default)
   - `7496` — TWS live
   - `4002` / `4001` — IB Gateway paper / live
3. Run the app:

   ```bash
   python app.py
   ```

4. Open <http://127.0.0.1:8050>.

The client connects **read-only**, so the dashboard can never place orders.

### Demo mode (no TWS required)

```bash
# PowerShell
$env:DASH_DEMO = "1"; python app.py

# bash
DASH_DEMO=1 python app.py
```

## Configuration

| Env var                | Default    | Purpose                                                        |
| ---------------------- | ---------- | -------------------------------------------------------------- |
| `DASH_DEMO`            | `0`        | `1` = use mock data (no TWS connection)                        |
| `DASH_REFRESH_SECONDS` | `10`       | Dashboard auto-refresh interval (seconds)                      |
| `MARKET_DATA_SOURCE`   | `yfinance` | Market-data backend: `yfinance` or `ibkr`                      |
| `FRED_API_KEY`         | *(unset)*  | FRED API key — required for the Macro page                     |
| `IBKR_FLEX_TOKEN`      | *(unset)*  | Flex Query token — enables portfolio history / dividends / trades |
| `IBKR_FLEX_QUERY_ID`   | *(unset)*  | Flex Query ID (paired with the token)                          |
| `IBKR_PORTFOLIO_START` | *(unset)*  | Clip portfolio history to on/after this date (`YYYY-MM-DD`)    |
| `DASH_PERF`            | `1`        | `0` = silence `[PERF]` timing logs                             |
| `MD_DEBUG`             | `1`        | `0` = silence `[MD]` market-data debug logs                    |

Connection host/port/client-id live in `ConnectionConfig` (`services/ibkr_client.py`).

## Project layout

The entry point is `app.py` (run: `python app.py`). The application itself
lives in the `dashboard` package:

```
app.py                     # entry point: loads .env, builds layout, runs server
dashboard/
  theme.py                 # colours, fonts and shared constants
  app_instance.py          # the singleton Dash app
  formatters.py            # money / percentage / P&L / currency formatting
  icons.py                 # SVG icon bodies + image helpers
  charts.py                # Plotly figure builders, SVG sparkline, Kalman filter
  data.py                  # data layer: demo + live snapshots, news, history
  shell.py                 # sidebar, layout assembly, navigation + the global
                           #   data-refresh callback
  pages/                   # one module per page (layout + its own callbacks)
    dashboard_page.py
    holdings_page.py
    search_page.py
    security_page.py
    cgt_page.py
    optimize_page.py
    performance_page.py
    backtest_page.py
    macro_page.py          # FRED macro dashboard + World overview
    markets_page.py        # Daily Market Summary board
assets/style.css           # CSS (auto-loaded by Dash)
services/                  # non-UI backend layer
  ibkr_client.py           # thread-safe IBKR API wrapper (ib_insync)
  flex_query.py            # IBKR Flex Query loader (history, dividends, trades)
  market_data.py           # yfinance market-data layer (cached, batched)
  optimizer.py             # portfolio optimisation methods
requirements.txt           # pinned dependencies
```

The global `refresh` callback in `shell.py` is intentionally a single
cross-cutting callback: it calls `get_data()` once per tick and fans the result
out to the dashboard, holdings, side-rail, news and world-map components. Heavy
or off-screen work (the market board, macro fetches, portfolio metrics) is
gated to run only while its tab is visible or on background threads, so it
doesn't block navigation.
