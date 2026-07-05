"""Unit tests for services.optimizer.

Tests fall into three groups:
1. Invariants every method must satisfy (weights sum to 1, long-only).
2. Financial correctness on synthetic data with a known answer.
3. API behaviour of run_optimization.
"""

import numpy as np
import pandas as pd
import pytest

from services.optimizer import (
    METHODS,
    hierarchical_risk_parity,
    max_sharpe,
    mean_variance,
    min_variance,
    risk_parity,
    run_optimization,
)

RNG = np.random.default_rng(42)


def make_returns(n_days=504, seed=42):
    """Three synthetic assets: low-vol, mid-vol/high-return, high-vol."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "LOW":  rng.normal(0.0002, 0.005, n_days),   # ~8% ann. vol
        "MID":  rng.normal(0.0006, 0.010, n_days),   # ~16% ann. vol
        "HIGH": rng.normal(0.0004, 0.025, n_days),   # ~40% ann. vol
    })


def make_clustered_returns(n_days=504, seed=7):
    """Four assets where A/B are near-duplicates and C/D are independent —
    the structure HRP is designed to exploit."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0004, 0.01, n_days)
    return pd.DataFrame({
        "A": base + rng.normal(0, 0.001, n_days),
        "B": base + rng.normal(0, 0.001, n_days),
        "C": rng.normal(0.0004, 0.01, n_days),
        "D": rng.normal(0.0004, 0.01, n_days),
    })


ALL_FUNCS = [mean_variance, min_variance, max_sharpe, risk_parity,
             hierarchical_risk_parity]


@pytest.mark.parametrize("func", ALL_FUNCS)
class TestInvariants:
    def test_weights_sum_to_one(self, func):
        w = func(make_returns())
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)

    def test_long_only(self, func):
        w = func(make_returns())
        assert all(v >= 0 for v in w.values())

    def test_covers_all_tickers(self, func):
        df = make_returns()
        w = func(df)
        assert set(w) == set(df.columns)


class TestFinancialCorrectness:
    def test_min_variance_beats_equal_weight_in_sample(self):
        df = make_returns()
        cov = df.cov().values * 252
        w_mv = np.array([min_variance(df)[t] for t in df.columns])
        w_eq = np.ones(len(df.columns)) / len(df.columns)
        assert w_mv @ cov @ w_mv <= w_eq @ cov @ w_eq + 1e-12

    def test_min_variance_prefers_low_vol_asset(self):
        w = min_variance(make_returns())
        assert w["LOW"] > w["HIGH"]

    def test_max_sharpe_beats_equal_weight_sharpe_in_sample(self):
        df = make_returns()
        mu, cov = df.mean().values * 252, df.cov().values * 252
        rf = 0.04

        def sharpe(w):
            return (w @ mu - rf) / np.sqrt(w @ cov @ w)

        w_ms = np.array([max_sharpe(df, risk_free_rate=rf)[t] for t in df.columns])
        w_eq = np.ones(len(df.columns)) / len(df.columns)
        assert sharpe(w_ms) >= sharpe(w_eq) - 1e-9

    def test_risk_parity_equalises_risk_contributions(self):
        df = make_returns()
        cov = df.cov().values * 252
        w = np.array([risk_parity(df)[t] for t in df.columns])
        vol = np.sqrt(w @ cov @ w)
        rc = w * (cov @ w) / vol           # risk contribution per asset
        # Each contribution should be close to vol / n.
        assert np.allclose(rc, vol / len(w), rtol=0.15)

    def test_risk_parity_underweights_high_vol(self):
        w = risk_parity(make_returns())
        assert w["LOW"] > w["MID"] > w["HIGH"]

    def test_hrp_splits_duplicate_cluster(self):
        """A and B are one risk cluster; HRP should give the pair combined
        weight comparable to each independent asset, i.e. each duplicate gets
        less than C or D individually."""
        w = hierarchical_risk_parity(make_clustered_returns())
        assert w["A"] < w["C"]
        assert w["B"] < w["D"]

    def test_hrp_equal_weights_on_degenerate_input(self):
        """Zero-variance input can't be clustered — HRP must fall back to
        equal weight rather than crash."""
        df = pd.DataFrame({"X": [0.0] * 50, "Y": [0.0] * 50})
        w = hierarchical_risk_parity(df)
        assert w["X"] == pytest.approx(0.5, abs=1e-6)
        assert w["Y"] == pytest.approx(0.5, abs=1e-6)


class TestRunOptimization:
    def test_dispatches_every_registered_method(self):
        df = make_returns()
        for key in METHODS:
            w = run_optimization(key, df)
            assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="Unknown method"):
            run_optimization("does_not_exist", make_returns())

    def test_single_asset_gets_full_weight(self):
        df = make_returns()[["LOW"]]
        for key in METHODS:
            w = run_optimization(key, df)
            assert w["LOW"] == pytest.approx(1.0, abs=1e-4)
