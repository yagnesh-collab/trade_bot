"""Simulation engine and performance metrics. Pure functions, no I/O."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategies import TRADING_DAYS_PER_YEAR


@dataclass
class Result:
    equity: pd.Series      # portfolio value per day
    returns: pd.Series     # daily returns after costs
    holdings: pd.DataFrame  # weights actually held during each day
    targets: pd.DataFrame  # weights decided at each day's close (for tomorrow)
    switches: pd.Series    # True on days the portfolio changed


def simulate(closes: pd.DataFrame, targets: pd.DataFrame, start: str | pd.Timestamp,
             capital: float, cost: float) -> Result:
    """Run a strategy from `start` (first day the portfolio is invested is the next trading day)."""
    targets = targets.reindex(closes.index)
    asset_ret = closes[targets.columns].pct_change()
    held = targets.shift(1)  # decided at yesterday's close, held today

    period = closes.index[closes.index >= pd.Timestamp(start)]
    if len(period) == 0:
        raise ValueError("No data on/after start date")
    held = held.loc[period].copy()
    held.iloc[0] = 0.0  # start day: still in cash, buy at its close
    if held.iloc[1:].isna().any().any():
        raise ValueError("Strategy has no signal for part of the period (not enough history)")

    prev = held.shift(1).fillna(0.0)
    turnover = (held - prev).abs().sum(axis=1)
    rets = (held * asset_ret.loc[period]).sum(axis=1) - cost * turnover
    rets.iloc[0] = 0.0
    equity = capital * (1 + rets).cumprod()
    return Result(equity, rets, held, targets.loc[period], turnover > 1e-9)


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min()) if len(equity) else 0.0


def monthly_returns(equity: pd.Series) -> pd.Series:
    """Return per calendar month (the first month is measured from the start value)."""
    month_end = equity.groupby(equity.index.to_period("M")).last()
    prev = month_end.shift(1)
    if len(prev):
        prev.iloc[0] = equity.iloc[0]
    return month_end / prev - 1


def metrics(res: Result) -> dict:
    eq, r = res.equity, res.returns
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    vol = r.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    m = monthly_returns(eq)
    return {
        "final_value": float(eq.iloc[-1]),
        "total_return": float(total),
        "cagr": float((1 + total) ** (1 / years) - 1) if years >= 1 else float("nan"),
        "volatility": float(vol),
        "sharpe": float(r.mean() / r.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) if r.std() > 0 else float("nan"),
        "max_drawdown": max_drawdown(eq),
        "positive_months": float((m > 0).mean()) if len(m) else float("nan"),
        "worst_month": float(m.min()) if len(m) else float("nan"),
        "best_month": float(m.max()) if len(m) else float("nan"),
        "trades": int(res.switches.sum()),
    }


def hindsight_best_trade(prices: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    """Best single buy-then-sell return in hindsight. One pass, O(n) time, O(1) space."""
    best, buy_date, sell_date = 0.0, None, None
    low_price, low_date = float("inf"), None
    for date, price in prices.items():
        if price < low_price:
            low_price, low_date = price, date
        elif price / low_price - 1 > best:
            best, buy_date, sell_date = price / low_price - 1, low_date, date
    return best, buy_date, sell_date
