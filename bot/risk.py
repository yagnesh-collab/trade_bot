"""Risk rules that systematic traders apply on top of any signal.

- Trailing stop-loss per holding: exit a position when it falls `stop_loss` below its highest close
  since entry; stay out for `cooldown_days`, then follow the signal again.
- Portfolio circuit breaker: when the whole portfolio is `portfolio_drawdown` below its peak, go to
  cash for `cooldown_days` (then the peak is reset so trading can resume).
- Position cap: no single asset above `max_weight`.

Everything is decided with closes up to the day itself; the engine still delays execution by one day.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RiskRules:
    stop_loss: float | None = 0.15
    portfolio_drawdown: float | None = 0.20
    cooldown_days: int = 20
    max_weight: float = 1.0
    cash: str = "BIL"
    cost: float = 0.0005  # used only to track the portfolio value for the circuit breaker


def apply_risk_rules(targets: pd.DataFrame, closes: pd.DataFrame, rules: RiskRules,
                     highs: pd.DataFrame | None = None, lows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return adjusted target weights (NaN rows stay NaN). With `highs`/`lows` the trailing stop works like
    the broker's: the high-water mark uses the day's high and the trigger uses the day's low."""
    cash = rules.cash
    cols = list(targets.columns) + ([cash] if cash not in targets.columns else [])
    risky = [c for c in cols if c != cash]
    raw = targets.reindex(columns=cols)
    if cash not in targets.columns:
        raw[cash] = 0.0
    raw.loc[targets.isna().any(axis=1), :] = np.nan
    rets = closes[cols].pct_change().fillna(0.0)
    out = pd.DataFrame(np.nan, index=raw.index, columns=cols)

    held = pd.Series(0.0, index=cols)
    equity = peak = 1.0
    high: dict[str, float] = {}
    stop_until = {a: -1 for a in risky}
    breaker_until = -1
    for i, day in enumerate(raw.index):
        row = raw.loc[day]
        if row.isna().any():
            continue
        close = closes.loc[day]
        equity *= 1.0 + float((held * rets.loc[day]).sum())
        peak = max(peak, equity)

        for a in risky:
            if held[a] > 0:
                mark = float(highs.loc[day, a]) if highs is not None else float(close[a])
                trigger = float(lows.loc[day, a]) if lows is not None else float(close[a])
                high[a] = max(high.get(a, -np.inf), mark)
                if rules.stop_loss is not None and trigger < high[a] * (1 - rules.stop_loss):
                    stop_until[a] = i + rules.cooldown_days
            else:
                high.pop(a, None)
        if rules.portfolio_drawdown is not None and i >= breaker_until and equity / peak - 1 < -rules.portfolio_drawdown:
            breaker_until = i + rules.cooldown_days
            peak = equity  # restart the measurement when trading resumes

        w = row.copy()
        if i < breaker_until:
            w[risky] = 0.0
        for a in risky:
            if i < stop_until[a]:
                w[a] = 0.0
        w[risky] = w[risky].clip(upper=rules.max_weight)
        w[cash] = 1.0 - float(w[risky].sum())
        out.loc[day] = w

        turnover = float((w - held).abs().sum())
        equity *= 1.0 - rules.cost * turnover
        for a in risky:
            if held[a] <= 0 < w[a]:
                high[a] = float(close[a])  # new entry: trail from today's close
        held = w
    return out
