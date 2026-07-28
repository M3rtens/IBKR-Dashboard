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
import tempfile
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
                os.environ.setdefault(_k.strip(), _v.strip())

import pandas as pd
import requests
import threading


# ---------------------------------------------------------------------------
# Fetch coordination
#
# IBKR's Flex Web Service rejects overlapping report requests on the same
# token, and enough failed attempts trip a "1025 Too many failed attempts"
# lockout. The dashboard has several independent callers (history thread,
# CGT page), so all live fetches are serialised through one lock, failures
# impose a cooldown, and a stale cache is served instead of hammering the API.
# ---------------------------------------------------------------------------
_FETCH_LOCK = threading.Lock()
_COOLDOWN_UNTIL = 0.0            # epoch seconds; no live fetch before this
_LOCKOUT_COOLDOWN_SECONDS = 2 * 3600   # after a 1025 lockout
_FAILURE_COOLDOWN_SECONDS = 15 * 60    # after any other fetch failure


def _set_cooldown(seconds: float) -> None:
    global _COOLDOWN_UNTIL
    _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.time() + seconds)


def _safe_exception_summary(exc: Exception, *secrets: str) -> str:
    """Return a concise diagnostic without leaking Flex credentials."""
    detail = " ".join(str(exc).split()) or "no error message"
    for secret in secrets:
        if secret:
            detail = detail.replace(str(secret), "<redacted>")
    return detail[:500]


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

        ref_code = root.findtext("ReferenceCode")
        if ref_code:
            return ref_code

        # No reference code → IBKR returned an error/warning status instead.
        status = root.findtext("Status", "")
        code = root.findtext("ErrorCode", "")
        msg = root.findtext("ErrorMessage", "Unknown error")
        if code == "1025":
            # Lockout: polling it only adds failed attempts and extends it.
            _set_cooldown(_LOCKOUT_COOLDOWN_SECONDS)
        if status in ("Fail", "Warn"):
            raise RuntimeError(f"Flex SendRequest failed [{code}]: {msg}")
        raise RuntimeError(f"No ReferenceCode in response:\n{resp.text[:500]}")

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
                if code == "1025":
                    # Lockout: polling it only adds failed attempts and extends it.
                    _set_cooldown(_LOCKOUT_COOLDOWN_SECONDS)
                    raise RuntimeError(f"Flex GetStatement failed [1025]: {msg}")
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
                if code == "1025":
                    _set_cooldown(_LOCKOUT_COOLDOWN_SECONDS)
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
            "fx_rate":     _float(a.get("fxRateToBase")),
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
        "base_currency": stmt.get("baseCurrency"),
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
    account_info = _parse_account_info(root)
    cash_transactions = _parse_cash_transactions(root)
    if not cash_transactions.empty:
        base_currency = (account_info.get("base_currency") or "").upper()
        currencies = cash_transactions["currency"].fillna("").str.upper()
        fx_rates = cash_transactions["fx_rate"].astype(float)
        if base_currency:
            usable = (currencies == base_currency) | (fx_rates > 0)
        else:
            # Older statements may omit baseCurrency. Unconverted amounts are
            # only safe when the report contains a single transaction currency.
            usable = ((fx_rates > 0) | (currencies.nunique() <= 1))
        cash_transactions["base_amount"] = (
            cash_transactions["amount"]
            * fx_rates.where(fx_rates > 0, 1.0)
        ).where(usable)
    return FlexData(
        account_info=account_info,
        trades=_parse_trades(root),
        cash_transactions=cash_transactions,
        open_positions=_parse_open_positions(root),
        equity_summary=_parse_equity_summary(root),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _load_cache(cache_path: Optional[str], max_age_hours: float) -> Optional["FlexData"]:
    """Return the parsed cache if it exists and is fresh enough, else None."""
    if not cache_path or not os.path.exists(cache_path):
        return None
    age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
    if age_hours >= max_age_hours:
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            parsed = parse_flex_xml(f.read())
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"[WARN] Ignoring unreadable Flex cache {cache_path}: {exc}")
        return None
    print(f"Using cached Flex data ({age_hours:.1f}h old): {cache_path}")
    return parsed


def _load_stale_cache(cache_path: Optional[str], reason: str) -> Optional["FlexData"]:
    """Serve the cache regardless of age after a failed/blocked live fetch."""
    if not cache_path or not os.path.exists(cache_path):
        return None
    age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
    try:
        with open(cache_path, encoding="utf-8") as f:
            parsed = parse_flex_xml(f.read())
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"[WARN] Stale Flex cache is unreadable {cache_path}: {exc}")
        return None
    print(f"[WARN] {reason}; serving stale cache ({age_hours:.1f}h old): {cache_path}")
    return parsed


def load_flex_data(
    token: Optional[str] = None,
    query_id: Optional[str] = None,
    cache_path: Optional[str] = "flex_cache.xml",
    max_cache_age_hours: float = 4.0,
) -> FlexData:
    """
    Fetch Flex data from IBKR, with file-based caching and failure hardening.

    Live fetches are serialised through a process-wide lock — concurrent
    callers wait, then reuse the cache the first fetch wrote — because IBKR
    rejects overlapping report requests and repeated failures trip a "1025
    Too many failed attempts" lockout. After a failure a cooldown is imposed
    (2h for a 1025 lockout, 15min otherwise) and the stale cache is served
    instead of hammering the API.

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

    cached = _load_cache(cache_path, max_cache_age_hours)
    if cached is not None:
        return cached

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

    with _FETCH_LOCK:
        # The previous lock holder may have just refreshed the cache.
        cached = _load_cache(cache_path, max_cache_age_hours)
        if cached is not None:
            return cached

        remaining = _COOLDOWN_UNTIL - time.time()
        if remaining > 0:
            reason = f"Flex fetch cooling down ({remaining / 60:.0f} min left)"
            stale = _load_stale_cache(cache_path, reason)
            if stale is not None:
                return stale
            raise RuntimeError(f"{reason} and no cached data available")

        try:
            config = FlexConfig(token=token, query_id=query_id)
            client = FlexQueryClient(config)
            xml_text = client.fetch()
        except Exception as exc:
            _set_cooldown(_FAILURE_COOLDOWN_SECONDS)
            detail = _safe_exception_summary(exc, token, query_id)
            reason = (
                f"Flex live fetch failed "
                f"({type(exc).__name__}: {detail})"
            )
            stale = _load_stale_cache(cache_path, reason)
            if stale is not None:
                return stale
            raise

        parsed = parse_flex_xml(xml_text)
        if cache_path:
            cache_dir = os.path.dirname(os.path.abspath(cache_path))
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=cache_dir,
                    prefix=".flex-cache-", suffix=".tmp", delete=False
                ) as f:
                    tmp_path = f.name
                    f.write(xml_text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, cache_path)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            print(f"Cached Flex data to: {cache_path}")

        return parsed


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
