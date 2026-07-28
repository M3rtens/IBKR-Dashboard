"""Tests for the CGT FIFO lot-matching engine (services/cgt.py)."""

import datetime as dt

import pandas as pd
import pytest

from services import cgt


def _trade(date, sym, qty, price, comm=0.0, fx=1.5, pnl=0.0, cost=0.0,
           tid="1"):
    """Build one trade row in the shape the Flex parser produces."""
    return {
        "date": pd.Timestamp(date), "datetime": pd.Timestamp(date),
        "symbol": sym, "description": f"{sym} Inc", "asset_class": "STK",
        "currency": "USD", "action": "BUY" if qty > 0 else "SELL",
        "quantity": qty, "price": price, "proceeds": -qty * price,
        "commission": -abs(comm), "realized_pnl": pnl, "fx_rate": fx,
        "cost": cost, "open_close": "O" if qty > 0 else "C", "trade_id": tid,
    }


def _df(rows):
    return pd.DataFrame(rows)


# --- Basic matching + ATO FX conversion ------------------------------------

def test_single_parcel_ato_fx_method():
    """Proceeds convert at the disposal rate, cost base at the acquisition
    rate; commissions included on their own sides."""
    trades = _df([
        _trade("2025-01-10", "ABC", 10, 100, comm=1, fx=1.50),
        _trade("2025-02-10", "ABC", -10, 120, comm=1, fx=1.20,
               pnl=10 * 120 - 10 * 100 - 2, cost=-(10 * 100 + 1)),
    ])
    res = cgt.match_disposals(trades)
    assert len(res.parcels) == 1
    p = res.parcels.iloc[0]
    # local: proceeds 1200-1, cost 1000+1 → gain 198
    assert p["gain_local"] == pytest.approx(198.0)
    # AUD: proceeds (1200-1)*1.20, cost (1000+1)*1.50
    assert p["proceeds_aud"] == pytest.approx(1199 * 1.20)
    assert p["cost_aud"] == pytest.approx(1001 * 1.50)
    assert p["gain_aud"] == pytest.approx(1199 * 1.20 - 1001 * 1.50)
    assert p["cost_known"]
    assert not p["discount_eligible"]  # held 1 month
    assert res.unmatched.empty
    assert res.diagnostics["max_pnl_diff"] == pytest.approx(0.0)


def test_fifo_across_lots_with_prorata_commissions():
    """A 15-share sell consumes the 10-share lot fully and 5 of the next;
    buy commissions split pro-rata by quantity."""
    trades = _df([
        _trade("2025-01-05", "XYZ", 10, 100, comm=2, tid="1"),   # lot A
        _trade("2025-01-06", "XYZ", 10, 110, comm=2, tid="2"),   # lot B
        _trade("2025-03-01", "XYZ", -15, 130, comm=3, tid="3",
               pnl=15 * 130 - (10 * 100 + 5 * 110) - 6,
               cost=-(10 * 100 + 5 * 110 + 3)),
    ])
    res = cgt.match_disposals(trades)
    assert len(res.parcels) == 2
    a, b = res.parcels.iloc[0], res.parcels.iloc[1]
    assert a["qty"] == 10 and a["buy_price"] == 100     # FIFO: lot A first
    assert b["qty"] == 5 and b["buy_price"] == 110
    # lot A carries its full $2 commission; lot B half of its $2
    assert a["cost_local"] == pytest.approx(10 * 100 + 2)
    assert b["cost_local"] == pytest.approx(5 * 110 + 1)
    # sell commission splits 2/3 and 1/3 across the parcels
    assert a["proceeds_local"] == pytest.approx(10 * 130 - 2)
    assert b["proceeds_local"] == pytest.approx(5 * 130 - 1)
    # engine reconciles with IBKR's fifo figure
    assert res.parcels["gain_local"].sum() == pytest.approx(
        15 * 130 - (10 * 100 + 5 * 110) - 6, abs=0.01)


def test_discount_eligibility_rules():
    """>12 months + gain → eligible; loss or short hold → not."""
    trades = _df([
        _trade("2024-01-10", "OLD", 1, 100, tid="1"),
        _trade("2026-02-01", "OLD", -1, 150, tid="2", cost=-100),  # gain, >12m
        _trade("2024-01-10", "LOS", 1, 100, tid="3"),
        _trade("2026-02-01", "LOS", -1, 50, tid="4", cost=-100),   # loss, >12m
        _trade("2025-06-01", "NEW", 1, 100, tid="5"),
        _trade("2026-02-01", "NEW", -1, 150, tid="6", cost=-100),  # gain, <12m
    ])
    res = cgt.match_disposals(trades)
    by_sym = res.parcels.set_index("symbol")
    assert by_sym.loc["OLD", "discount_eligible"]
    assert not by_sym.loc["LOS", "discount_eligible"]  # losses never discounted
    assert not by_sym.loc["NEW", "discount_eligible"]  # < 12 months
    assert by_sym.loc["OLD", "hold_days"] > 365


def test_discount_boundary_just_past_12_months():
    """Bought 1 Jun 2024: eligible from 2 Jun 2025, not on 1 Jun 2025."""
    trades = _df([
        _trade("2024-06-01", "BND", 2, 100, tid="1"),
        _trade("2025-06-01", "BND", -1, 150, tid="2", cost=-100),  # exactly 12m
        _trade("2025-06-02", "BND", -1, 150, tid="3", cost=-100),  # 12m + 1d
    ])
    res = cgt.match_disposals(trades)
    assert not res.parcels.iloc[0]["discount_eligible"]
    assert res.parcels.iloc[1]["discount_eligible"]


def test_unmatched_sell_flagged_with_fallback_cost():
    """A sell with no buy on record is flagged cost_known=False and listed
    in the unmatched frame; cost falls back to IBKR's FIFO cost."""
    trades = _df([
        _trade("2025-03-01", "GHO", -5, 120, comm=1, fx=1.4,
               pnl=5 * 120 - 500 - 1, cost=-500),
    ])
    res = cgt.match_disposals(trades)
    assert len(res.unmatched) == 1
    assert res.unmatched.iloc[0]["qty"] == 5
    p = res.parcels.iloc[0]
    assert not p["cost_known"]
    assert not p["discount_eligible"]
    # fallback: IBKR cost converted at the disposal-date rate
    assert p["cost_aud"] == pytest.approx(500 * 1.4)
    assert res.diagnostics["unmatched_syms"] == ["GHO"]


def test_window_filters_disposals_but_buys_still_seed_lots():
    """Buys before the window price parcels sold inside it; sells outside the
    window consume lots without being reported."""
    trades = _df([
        _trade("2025-01-01", "AAA", 2, 100, tid="1"),
        _trade("2025-06-15", "AAA", -1, 130, tid="2", cost=-100),  # before window
        _trade("2025-08-10", "AAA", -1, 140, tid="3", cost=-100),  # inside window
    ])
    res = cgt.match_disposals(trades, start=dt.date(2025, 7, 1),
                              end=dt.date(2026, 6, 30))
    assert len(res.parcels) == 1
    p = res.parcels.iloc[0]
    # second sell consumes the remainder of the same lot (FIFO state kept)
    assert p["buy_date"] == pd.Timestamp("2025-01-01")
    assert p["qty"] == 1
    assert p["sell_date"] == pd.Timestamp("2025-08-10")


def test_cash_trades_ignored():
    trades = _df([
        _trade("2025-01-01", "AUD.USD", 100, 0.7, tid="1"),
        _trade("2025-02-01", "AUD.USD", -100, 0.7, tid="2"),
    ])
    trades["asset_class"] = "CASH"
    res = cgt.match_disposals(trades)
    assert res.parcels.empty
    assert res.diagnostics["sell_count"] == 0


# --- Summary / discount estimate --------------------------------------------

def test_summarise_offsets_losses_before_discount():
    """Losses offset non-discount gains first; 50% discount on what remains
    of the eligible gains."""
    parcels = pd.DataFrame([
        # plain gain 100, eligible gain 200, loss -150
        {"gain_aud": 100.0, "discount_eligible": False, "cost_known": True},
        {"gain_aud": 200.0, "discount_eligible": True,  "cost_known": True},
        {"gain_aud": -150.0, "discount_eligible": False, "cost_known": True},
    ])
    s = cgt.summarise(parcels)
    assert s["gains"] == pytest.approx(300.0)
    assert s["losses"] == pytest.approx(-150.0)
    assert s["net"] == pytest.approx(150.0)
    # 150 loss wipes the 100 plain gain, then 50 of the eligible gain;
    # taxable = 0 plain + 50% of remaining 150 eligible
    assert s["est_taxable"] == pytest.approx(75.0)
    assert s["disposals"] == 3
    assert s["unknown_cost"] == 0


def test_summarise_empty():
    s = cgt.summarise(pd.DataFrame())
    assert s["net"] == 0.0 and s["disposals"] == 0
