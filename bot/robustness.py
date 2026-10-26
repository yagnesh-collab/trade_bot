"""Robustness checks for the backtest: does the result survive other parameters and other periods?

A strategy whose return collapses when the lookback changes by a few weeks, or that only beats the
benchmark in one lucky stretch, has no real edge. These tables make that visible.
"""
from __future__ import annotations

import pandas as pd

from .engine import Result, metrics, simulate
from .risk import RiskRules, apply_risk_rules
from .strategies import TRADING_DAYS_PER_YEAR, dual_momentum, multi_asset_momentum, trend_qqq, trend_qqq_vt

SENSITIVITY = {
    "Trend QQQ, window": (lambda w: lambda c: trend_qqq(c, window=w), [100, 150, 200, 250, 300]),
    "Trend QQQ vol-target, target": (lambda v: lambda c: trend_qqq_vt(c, target_vol=v), [0.06, 0.08, 0.10, 0.12, 0.15]),
    "Dual momentum, lookback days": (lambda l: lambda c: dual_momentum(c, lookback=l), [126, 189, 252]),
    "Multi-asset momentum, top N": (lambda n: lambda c: multi_asset_momentum(c, top_n=n), [1, 2, 3]),
    "Trend QQQ, trailing stop-loss": (lambda sl: lambda c: apply_risk_rules(trend_qqq(c), c, RiskRules(stop_loss=sl, portfolio_drawdown=None)),
                                      [0.05, 0.10, 0.15, 0.20, 0.30]),
    "Trend QQQ, portfolio circuit breaker": (lambda dd: lambda c: apply_risk_rules(trend_qqq(c), c, RiskRules(stop_loss=None, portfolio_drawdown=dd)),
                                             [0.10, 0.15, 0.20, 0.30]),
}


def parameter_sensitivity(closes: pd.DataFrame, start, capital: float, cost: float) -> tuple[list[str], list[list[str]]]:
    """CAGR / max drop / Sharpe for each strategy across nearby parameter values."""
    from .report import pct  # local import: report imports this module too
    rows = []
    for label, (make, values) in SENSITIVITY.items():
        for v in values:
            fn = make(v)
            targets = fn(closes)
            first = targets.dropna().index[0]
            res = simulate(closes, targets, max(pd.Timestamp(start), first), capital, cost)
            m = metrics(res)
            rows.append([label, f"{v:.0%}" if isinstance(v, float) else str(v), pct(m["cagr"]),
                         pct(m["max_drawdown"]), f"{m['sharpe']:.2f}", str(m["trades"])])
    return ["Strategy, parameter", "Value", "Yearly (CAGR)", "Max drop", "Sharpe", "Trades"], rows


def rolling_win_rate(results: dict[str, Result], benchmark: str = "buy_hold_spy",
                     years: int = 3) -> tuple[list[str], list[list[str]]]:
    """How often each strategy beat the benchmark over every rolling `years`-year window (monthly steps)."""
    from .report import pct
    bench = results[benchmark].equity
    starts = bench.groupby(bench.index.to_period("M")).head(1).index  # first trading day of each month
    rows = []
    for key, res in results.items():
        if key == benchmark:
            continue
        wins, excess = 0, []
        for s in starts:
            e = s + pd.DateOffset(years=years)
            if e > bench.index[-1]:
                break
            b = bench.asof(e) / bench.asof(s) - 1
            r = res.equity.asof(e) / res.equity.asof(s) - 1
            excess.append(r - b)
            wins += r > b
        if excess:
            rows.append([key, f"{wins}/{len(excess)}", f"{wins / len(excess):.0%}",
                         pct(float(pd.Series(excess).median())), pct(float(min(excess)))])
    return ["Strategy", f"{years}-year windows won", "Win rate", "Median excess vs SPY", "Worst excess"], rows


def trailing(results: dict[str, Result], benchmark: str = "buy_hold_spy",
             days: int = TRADING_DAYS_PER_YEAR) -> tuple[list[str], list[list[str]]]:
    """Last `days` trading days: return, max drop, Sharpe, and whether the strategy beat the benchmark on both."""
    from .engine import max_drawdown
    from .report import pct
    def stats(res: Result):
        eq = res.equity.iloc[-days:]
        r = res.returns.iloc[-days:]
        sharpe = float(r.mean() / r.std() * (TRADING_DAYS_PER_YEAR ** 0.5)) if r.std() > 0 else float("nan")
        return float(eq.iloc[-1] / eq.iloc[0] - 1), max_drawdown(eq), sharpe
    b_ret, b_dd, _ = stats(results[benchmark])
    rows = []
    for key, res in results.items():
        ret, dd, sh = stats(res)
        beats = "benchmark" if key == benchmark else ("yes" if ret > b_ret and dd > b_dd else "no")
        rows.append([key, pct(ret), pct(dd), f"{sh:.2f}", beats])
    return ["Strategy", "Return", "Max drop", "Sharpe", "Beats SPY on both?"], rows
