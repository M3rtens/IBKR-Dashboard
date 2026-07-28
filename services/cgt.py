"""Australian CGT engine: FIFO lot matching over IBKR Flex trades.

Rebuilds every disposal at the parcel level so the report follows the ATO
cost-base method rather than approximating it:

- proceeds are converted to AUD at the **disposal-date** FX rate
- each parcel's cost base is converted at its **acquisition-date** FX rate
- commissions are included in the cost base / deducted from proceeds
  (allocated pro-rata by quantity across the parcels a trade touches)
- holding period is measured per parcel, so the 50% CGT discount
  (assets held > 12 months) can be flagged per disposal

IBKR's own ``fifoPnlRealized`` is kept alongside as a cross-check: for a sell
whose lots are all present in the report window, the engine's local-currency
gain reconciles with IBKR's figure (commissions included either way).

Sells that consume lots opened before the Flex Query's window have no
acquisition record — those parcels are flagged ``cost_known = False`` and
fall back to IBKR's FIFO cost converted at the disposal-date rate.
"""

from collections import deque
from dataclasses import dataclass, field

import pandas as pd

# Individual/trust CGT discount for assets held more than 12 months.
CGT_DISCOUNT = 0.50
# Holding must exceed 12 months; bought 1 Jun 2024 is discount-eligible only
# from 2 Jun 2025 (the acquisition and disposal days are both excluded).
def _min_hold_date(buy_date: pd.Timestamp) -> pd.Timestamp:
    return buy_date + pd.DateOffset(years=1) + pd.DateOffset(days=1)


@dataclass
class CGTResult:
    parcels: pd.DataFrame        # one row per (sell trade × buy lot) disposal
    unmatched: pd.DataFrame      # sells with no recorded acquisition
    diagnostics: dict = field(default_factory=dict)


def _trade_order(trades: pd.DataFrame) -> pd.DataFrame:
    """Chronological trade order: execution time, then trade id as a stable
    tie-break for same-second prints."""
    df = trades.copy()
    sort_ts = df["datetime"].fillna(df["date"])
    df["_ts"] = sort_ts
    df["_tid"] = pd.to_numeric(df["trade_id"], errors="coerce")
    return df.sort_values(["_ts", "_tid"], kind="mergesort").reset_index(drop=True)


def match_disposals(trades: pd.DataFrame, start=None, end=None) -> CGTResult:
    """FIFO-match sells against prior buys, per symbol.

    ``start`` / ``end`` (datetime.date) restrict which *disposals* are
    reported; buys of any age still seed the lot book so acquisitions before
    the window price the parcels sold inside it.

    Returns a :class:`CGTResult` whose ``parcels`` DataFrame has one row per
    disposed parcel (see the column list at the bottom of this function).
    """
    if trades.empty:
        return CGTResult(pd.DataFrame(), pd.DataFrame(),
                         {"sell_count": 0, "max_pnl_diff": 0.0})

    df = _trade_order(trades[trades["asset_class"] != "CASH"])

    lots: dict[str, deque] = {}   # symbol -> deque of open buy lots
    parcels, unmatched = [], []
    sell_count, max_pnl_diff = 0, 0.0

    for _, t in df.iterrows():
        sym = t["symbol"]
        qty = float(t["quantity"])
        book = lots.setdefault(sym, deque())

        if qty > 0:  # BUY — open a new lot at the back of the FIFO queue
            book.append({
                "date":  t["date"],
                "qty":   qty,
                "price": float(t["price"]),
                "fx":    float(t["fx_rate"]) or 1.0,
                "comm":  abs(float(t["commission"])),
            })
            continue

        # SELL — consume lots from the front
        sell_qty    = -qty
        sell_price  = float(t["price"])
        sell_fx     = float(t["fx_rate"]) or 1.0
        sell_comm   = abs(float(t["commission"]))
        sell_date   = t["date"]
        in_window   = ((start is None or sell_date.date() >= start) and
                       (end is None or sell_date.date() <= end))
        ibkr_pnl    = float(t["realized_pnl"])
        ibkr_cost   = abs(float(t["cost"]))
        remaining   = sell_qty
        local_gain_sum = 0.0

        while remaining > 1e-9 and book:
            lot = book[0]
            take = min(lot["qty"], remaining)

            # Commissions follow the quantity: the fraction of the buy lot
            # consumed carries the same fraction of the buy commission, and
            # likewise for the sell commission across the parcels it splits into.
            buy_comm  = lot["comm"] * (take / lot["qty"])
            sell_c    = sell_comm * (take / sell_qty)

            cost_local      = take * lot["price"] + buy_comm
            proceeds_local  = take * sell_price - sell_c
            gain_local      = proceeds_local - cost_local
            local_gain_sum += gain_local

            if in_window:
                # ATO cost-base method: each side converted at its own date's
                # rate, commissions included on their respective sides.
                cost_aud     = (take * lot["price"] + buy_comm) * lot["fx"]
                proceeds_aud = (take * sell_price - sell_c) * sell_fx
                gain_aud     = proceeds_aud - cost_aud
                hold_days    = (sell_date - lot["date"]).days
                discount_eligible = (
                    gain_aud > 0 and
                    sell_date >= _min_hold_date(lot["date"])
                )
                parcels.append({
                    "sell_date":    sell_date,
                    "symbol":       sym,
                    "description":  t["description"],
                    "currency":     t["currency"],
                    "qty":          take,
                    "buy_date":     lot["date"],
                    "buy_price":    lot["price"],
                    "sell_price":   sell_price,
                    "buy_fx":       lot["fx"],
                    "sell_fx":      sell_fx,
                    "cost_local":   round(cost_local, 4),
                    "proceeds_local": round(proceeds_local, 4),
                    "gain_local":   round(gain_local, 2),
                    "cost_aud":     round(cost_aud, 2),
                    "proceeds_aud": round(proceeds_aud, 2),
                    "gain_aud":     round(gain_aud, 2),
                    "hold_days":    hold_days,
                    "discount_eligible": bool(discount_eligible),
                    "cost_known":   True,
                })

            lot["qty"]  -= take
            lot["comm"] -= buy_comm
            remaining   -= take
            if lot["qty"] <= 1e-9:
                book.popleft()

        if remaining > 1e-9:
            # No acquisition on record for this quantity — the position
            # predates the Flex Query window (or was transferred in).
            sell_c = sell_comm * (remaining / sell_qty)
            proceeds_local = remaining * sell_price - sell_c
            cost_local = (ibkr_cost * (remaining / sell_qty)) if sell_qty else 0.0
            gain_local = proceeds_local - cost_local
            local_gain_sum += gain_local
            if in_window:
                unmatched.append({
                    "sell_date": sell_date, "symbol": sym,
                    "description": t["description"], "currency": t["currency"],
                    "qty": remaining, "sell_price": sell_price,
                })
                parcels.append({
                    "sell_date":    sell_date,
                    "symbol":       sym,
                    "description":  t["description"],
                    "currency":     t["currency"],
                    "qty":          remaining,
                    "buy_date":     pd.NaT,
                    "buy_price":    (cost_local / remaining) if remaining else 0.0,
                    "sell_price":   sell_price,
                    "buy_fx":       None,
                    "sell_fx":      sell_fx,
                    "cost_local":   round(cost_local, 4),
                    "proceeds_local": round(proceeds_local, 4),
                    "gain_local":   round(gain_local, 2),
                    # Fallback: IBKR's FIFO cost at the disposal-date rate.
                    # Marked cost_known=False so the report can flag it.
                    "cost_aud":     round(cost_local * sell_fx, 2),
                    "proceeds_aud": round(proceeds_local * sell_fx, 2),
                    "gain_aud":     round(gain_local * sell_fx, 2),
                    "hold_days":    None,
                    "discount_eligible": False,
                    "cost_known":   False,
                })

        sell_count += 1
        if in_window:
            max_pnl_diff = max(max_pnl_diff, abs(local_gain_sum - ibkr_pnl))

    diagnostics = {
        "sell_count":     sell_count,
        "max_pnl_diff":   round(max_pnl_diff, 6),
        "unmatched_qty":  float(sum(r["qty"] for r in unmatched)),
        "unmatched_syms": sorted({r["symbol"] for r in unmatched}),
    }
    return CGTResult(pd.DataFrame(parcels), pd.DataFrame(unmatched),
                     diagnostics)


def summarise(parcels: pd.DataFrame, discount: float = CGT_DISCOUNT) -> dict:
    """Headline figures for the report, incl. the discount-adjusted estimate.

    Loss-offset ordering follows the most tax-effective reading of the ATO
    rules: current-year losses offset non-discount gains first, then
    discount-eligible gains, and the 50% discount applies to what remains.
    """
    if parcels.empty:
        return dict(gains=0.0, losses=0.0, net=0.0, eligible_gains=0.0,
                    plain_gains=0.0, est_taxable=0.0, disposals=0,
                    unknown_cost=0)
    g = parcels["gain_aud"].astype(float)
    eligible = parcels["discount_eligible"].fillna(False).astype(bool)
    gains   = float(g[g > 0].sum())
    losses  = float(g[g < 0].sum())          # negative
    elig    = float(g[(g > 0) & eligible].sum())
    plain   = float(g[(g > 0) & ~eligible].sum())

    rem = -losses                            # losses left to offset
    offset = min(plain, rem)
    plain -= offset
    rem   -= offset
    elig   = max(0.0, elig - rem)

    return dict(
        gains=gains, losses=losses, net=gains + losses,
        eligible_gains=elig, plain_gains=plain,
        est_taxable=plain + elig * (1 - discount),
        disposals=len(parcels),
        unknown_cost=int((~parcels["cost_known"]).sum())
        if "cost_known" in parcels else 0,
    )
