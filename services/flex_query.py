"""
IBKR Activity Flex Query client.

Fetches historical trades, cash transactions, dividends, and open positions
from the IBKR Flex Query reporting API, then parses the XML into DataFrames
ready for the dashboard.

Usage (standalone test):
    set IBKR_FLEX_TOKEN=your_token
    set IBKR_FLEX_QUERY_ID=your_query_id
    python -m services.flex_query

Environment variables:
    IBKR_FLEX_TOKEN     — Flex token from Account Management > Reports > Flex Queries > Manage tokens
    IBKR_FLEX_QUERY_ID  — Numeric ID of the Activity Flex Query you created

Flex Query API flow:
    1. POST SendRequest with token + query_id → receive reference code
    2. Poll GetStatement with reference code → receive XML once ready (usually 5-30s)
"""

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

# ─── Load .env file manually ──────────────────────────────────────────
if os.path.exists(".env"):
    with open(".env", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

import pandas as pd
import requests


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

_SEND_REQUEST_URL = (
    "https://gdcdyn.interactivebrokers.com"
    "/Universal/servlet/FlexStatementService.SendRequest"
)
_GET_STATEMENT_URL = (
    "https://gdcdyn.interactivebrokers.com"
    "/Universal/servlet/FlexStatementService.GetStatement"
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class FlexConfig:
    token: str
    query_id: str
    max_retries: int = 5        # number of polling attempts before giving up
    retry_delay: float = 3.0    # initial seconds between polling attempts
    max_retry_delay: float = 30.0  # maximum backoff delay in seconds
    timeout: int = 30           # HTTP request timeout in seconds


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class FlexQueryClient:
    def __init__(self, config: FlexConfig):
        self.config = config
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "Python-FlexQuery/1.0"

    def _send_request(self) -> str:
        """Trigger report generation; returns the reference code."""
        resp = self._session.get(
            _SEND_REQUEST_URL,
            params={"t": self.config.token, "q": self.config.query_id, "v": "3"},
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        status = root.findtext("Status", "")
        if status == "Fail":
            code = root.findtext("ErrorCode", "")
            msg = root.findtext("ErrorMessage", "Unknown error")
            raise RuntimeError(f"Flex SendRequest failed [{code}]: {msg}")

        ref_code = root.findtext("ReferenceCode")
        if not ref_code:
            raise RuntimeError(f"No ReferenceCode in response:\n{resp.text[:500]}")
        return ref_code

    def _get_statement(self, ref_code: str) -> str:
        """Poll until the statement is ready; returns raw XML string."""
        params = {"q": ref_code, "t": self.config.token, "v": "3"}
        delay = self.config.retry_delay

        for attempt in range(1, self.config.max_retries + 1):
            resp = self._session.get(
                _GET_STATEMENT_URL, params=params, timeout=self.config.timeout
            )
            resp.raise_for_status()
            text = resp.text.strip()

            # Parse to detect status vs actual statement
            root = ET.fromstring(text)
            status = root.findtext("Status", "")

            if status == "Warn":
                code = root.findtext("ErrorCode", "")
                msg = root.findtext("ErrorMessage", "")
                # Code 1019 = still generating; keep polling
                if code == "1019" or "try again" in msg.lower():
                    print(f"  Report not ready (attempt {attempt}/{self.config.max_retries}) — retrying in {delay:.0f}s …")
                    time.sleep(delay)
                    delay = min(delay * 2, self.config.max_retry_delay)
                    continue
                raise RuntimeError(f"Flex GetStatement error [{code}]: {msg}")

            if status == "Fail":
                code = root.findtext("ErrorCode", "")
                msg = root.findtext("ErrorMessage", "Unknown error")
                # 1025 = too many failed attempts; back off longer
                if code == "1025":
                    print(f"  Rate limited (attempt {attempt}/{self.config.max_retries}) — waiting {delay:.0f}s …")
                    time.sleep(delay)
                    delay = min(delay * 2, self.config.max_retry_delay)
                    continue
                raise RuntimeError(f"Flex GetStatement failed [{code}]: {msg}")

            # No error status → this is the actual FlexQueryResponse data
            return text

        raise TimeoutError(
            f"Statement not ready after {self.config.max_retries} attempts"
        )

    def fetch(self) -> str:
        """End-to-end fetch: trigger + poll. Returns raw XML."""
        print("Requesting Flex Query report …")
        ref_code = self._send_request()
        print(f"Reference code: {ref_code}  (polling for result)")
        # Short initial pause — IBKR usually takes a few seconds to compile
        time.sleep(3)
        return self._get_statement(ref_code)


# ---------------------------------------------------------------------------
# XML parsers
# ---------------------------------------------------------------------------

def _float(value: Optional[str]) -> float:
    """Convert a string to float, returning 0.0 on failure."""
    try:
        return float(value) if value else 0.0
    except (ValueError, TypeError):
        return 0.0


def _parse_dt(value: Optional[str]) -> "pd.Timestamp":
    """Parse IBKR dateTime strings (YYYYMMDD;HHMMSS or DD/MM/YYYY;HHMMSS)."""
    if not value:
        return pd.NaT
    try:
        return pd.to_datetime(value.replace(";", " "), dayfirst=True, errors="coerce")
    except Exception:
        return pd.NaT


def _parse_trades(root: ET.Element) -> pd.DataFrame:
    rows = []
    for trade in root.iter("Trade"):
        a = trade.attrib
        # Skip summary/subtotal rows (no tradeDate)
        if not a.get("tradeDate"):
            continue
        rows.append({
            "date":           a.get("tradeDate"),
            "datetime":       a.get("dateTime"),
            "account_id":     a.get("accountId"),
            "symbol":         a.get("symbol"),
            "description":    a.get("description"),
            "asset_class":    a.get("assetCategory"),
            "exchange":       a.get("exchange"),
            "currency":       a.get("currency"),
            "action":         a.get("buySell"),          # BUY / SELL
            "quantity":       _float(a.get("quantity")),
            "price":          _float(a.get("tradePrice")),
            "proceeds":       _float(a.get("proceeds")),
            "commission":     _float(a.get("ibCommission")),
            "net_cash":       _float(a.get("netCash")),
            "realized_pnl":   _float(a.get("fifoPnlRealized")),
            "mtm_pnl":        _float(a.get("mtmPnl")),
            "fx_rate":        _float(a.get("fxRateToBase")),
            "cost":           _float(a.get("cost")),
            "open_close":     a.get("openCloseIndicator", ""),
            "trade_id":       a.get("tradeID"),
            "order_id":       a.get("orderId"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
        df["datetime"] = df["datetime"].apply(_parse_dt)
        df = df.sort_values("date", ascending=False).reset_index(drop=True)
    return df


def _parse_cash_transactions(root: ET.Element) -> pd.DataFrame:
    rows = []
    for txn in root.iter("CashTransaction"):
        a = txn.attrib
        if not a.get("dateTime"):
            continue
        rows.append({
            "datetime":    a.get("dateTime"),
            "account_id":  a.get("accountId"),
            "symbol":      a.get("symbol"),
            "description": a.get("description"),
            "type":        a.get("type"),   # Dividends | Deposits/Withdrawals | Broker Interest | etc.
            "amount":      _float(a.get("amount")),
            "currency":    a.get("currency"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["datetime"] = df["datetime"].apply(_parse_dt)
        df = df.sort_values("datetime", ascending=False).reset_index(drop=True)
    return df


def _parse_open_positions(root: ET.Element) -> pd.DataFrame:
    rows = []
    for pos in root.iter("OpenPosition"):
        a = pos.attrib
        if not a.get("symbol"):
            continue
        rows.append({
            "report_date":   a.get("reportDate"),
            "account_id":    a.get("accountId"),
            "symbol":        a.get("symbol"),
            "description":   a.get("description"),
            "asset_class":   a.get("assetCategory"),
            "currency":      a.get("currency"),
            "quantity":      _float(a.get("position")),
            "avg_cost":      _float(a.get("costBasisPrice")),
            "cost_basis":    _float(a.get("costBasisMoney")),
            "close_price":   _float(a.get("markPrice")),
            "market_value":  _float(a.get("positionValue")),
            "unrealized_pnl": _float(a.get("unrealizedPnl")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["report_date"] = pd.to_datetime(df["report_date"], dayfirst=True, errors="coerce")
    return df


def _parse_account_info(root: ET.Element) -> dict:
    stmt = root.find(".//FlexStatement")
    if stmt is None:
        return {}
    return {
        "account_id":   stmt.get("accountId"),
        "from_date":    stmt.get("fromDate"),
        "to_date":      stmt.get("toDate"),
        "period":       stmt.get("period"),
        "generated_at": stmt.get("whenGenerated"),
    }


def _parse_equity_summary(root: ET.Element) -> pd.DataFrame:
    """Parse EquitySummaryByReportDate or EquitySummaryByReportDateInBase — daily net liquidation values.

    This section must be enabled in the Flex Query configuration under
    'Equity Summary by Report Date' (either regular or In Base Currency).
    """
    rows = []
    # Check both tag variations used by IBKR Flex Query
    for tag in ("EquitySummaryByReportDate", "EquitySummaryByReportDateInBase"):
        for el in root.iter(tag):
            a = el.attrib
            date_str = a.get("reportDate")
            total = a.get("total")
            if not date_str or not total:
                continue
            rows.append({
                "date":  date_str,
                "value": _float(total),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        # Try both formats (%Y-%m-%d and %d/%m/%Y)
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce").dt.date
        df = df.dropna(subset=["date"])
        df = df[df["value"] > 0].sort_values("date").reset_index(drop=True)
    return df



# ---------------------------------------------------------------------------
# Structured result
# ---------------------------------------------------------------------------

@dataclass
class FlexData:
    account_info: dict
    trades: pd.DataFrame
    cash_transactions: pd.DataFrame
    open_positions: pd.DataFrame
    equity_summary: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def dividends(self) -> pd.DataFrame:
        """Cash transactions that are dividend-type payments."""
        if self.cash_transactions.empty:
            return pd.DataFrame()
        mask = self.cash_transactions["type"].isin(
            ["Dividends", "Payment In Lieu Of Dividends", "Withholding Tax"]
        )
        return self.cash_transactions[mask].reset_index(drop=True)

    @property
    def deposits_withdrawals(self) -> pd.DataFrame:
        if self.cash_transactions.empty:
            return pd.DataFrame()
        mask = self.cash_transactions["type"] == "Deposits/Withdrawals"
        return self.cash_transactions[mask].reset_index(drop=True)

    def summary(self) -> None:
        ai = self.account_info
        print(f"Account : {ai.get('account_id', '—')}")
        print(f"Period  : {ai.get('from_date')} → {ai.get('to_date')}")
        print(f"Trades                : {len(self.trades):>6}")
        print(f"Cash transactions     : {len(self.cash_transactions):>6}")
        print(f"  ↳ Dividends         : {len(self.dividends):>6}")
        print(f"  ↳ Deposits/Wdrl     : {len(self.deposits_withdrawals):>6}")
        print(f"Open positions        : {len(self.open_positions):>6}")
        print(f"Equity summary rows   : {len(self.equity_summary):>6}")
        if not self.trades.empty:
            realized = self.trades["realized_pnl"].sum()
            commission = self.trades["commission"].sum()
            print(f"Realized P&L (trades): ${realized:>10,.2f}")
            print(f"Total commissions    : ${commission:>10,.2f}")


def parse_flex_xml(xml_text: str) -> FlexData:
    root = ET.fromstring(xml_text)
    return FlexData(
        account_info=_parse_account_info(root),
        trades=_parse_trades(root),
        cash_transactions=_parse_cash_transactions(root),
        open_positions=_parse_open_positions(root),
        equity_summary=_parse_equity_summary(root),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_flex_data(
    token: Optional[str] = None,
    query_id: Optional[str] = None,
    cache_path: Optional[str] = "flex_cache.xml",
    max_cache_age_hours: float = 4.0,
) -> FlexData:
    """
    Fetch Flex data from IBKR, with optional file-based caching.

    Args:
        token:               Flex token (falls back to IBKR_FLEX_TOKEN env var).
        query_id:            Flex query ID (falls back to IBKR_FLEX_QUERY_ID env var).
        cache_path:          Path to cache the raw XML. Set to None to disable.
        max_cache_age_hours: Use cached file if younger than this many hours.

    Returns:
        FlexData with .trades, .cash_transactions, .open_positions, .dividends DataFrames.
    """
    token = token or os.environ.get("IBKR_FLEX_TOKEN", "")
    query_id = query_id or os.environ.get("IBKR_FLEX_QUERY_ID", "")

    # Serve from cache if fresh enough
    if cache_path and os.path.exists(cache_path):
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < max_cache_age_hours:
            print(f"Using cached Flex data ({age_hours:.1f}h old): {cache_path}")
            with open(cache_path, encoding="utf-8") as f:
                return parse_flex_xml(f.read())

    if not token:
        raise ValueError(
            "IBKR_FLEX_TOKEN is not set. "
            "Generate one under Account Management > Reports > Flex Queries > Manage tokens."
        )
    if not query_id:
        raise ValueError(
            "IBKR_FLEX_QUERY_ID is not set. "
            "Find the numeric ID in the Flex Query list in Account Management."
        )

    config = FlexConfig(token=token, query_id=query_id)
    client = FlexQueryClient(config)
    xml_text = client.fetch()

    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(xml_text)
        print(f"Cached Flex data to: {cache_path}")

    return parse_flex_xml(xml_text)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("IBKR Flex Query — standalone fetch")
    print("=" * 60)

    data = load_flex_data()
    print()
    data.summary()

    if not data.trades.empty:
        print("\n--- Recent Trades (last 10) ---")
        cols = ["date", "symbol", "action", "quantity", "price", "proceeds", "commission", "realized_pnl"]
        print(data.trades[cols].head(10).to_string(index=False))

    if not data.dividends.empty:
        print("\n--- Dividends (last 10) ---")
        print(data.dividends[["datetime", "symbol", "description", "amount", "currency"]].head(10).to_string(index=False))

    if not data.deposits_withdrawals.empty:
        print("\n--- Deposits / Withdrawals ---")
        print(data.deposits_withdrawals[["datetime", "description", "amount", "currency"]].to_string(index=False))

    if not data.open_positions.empty:
        print("\n--- Open Positions (as of last report date) ---")
        print(data.open_positions[["symbol", "quantity", "avg_cost", "close_price", "market_value", "unrealized_pnl"]].to_string(index=False))
