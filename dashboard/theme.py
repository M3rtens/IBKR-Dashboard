"""Theme constants and shared configuration.

Colours, fonts, allocation palettes and the demo dataset definitions used
across the dashboard. This module is a dependency-free leaf — it must not
import any other ``dashboard`` module.
"""

import os

# --- Config ----------------------------------------------------------
DEMO_MODE = os.environ.get("DASH_DEMO", "0") == "1"
REFRESH   = int(os.environ.get("DASH_REFRESH_SECONDS", "10"))

# Source for the market-data layer (sparklines, daily %, movers, benchmarks,
# world map, security detail): "yfinance" (default; no IBKR subscription
# needed) or "ibkr" (use the IBKR API paths). Core portfolio data always comes
# from IBKR/Flex regardless of this setting.
MARKET_DATA_SOURCE = os.environ.get("MARKET_DATA_SOURCE", "yfinance").strip().lower()


BG = "#0a0c10"; BG_SIDE = "#0c0e13"; BG_CARD = "#13161d"
BORDER = "rgba(255,255,255,0.06)"; ACCENT = "#36d399"; RED = "#ff6b6b"
T1="#e8eaef"; T2="#9aa0ad"; T3="#7a808d"; T4="#6c7280"; T5="#5b616e"

ALLOC_HEX = {
    "Australian Shares": "#4ade80", "International Shares": "#60a5fa",
    "Bonds": "#c084fc", "Property": "#fbbf24", "Cash": "#94a3b8",
    "Securities": "#60a5fa",
}
HOLD_CLR = {
    "Australian Shares": "oklch(0.80 0.13 158)",
    "International Shares": "oklch(0.76 0.13 235)",
    "Bonds": "oklch(0.74 0.13 300)",
    "Property": "oklch(0.82 0.12 78)",
    "Cash": "oklch(0.72 0.015 250)",
}

_DEMO_RAW = [
    ("VAS","Vanguard Australian Shares",  "Australian Shares",    92400,71200, 0.42),
    ("VGS","Vanguard MSCI Intl Shares",   "International Shares", 84150,60300, 0.78),
    ("CBA","Commonwealth Bank",           "Australian Shares",    41600,28900,-0.31),
    ("NDQ","Betashares Nasdaq 100",       "International Shares", 38900,22700, 1.45),
    ("BHP","BHP Group",                   "Australian Shares",    33250,30100, 1.12),
    ("CSL","CSL Limited",                 "Australian Shares",    29800,24500,-0.64),
    ("WES","Wesfarmers",                  "Australian Shares",    21300,17800, 0.22),
    ("VAF","Vanguard Aus Fixed Interest", "Bonds",                18600,19200, 0.05),
    ("CASH","Cash (AUD)",                 "Cash",                 14800,14800, 0.00),
    ("TLS","Telstra Group",               "Australian Shares",    12450,13900,-0.18),
    ("VAP","Vanguard Aus Property",       "Property",              9700, 8400, 0.95),
]
_RANGES   = [("1M",22),("3M",63),("6M",126),("1Y",252),("ALL",1300)]
_BENCH    = {
    "asx":  {"label":"ASX 200","color":"#8a93a3","seed":99173, "drift":0.00042,"vol":0.012,"shock":-0.055},
    "sp500":{"label":"S&P 500","color":"#4aa8ff","seed":50231, "drift":0.00060,"vol":0.011,"shock":-0.050},
    "ndx":  {"label":"Nasdaq", "color":"#b08bff","seed":70419, "drift":0.00072,"vol":0.016,"shock":-0.070},
}

EXTERNAL_SS = [
    "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;600&display=swap"
]
