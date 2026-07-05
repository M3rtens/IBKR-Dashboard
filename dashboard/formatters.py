"""Number and value formatting helpers."""

import math

from dashboard.theme import ACCENT, RED, T2


def money(n, signed=False):
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "$0"
    # Signed values are performance figures (P&L, gains, day change) — show to
    # 1 dp. Unsigned values are levels (portfolio / holding totals) — keep whole
    # dollars.
    if signed:
        s = f"${abs(n):,.1f}"
        return f"-{s}" if n < 0 else (f"+{s}" if n > 0 else s)
    s = f"${abs(int(round(n))):,}"
    return f"-{s}" if n < 0 else s

# Currency code → display symbol. Falls back to the upper-cased code + space
# (e.g. an unmapped "MXN" renders as "MXN 123.45").
_CCY_SYMBOL = {
    "USD": "$",  "AUD": "A$", "NZD": "NZ$", "CAD": "C$", "HKD": "HK$",
    "SGD": "S$", "EUR": "€",  "GBP": "£",   "GBp": "p",  "JPY": "¥",
    "CNY": "¥",  "CHF": "CHF ", "KRW": "₩", "INR": "₹",  "TWD": "NT$",
    "SEK": "kr ", "NOK": "kr ", "DKK": "kr ", "ZAR": "R ", "BRL": "R$",
}


def ccy_symbol(code) -> str:
    """Map a currency code (e.g. 'AUD', 'JPY') to its display symbol."""
    if not code:
        return "$"
    return _CCY_SYMBOL.get(code, _CCY_SYMBOL.get(str(code).upper(),
                                                 str(code).upper() + " "))


def price_money(n, sym="$"):
    """Format a security's per-share price with 2 decimals, e.g. $123.45.

    ``sym`` is the currency symbol (see :func:`ccy_symbol`); defaults to '$'.
    """
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return f"{sym}0.00"
    sign = "-" if n < 0 else ""
    return f"{sign}{sym}{abs(n):,.2f}"

def pct_fmt(n, signed=False, dp=1):
    sign = ("+" if n>0 else ("-" if n<0 else "")) if signed else ("-" if n<0 else "")
    return f"{sign}{abs(n):.{dp}f}%"

def pnl_c(n):
    if n > 0.001: return ACCENT
    if n < -0.001: return RED
    return T2
