"""
Portfolio optimization methods.
All functions accept:
  - returns_df: pd.DataFrame of daily returns (columns = tickers)
  - current_weights: dict {ticker: weight}
  - risk_free_rate: float (annualised, default 0.04)
  - **method-specific kwargs
Returns:
  - dict {ticker: recommended_weight}
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def mean_variance(returns_df: pd.DataFrame, target_return: float = None,
                  risk_aversion: float = 1.0, **kw) -> dict:
    """Classic Markowitz mean-variance optimization."""
    tickers = list(returns_df.columns)
    n = len(tickers)
    mu = returns_df.mean().values * 252
    cov = returns_df.cov().values * 252

    def _port_ret(w):
        return w @ mu

    def _port_var(w):
        return w @ cov @ w

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    if target_return is not None:
        constraints.append({"type": "eq", "fun": lambda w: _port_ret(w) - target_return})
        result = minimize(_port_var, x0=np.ones(n) / n, method="SLSQP",
                          bounds=bounds, constraints=constraints)
    else:
        def _neg_utility(w):
            return -(w @ mu - 0.5 * risk_aversion * (w @ cov @ w))
        result = minimize(_neg_utility, x0=np.ones(n) / n, method="SLSQP",
                          bounds=bounds, constraints=constraints)

    if result.success:
        weights = np.maximum(result.x, 0)
        weights = weights / weights.sum()
    else:
        weights = np.ones(n) / n

    return {t: round(float(w), 6) for t, w in zip(tickers, weights)}


def min_variance(returns_df: pd.DataFrame, **kw) -> dict:
    """Global minimum variance portfolio."""
    return mean_variance(returns_df, target_return=None, risk_aversion=100.0, **kw)


def max_sharpe(returns_df: pd.DataFrame, risk_free_rate: float = 0.04, **kw) -> dict:
    """Maximum Sharpe ratio portfolio."""
    tickers = list(returns_df.columns)
    n = len(tickers)
    mu = returns_df.mean().values * 252
    cov = returns_df.cov().values * 252
    rf = risk_free_rate

    def _neg_sharpe(w):
        ret = w @ mu
        vol = np.sqrt(w @ cov @ w)
        if vol < 1e-10:
            return 0
        return -(ret - rf) / vol

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    result = minimize(_neg_sharpe, x0=np.ones(n) / n, method="SLSQP",
                      bounds=bounds, constraints=constraints)

    if result.success:
        weights = np.maximum(result.x, 0)
        weights = weights / weights.sum()
    else:
        weights = np.ones(n) / n

    return {t: round(float(w), 6) for t, w in zip(tickers, weights)}


def risk_parity(returns_df: pd.DataFrame, **kw) -> dict:
    """Risk parity — each asset contributes equally to total portfolio risk."""
    tickers = list(returns_df.columns)
    n = len(tickers)
    cov = returns_df.cov().values * 252

    def _risk_budget_obj(w):
        vol = np.sqrt(w @ cov @ w)
        if vol < 1e-10:
            return 0
        mrc = cov @ w
        rc = w * mrc / vol
        target = vol / n
        return np.sum((rc - target) ** 2)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0.001, 1)] * n
    result = minimize(_risk_budget_obj, x0=np.ones(n) / n, method="SLSQP",
                      bounds=bounds, constraints=constraints)

    if result.success:
        weights = np.maximum(result.x, 0)
        weights = weights / weights.sum()
    else:
        weights = np.ones(n) / n

    return {t: round(float(w), 6) for t, w in zip(tickers, weights)}


def hierarchical_risk_parity(returns_df: pd.DataFrame, **kw) -> dict:
    """Hierarchical Risk Parity (Lopez de Prado, 2016)."""
    from scipy.cluster.hierarchy import linkage, leaves_list
    from scipy.spatial.distance import squareform

    tickers = list(returns_df.columns)
    n = len(tickers)
    cov = returns_df.cov().values * 252

    # Distance matrix from correlation
    corr = returns_df.corr().values
    dist = np.sqrt(0.5 * (1 - corr))
    np.fill_diagonal(dist, 0)
    dist = np.clip(dist, 0, None)

    # Handle NaN/Inf in distance matrix
    if not np.all(np.isfinite(dist)):
        return {t: round(1.0 / n, 6) for t in tickers}

    # Condensed distance and hierarchical clustering
    try:
        condensed = squareform(dist, checks=False)
        link = linkage(condensed, method="single")
        sorted_idx = leaves_list(link).tolist()
    except Exception:
        sorted_idx = list(range(n))

    # Recursive bisection
    def _w_alloc(items):
        if len(items) == 1:
            return {items[0]: 1.0}

        mid = len(items) // 2
        left = items[:mid]
        right = items[mid:]

        left_var = _cluster_var(left)
        right_var = _cluster_var(right)
        total_var = left_var + right_var

        if total_var < 1e-10:
            alpha = 0.5
        else:
            alpha = 1 - left_var / total_var

        result = {}
        for t, w in _w_alloc(left).items():
            result[t] = w * alpha
        for t, w in _w_alloc(right).items():
            result[t] = w * (1 - alpha)
        return result

    def _cluster_var(items):
        idx = [tickers.index(t) for t in items]
        sub_cov = cov[np.ix_(idx, idx)]
        w = np.ones(len(idx)) / len(idx)
        return float(w @ sub_cov @ w)

    sorted_tickers = [tickers[i] for i in sorted_idx]
    weights = _w_alloc(sorted_tickers)

    return {t: round(float(weights.get(t, 0)), 6) for t in tickers}


METHODS = {
    "mean_variance":  ("Mean-Variance", mean_variance),
    "max_sharpe":     ("Max Sharpe Ratio", max_sharpe),
    "min_variance":   ("Minimum Variance", min_variance),
    "risk_parity":    ("Risk Parity", risk_parity),
    "hrp":            ("Hierarchical Risk Parity", hierarchical_risk_parity),
}


def run_optimization(method_key: str, returns_df: pd.DataFrame,
                     risk_free_rate: float = 0.04, **kwargs) -> dict:
    """Run the selected optimization method and return weights dict."""
    if method_key not in METHODS:
        raise ValueError(f"Unknown method: {method_key}")
    _, func = METHODS[method_key]
    return func(returns_df, risk_free_rate=risk_free_rate, **kwargs)
