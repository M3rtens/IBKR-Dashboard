"""Unit tests for services.flex_query XML parsing.

Uses a small synthetic Flex statement mirroring IBKR's real attribute names,
so parsing is exercised without any network call or credentials.
"""

import pandas as pd
import pytest

from services.flex_query import _float, _parse_dt, parse_flex_xml

FLEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="test" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20250101" toDate="20250630">
      <Trades>
        <Trade tradeDate="20250115" dateTime="20250115;103000" accountId="U1234567"
               symbol="AAPL" description="APPLE INC" assetCategory="STK"
               exchange="NASDAQ" currency="USD" buySell="BUY" quantity="10"
               tradePrice="185.50" proceeds="-1855.00" ibCommission="-1.00"
               netCash="-1856.00" fifoPnlRealized="0" mtmPnl="0"
               fxRateToBase="1.52" cost="1856.00" openCloseIndicator="O"
               tradeID="t1" orderId="o1"/>
        <Trade tradeDate="20250320" dateTime="20250320;142000" accountId="U1234567"
               symbol="AAPL" description="APPLE INC" assetCategory="STK"
               exchange="NASDAQ" currency="USD" buySell="SELL" quantity="-10"
               tradePrice="200.00" proceeds="2000.00" ibCommission="-1.00"
               netCash="1999.00" fifoPnlRealized="143.00" mtmPnl="0"
               fxRateToBase="1.55" cost="-1856.00" openCloseIndicator="C"
               tradeID="t2" orderId="o2"/>
        <Trade symbol="SUBTOTAL" quantity="0"/>
      </Trades>
      <CashTransactions>
        <CashTransaction dateTime="20250201;000000" accountId="U1234567"
                         symbol="AAPL" description="AAPL DIVIDEND"
                         type="Dividends" amount="24.00" currency="USD"/>
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


class TestHelpers:
    def test_float_parses(self):
        assert _float("1.5") == 1.5

    def test_float_bad_input_is_zero(self):
        assert _float(None) == 0.0
        assert _float("") == 0.0
        assert _float("abc") == 0.0

    def test_parse_dt_ibkr_format(self):
        ts = _parse_dt("20250115;103000")
        assert ts == pd.Timestamp(2025, 1, 15, 10, 30, 0)

    def test_parse_dt_empty_is_nat(self):
        assert _parse_dt(None) is pd.NaT


@pytest.fixture(scope="module")
def fd():
    return parse_flex_xml(FLEX_XML)


class TestParseFlexXml:

    def test_two_real_trades_parsed(self, fd):
        # The summary row without tradeDate must be skipped.
        assert len(fd.trades) == 2

    def test_trade_fields_typed_and_mapped(self, fd):
        sell = fd.trades[fd.trades["action"] == "SELL"].iloc[0]
        assert sell["symbol"] == "AAPL"
        assert sell["quantity"] == -10.0
        assert sell["price"] == 200.00
        assert sell["realized_pnl"] == 143.00
        assert sell["fx_rate"] == 1.55
        assert sell["asset_class"] == "STK"

    def test_trades_sorted_newest_first(self, fd):
        dates = fd.trades["date"].tolist()
        assert dates == sorted(dates, reverse=True)

    def test_dividend_cash_transaction_parsed(self, fd):
        divs = fd.cash_transactions
        assert len(divs) == 1
        assert divs.iloc[0]["type"] == "Dividends"
        assert divs.iloc[0]["amount"] == 24.00

    def test_aud_gain_conversion_matches_report_logic(self, fd):
        """The CGT page converts realised USD P&L with the per-trade FX rate;
        verify the parsed fields reproduce the expected AUD figure."""
        sell = fd.trades[fd.trades["action"] == "SELL"].iloc[0]
        assert sell["realized_pnl"] * sell["fx_rate"] == pytest.approx(221.65)
