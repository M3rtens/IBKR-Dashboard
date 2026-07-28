"""Regression tests for the 2026-07-27 code-review fixes."""

import time
from types import SimpleNamespace

import pandas as pd
import pytest


def test_single_ticker_download_uses_ticker_first_multiindex():
    from services.market_data import _extract_closes

    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Open"),
        ("AAPL", "Close"),
    ])
    data = pd.DataFrame([[99.0, 100.0], [100.0, 102.0]], columns=columns)

    assert _extract_closes(data, "AAPL", 1) == [100.0, 102.0]


def test_empty_history_uses_short_negative_cache(monkeypatch):
    from services import market_data

    key = ("EMPTY", "1mo", "1d")
    calls = []

    class FakeTicker:
        def history(self, **_kwargs):
            calls.append(True)
            return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setitem(
        __import__("sys").modules, "yfinance",
        SimpleNamespace(Ticker=lambda _ticker: FakeTicker()))
    monkeypatch.setitem(
        market_data._hist_cache, key, {"data": pd.DataFrame(), "ts": 100.0})

    monkeypatch.setattr(market_data.time, "time", lambda: 150.0)
    assert market_data._history(*key).empty
    assert calls == []

    monkeypatch.setattr(market_data.time, "time", lambda: 221.0)
    assert not market_data._history(*key).empty
    assert calls == [True]
    market_data._hist_cache.pop(key, None)


def test_synthetic_demo_benchmark_matches_chart_length(monkeypatch):
    from dashboard import charts

    monkeypatch.setattr(charts, "DEMO_MODE", True)
    fig = charts.build_main_chart(
        list(range(100, 110)), range_key="ALL", active_bench=["asx"])

    assert len(fig.data) == 2
    assert len(fig.data[1].x) == 10
    assert len(fig.data[1].y) == 10


def test_solver_failure_is_not_reported_as_equal_weights(monkeypatch):
    from services import optimizer

    returns = pd.DataFrame({"A": [0.01, -0.01], "B": [0.0, 0.01]})
    failed = SimpleNamespace(success=False, message="iteration limit", x=[0.5, 0.5])
    monkeypatch.setattr(optimizer, "minimize", lambda *args, **kwargs: failed)

    with pytest.raises(optimizer.OptimizationError, match="iteration limit"):
        optimizer.max_sharpe(returns)


def test_corrupt_flex_cache_falls_through_and_is_replaced(monkeypatch, tmp_path):
    from services import flex_query

    xml = """<FlexQueryResponse>
      <FlexStatements><FlexStatement accountId="U1" baseCurrency="AUD">
        <CashTransactions>
          <CashTransaction dateTime="20250101;000000" type="Dividends"
            amount="10" currency="USD" fxRateToBase="1.5"/>
        </CashTransactions>
      </FlexStatement></FlexStatements>
    </FlexQueryResponse>"""
    cache = tmp_path / "flex.xml"
    cache.write_text("<truncated", encoding="utf-8")
    monkeypatch.setattr(flex_query, "_COOLDOWN_UNTIL", 0.0)
    monkeypatch.setattr(flex_query.FlexQueryClient, "fetch", lambda _self: xml)

    data = flex_query.load_flex_data(
        token="token", query_id="query", cache_path=str(cache))

    assert data.dividends.iloc[0]["base_amount"] == pytest.approx(15.0)
    assert cache.read_text(encoding="utf-8") == xml
    assert list(tmp_path.glob(".flex-cache-*.tmp")) == []


def test_flex_cooldown_serves_stale_cache_without_fetching(monkeypatch, tmp_path):
    from services import flex_query

    xml = """<FlexQueryResponse>
      <FlexStatements><FlexStatement accountId="U1"/></FlexStatements>
    </FlexQueryResponse>"""
    cache = tmp_path / "flex.xml"
    cache.write_text(xml, encoding="utf-8")
    monkeypatch.setattr(flex_query, "_COOLDOWN_UNTIL", time.time() + 300)

    def should_not_fetch(_self):
        raise AssertionError("live fetch ran during cooldown")

    monkeypatch.setattr(flex_query.FlexQueryClient, "fetch", should_not_fetch)
    data = flex_query.load_flex_data(
        token="token", query_id="query", cache_path=str(cache),
        max_cache_age_hours=0)

    assert data.account_info["account_id"] == "U1"


def test_flex_failure_prints_sanitized_reason(monkeypatch, tmp_path, capsys):
    from services import flex_query

    xml = """<FlexQueryResponse>
      <FlexStatements><FlexStatement accountId="U1"/></FlexStatements>
    </FlexQueryResponse>"""
    cache = tmp_path / "flex.xml"
    cache.write_text(xml, encoding="utf-8")
    monkeypatch.setattr(flex_query, "_COOLDOWN_UNTIL", 0.0)

    def failed_fetch(_self):
        raise RuntimeError(
            "IBKR rejected token=secret-token query=secret-query code=1025")

    monkeypatch.setattr(flex_query.FlexQueryClient, "fetch", failed_fetch)
    data = flex_query.load_flex_data(
        token="secret-token", query_id="secret-query",
        cache_path=str(cache), max_cache_age_hours=0)

    output = capsys.readouterr().out
    assert data.account_info["account_id"] == "U1"
    assert "Flex live fetch failed (RuntimeError:" in output
    assert "code=1025" in output
    assert "secret-token" not in output
    assert "secret-query" not in output
    assert output.count("<redacted>") == 2
