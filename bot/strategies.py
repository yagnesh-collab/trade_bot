"""Strategies return target weights per day, decided with data up to and including that day's close.

The engine shifts weights by one day, so a decision made at Monday's close earns Tuesday's return
(no look-ahead bias).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

import numpy as np
import pandas as pd

from .risk import RiskRules, apply_risk_rules

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


def trend_qqq(closes: pd.DataFrame, window: int = 200, band: float = 0.0, confirm_days: int = 1) -> pd.DataFrame:
    """Hold Nasdaq-100 (QQQ) while it closes above its moving average, else T-bills (BIL).

    Anti-whipsaw rules: `band` adds hysteresis (enter only above average*(1+band), exit only below
    average*(1-band)) and `confirm_days` requires that many consecutive closes before switching.
    With band=0 and confirm_days=1 this is the plain rule."""
    px, sma = closes["QQQ"], closes["QQQ"].rolling(window).mean()
    above, below = px > sma * (1 + band), px < sma * (1 - band)
    choice = pd.Series(None, index=closes.index, dtype=object)
    state, run_above, run_below = None, 0, 0
    for day in closes.index:
        if pd.isna(sma[day]):
            continue
        run_above = run_above + 1 if above[day] else 0
        run_below = run_below + 1 if below[day] else 0
        if state is None:
            state = "QQQ" if px[day] > sma[day] else "BIL"
        elif state == "BIL" and run_above >= confirm_days:
            state = "QQQ"
        elif state == "QQQ" and run_below >= confirm_days:
            state = "BIL"
        choice[day] = state
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


def buffett_90_10(closes: pd.DataFrame) -> pd.DataFrame:
    """Warren Buffett's advice to private investors: 90 % S&P 500, 10 % short-term government bills, never trade.

    The simulation rebalances to the target each day; the turnover that causes is tiny, so costs are negligible.
    """
    return pd.DataFrame({"SPY": 0.9, "BIL": 0.1}, index=closes.index)


def sixty_forty(closes: pd.DataFrame) -> pd.DataFrame:
    """The classic balanced allocation: 60 % stocks (SPY), 40 % bonds (AGG)."""
    return pd.DataFrame({"SPY": 0.6, "AGG": 0.4}, index=closes.index)


def golden_cross_spy(closes: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.DataFrame:
    """Hold SPY while its 50-day average is above its 200-day average (golden cross), else T-bills."""
    f, sl = closes["SPY"].rolling(fast).mean(), closes["SPY"].rolling(slow).mean()
    choice = pd.Series("BIL", index=closes.index, dtype=object)
    choice[f > sl] = "SPY"
    choice[sl.isna()] = None
    return _one_hot(choice, ["SPY", "BIL"])


def rsi(prices: pd.Series, length: int) -> pd.Series:
    """Wilder's RSI."""
    delta = prices.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = up / down.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def mean_reversion_rsi2(closes: pd.DataFrame, rsi_len: int = 2, entry: float = 10.0,
                        exit_ma: int = 5, trend_ma: int = 200) -> pd.DataFrame:
    """Short-term mean reversion (the classic RSI(2) rule): buy SPY after a sharp dip in an uptrend,
    sell when it closes back above its 5-day average. Otherwise T-bills. Trades often: costs matter."""
    px = closes["SPY"]
    r, ma_exit, ma_trend = rsi(px, rsi_len), px.rolling(exit_ma).mean(), px.rolling(trend_ma).mean()
    choice = pd.Series(None, index=closes.index, dtype=object)
    in_pos = False
    for day in closes.index:
        if pd.isna(ma_trend[day]):
            continue
        if in_pos and px[day] > ma_exit[day]:
            in_pos = False
        elif not in_pos and r[day] < entry and px[day] > ma_trend[day]:
            in_pos = True
        choice[day] = "SPY" if in_pos else "BIL"
    return _one_hot(choice, ["SPY", "BIL"])


MOMENTUM_UNIVERSE = ["SPY", "QQQ", "EFA", "EEM", "GLD", "TLT"]


def multi_asset_momentum(closes: pd.DataFrame, lookback: int = TRADING_DAYS_PER_YEAR, top_n: int = 2) -> pd.DataFrame:
    """Diversified momentum, rebalanced monthly.

    At each month-end, rank US stocks, Nasdaq-100, developed and emerging markets, gold and long
    bonds by their `lookback` return. Hold the best `top_n` of those that beat T-bills (BIL),
    equal-weighted; whatever is left over sits in BIL. Spreads risk over assets that rarely fall
    together, instead of betting everything on one index.
    """
    cols = MOMENTUM_UNIVERSE + ["BIL"]
    r = closes[cols] / closes[cols].shift(lookback) - 1
    w = pd.DataFrame(float("nan"), index=closes.index, columns=cols)
    decided = month_end_mask(closes.index) & r.notna().all(axis=1)
    for day in closes.index[decided]:
        row = r.loc[day, MOMENTUM_UNIVERSE]
        winners = row[row > r.loc[day, "BIL"]].sort_values(ascending=False).index[:top_n]
        weights = pd.Series(0.0, index=cols)
        if len(winners):
            weights[list(winners)] = 1.0 / top_n   # keep the slot size fixed; empty slots are cash
        weights["BIL"] = 1.0 - weights[MOMENTUM_UNIVERSE].sum()
        w.loc[day] = weights
    return w.ffill()


def volatility_target(weights: pd.DataFrame, closes: pd.DataFrame, target_vol: float = 0.10,
                      window: int = 20, cash: str = "BIL", max_scale: float = 1.0,
                      rebalance_band: float = 0.10) -> pd.DataFrame:
    """Risk control: scale the risky part of `weights` so recent realized volatility matches `target_vol`.

    Uses only returns up to each day's close (the engine still shifts by one day). Exposure is never
    raised above `max_scale` (1.0 = no leverage), so this can only reduce risk, never add it. The part
    taken out of risky assets is parked in `cash`.
    """
    risky = [c for c in weights.columns if c != cash]
    asset_ret = closes[weights.columns].pct_change()
    port_ret = (weights.shift(1) * asset_ret).sum(axis=1, min_count=1)
    vol = port_ret.rolling(window).std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    scale = (target_vol / vol.replace(0.0, np.nan)).clip(upper=max_scale).fillna(max_scale)
    if rebalance_band > 0:  # deadband: only change the size when it moved enough (far fewer small trades)
        held, out = None, []
        for v in scale.values:
            if held is None or abs(v - held) > rebalance_band or v >= max_scale > held:
                held = float(v)
            out.append(held)
        scale = pd.Series(out, index=scale.index)
    scaled = weights[risky].mul(scale, axis=0)
    scaled[cash] = 1.0 - scaled[risky].sum(axis=1)
    scaled[weights[risky].isna().any(axis=1) | vol.isna()] = float("nan")
    return scaled[list(weights.columns) if cash in weights.columns else risky + [cash]]


def trend_qqq_vt(closes: pd.DataFrame, window: int = 200, target_vol: float = 0.10, vol_window: int = 20,
                 band: float = 0.0, confirm_days: int = 1) -> pd.DataFrame:
    """Trend-following Nasdaq-100 with volatility targeting (same signal, risk-controlled size)."""
    return volatility_target(trend_qqq(closes, window, band, confirm_days), closes, target_vol, vol_window)


def multi_asset_momentum_vt(closes: pd.DataFrame, lookback: int = TRADING_DAYS_PER_YEAR, top_n: int = 2,
                            target_vol: float = 0.10, vol_window: int = 20) -> pd.DataFrame:
    """Multi-asset momentum with volatility targeting: diversified AND risk-sized. The lowest-drawdown
    active strategy in the backtest, which is why it is the default for paper orders."""
    return volatility_target(multi_asset_momentum(closes, lookback, top_n), closes, target_vol, vol_window)


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    description: str
    fn: Callable[[pd.DataFrame], pd.DataFrame]


# Passive allocations never get stop-loss rules: their whole point is to hold through everything.
PASSIVE = {"buy_hold_spy", "buffett_90_10", "sixty_forty"}

# Risk rules for the active strategies, set from config.toml by `configure`; None = off.
RULES: RiskRules | None = None


def targets_for(key: str, closes: pd.DataFrame, highs: pd.DataFrame | None = None,
                lows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Strategy signal plus the risk rules (when configured and the strategy is active)."""
    t = STRATEGIES[key].fn(closes)
    if RULES is not None and key not in PASSIVE:
        t = apply_risk_rules(t, closes, RULES, highs, lows)
    return t


def build_strategies(trend_window: int = 200, target_vol: float = 0.10, vol_window: int = 20,
                     momentum_lookback: int = TRADING_DAYS_PER_YEAR, top_n: int = 2,
                     band: float = 0.0, confirm_days: int = 1) -> dict[str, Strategy]:
    """The strategy table. Parameters come from config.toml via `configure`; defaults are the classic values."""
    anti = f" (band {band:.0%}, {confirm_days}-day confirmation)" if band or confirm_days > 1 else ""
    return {
        s.key: s for s in [
            Strategy("buy_hold_spy", "Buy & hold S&P 500 (benchmark)",
                     "Always 100 % SPY. Every strategy must beat this to be worth using.", buy_hold_spy),
            Strategy("buffett_90_10", "Buffett 90/10 (passive)",
                     "90 % SPY, 10 % T-bills, never trades. Buffett's own advice for private investors.", buffett_90_10),
            Strategy("sixty_forty", "60/40 stocks/bonds (passive)",
                     "60 % SPY, 40 % AGG, the classic balanced portfolio.", sixty_forty),
            Strategy("golden_cross_spy", "Golden cross S&P 500",
                     "SPY while the 50-day average is above the 200-day average, else T-bills.", golden_cross_spy),
            Strategy("mean_reversion_rsi2", "Mean reversion RSI(2) S&P 500",
                     "Buy SPY on a sharp dip in an uptrend (RSI(2) below 10), sell above the 5-day average.",
                     mean_reversion_rsi2),
            Strategy("trend_qqq", "Trend-following Nasdaq-100",
                     f"QQQ while above its {trend_window}-day average, otherwise T-bills (BIL){anti}.",
                     partial(trend_qqq, window=trend_window, band=band, confirm_days=confirm_days)),
            Strategy("trend_qqq_vt", f"Trend Nasdaq-100, {target_vol:.0%} vol target",
                     "Same signal, but position size shrinks when recent volatility is high.",
                     partial(trend_qqq_vt, window=trend_window, target_vol=target_vol, vol_window=vol_window,
                             band=band, confirm_days=confirm_days)),
            Strategy("dual_momentum", "Dual momentum (monthly)",
                     "Best of SPY/EFA by 12-month return if stocks beat T-bills, else bonds (AGG).",
                     partial(dual_momentum, lookback=momentum_lookback)),
            Strategy("multi_asset_momentum", f"Multi-asset momentum (top {top_n} of 6)",
                     "Monthly: best assets among SPY, QQQ, EFA, EEM, GLD, TLT that beat T-bills; rest in BIL.",
                     partial(multi_asset_momentum, lookback=momentum_lookback, top_n=top_n)),
            Strategy("multi_asset_momentum_vt", f"Multi-asset momentum, {target_vol:.0%} vol target",
                     "Diversified and risk-sized: the multi-asset strategy with volatility targeting.",
                     partial(multi_asset_momentum_vt, lookback=momentum_lookback, top_n=top_n,
                             target_vol=target_vol, vol_window=vol_window)),
        ]
    }


STRATEGIES: dict[str, Strategy] = build_strategies()


def configure(cfg: dict) -> None:
    """Rebuild STRATEGIES and RULES in place from config.toml."""
    global RULES
    t, r, m, st = cfg.get("trend", {}), cfg.get("risk", {}), cfg.get("momentum", {}), cfg.get("stops", {})
    RULES = None
    if st.get("enabled", False):
        RULES = RiskRules(stop_loss=st.get("stop_loss") or None, portfolio_drawdown=st.get("portfolio_drawdown") or None,
                          cooldown_days=int(st.get("cooldown_days", 20)), max_weight=float(st.get("max_weight", 1.0)),
                          cost=float(cfg.get("costs", {}).get("per_turnover", 0.0005)))
    STRATEGIES.clear()
    STRATEGIES.update(build_strategies(
        trend_window=int(t.get("window", 200)), band=float(t.get("band", 0.0)), confirm_days=int(t.get("confirm_days", 1)),
        target_vol=float(r.get("target_vol", 0.10)), vol_window=int(r.get("vol_window", 20)),
        momentum_lookback=int(m.get("lookback_days", TRADING_DAYS_PER_YEAR)), top_n=int(m.get("top_n", 2)),
    ))


SYMBOLS = ["SPY", "QQQ", "EFA", "EEM", "AGG", "BIL", "GLD", "TLT"]
