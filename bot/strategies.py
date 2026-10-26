"""Strategies return target weights per day, decided with data up to and including that day's close.

The engine shifts weights by one day, so a decision made at Monday's close earns Tuesday's return
(no look-ahead bias).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def _one_hot(choice: pd.Series, symbols: list[str]) -> pd.DataFrame:
    """Turn a Series of chosen symbols (NaN = no decision yet) into a 0/1 weight table."""
    w = pd.DataFrame(0.0, index=choice.index, columns=symbols)
    for s in symbols:
        w.loc[choice == s, s] = 1.0
    w[choice.isna()] = float("nan")
    return w


def buy_hold_spy(closes: pd.DataFrame) -> pd.DataFrame:
    """Benchmark: 100 % S&P 500 ETF, always."""
    return pd.DataFrame({"SPY": 1.0}, index=closes.index)


def trend_qqq(closes: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    """Hold Nasdaq-100 (QQQ) while it closes above its 200-day average, else T-bills (BIL)."""
    sma = closes["QQQ"].rolling(window).mean()
    choice = pd.Series("BIL", index=closes.index, dtype=object)
    choice[closes["QQQ"] > sma] = "QQQ"
    choice[sma.isna()] = None
    return _one_hot(choice, ["QQQ", "BIL"])


def month_end_mask(index: pd.DatetimeIndex) -> pd.Series:
    """True on the last trading day of each month.

    The final row has no "next day" yet, so it counts if the next business day falls in a new month.
    """
    months = pd.Series(index.to_period("M"), index=index)
    mask = months != months.shift(-1)
    if len(index):
        last = index[-1]
        mask.iloc[-1] = (last + pd.offsets.BDay(1)).month != last.month
    return mask


def dual_momentum(closes: pd.DataFrame, lookback: int = TRADING_DAYS_PER_YEAR) -> pd.DataFrame:
    """Global Equities Momentum, rebalanced monthly.

    If US stocks beat T-bills over 12 months, hold whichever of US (SPY) / international (EFA)
    did better; otherwise hold bonds (AGG).
    """
    r = closes[["SPY", "EFA", "BIL"]] / closes[["SPY", "EFA", "BIL"]].shift(lookback) - 1
    choice = pd.Series(None, index=closes.index, dtype=object)
    risk_on = r["SPY"] > r["BIL"]
    best_equity = pd.Series(np.where(r["SPY"] >= r["EFA"], "SPY", "EFA"), index=closes.index)
    decided = month_end_mask(closes.index) & r[["SPY", "EFA", "BIL"]].notna().all(axis=1)
    choice[decided] = best_equity[decided].where(risk_on[decided], "AGG")
    choice = choice.ffill()
    return _one_hot(choice, ["SPY", "EFA", "AGG"])


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    description: str
    fn: Callable[[pd.DataFrame], pd.DataFrame]


STRATEGIES: dict[str, Strategy] = {
    s.key: s for s in [
        Strategy("buy_hold_spy", "Buy & hold S&P 500 (benchmark)",
                 "Always 100 % SPY. Every strategy must beat this to be worth using.", buy_hold_spy),
        Strategy("trend_qqq", "Trend-following Nasdaq-100",
                 "QQQ while above its 200-day average, otherwise T-bills (BIL).", trend_qqq),
        Strategy("dual_momentum", "Dual momentum (monthly)",
                 "Best of SPY/EFA by 12-month return if stocks beat T-bills, else bonds (AGG).",
                 dual_momentum),
    ]
}

SYMBOLS = ["SPY", "QQQ", "EFA", "AGG", "BIL"]
