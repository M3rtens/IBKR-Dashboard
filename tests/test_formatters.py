"""Unit tests for dashboard.formatters — pure display-formatting helpers."""

import math

import pytest

from dashboard.formatters import ccy_symbol, money, pct_fmt, price_money


class TestMoney:
    def test_whole_dollars_unsigned(self):
        assert money(1234567.89) == "$1,234,568"

    def test_negative_unsigned(self):
        assert money(-2500) == "-$2,500"

    def test_zero(self):
        assert money(0) == "$0"

    def test_none_is_zero(self):
        assert money(None) == "$0"

    def test_nan_is_zero(self):
        assert money(float("nan")) == "$0"

    def test_signed_positive_gets_plus_and_1dp(self):
        assert money(1234.56, signed=True) == "+$1,234.6"

    def test_signed_negative(self):
        assert money(-1234.56, signed=True) == "-$1,234.6"

    def test_signed_zero_has_no_sign(self):
        assert money(0, signed=True) == "$0.0"


class TestCcySymbol:
    @pytest.mark.parametrize(
        "code,symbol",
        [("USD", "$"), ("AUD", "A$"), ("EUR", "\u20ac"), ("GBP", "\u00a3"),
         ("JPY", "\u00a5"), ("KRW", "\u20a9")],
    )
    def test_known_codes(self, code, symbol):
        assert ccy_symbol(code) == symbol

    def test_lowercase_code_maps_via_upper(self):
        assert ccy_symbol("aud") == "A$"

    def test_gbp_pence_is_case_sensitive(self):
        assert ccy_symbol("GBp") == "p"

    def test_unknown_code_falls_back_to_code_plus_space(self):
        assert ccy_symbol("MXN") == "MXN "

    def test_empty_defaults_to_dollar(self):
        assert ccy_symbol("") == "$"
        assert ccy_symbol(None) == "$"


class TestPriceMoney:
    def test_two_decimals(self):
        assert price_money(123.456) == "$123.46"

    def test_thousands_separator(self):
        assert price_money(1234.5) == "$1,234.50"

    def test_negative_sign_before_symbol(self):
        assert price_money(-9.99) == "-$9.99"

    def test_custom_symbol(self):
        assert price_money(100, sym="\u00a5") == "\u00a5100.00"

    def test_none_and_nan(self):
        assert price_money(None) == "$0.00"
        assert price_money(float("nan")) == "$0.00"


class TestPctFmt:
    def test_unsigned_positive(self):
        assert pct_fmt(3.14159) == "3.1%"

    def test_unsigned_negative_keeps_minus(self):
        assert pct_fmt(-2.5) == "-2.5%"

    def test_signed_positive_gets_plus(self):
        assert pct_fmt(2.5, signed=True) == "+2.5%"

    def test_signed_zero_no_sign(self):
        assert pct_fmt(0, signed=True) == "0.0%"

    def test_custom_dp(self):
        assert pct_fmt(1.2345, dp=2) == "1.23%"
