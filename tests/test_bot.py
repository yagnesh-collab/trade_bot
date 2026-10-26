"""Offline tests using synthetic prices (no network, no secrets)."""
import numpy as np
import pandas as pd
import pytest

from bot import broker
from bot.data import DataError, validate
from bot.engine import after_tax, hindsight_best_trade, max_drawdown, metrics, monthly_returns, simulate
from bot.report import backtest_report, daily_report, monthly_report
from bot.risk import RiskRules, apply_risk_rules
from bot.robustness import parameter_sensitivity, rolling_win_rate, trailing
import bot.strategies as strategies
from bot.strategies import (MOMENTUM_UNIVERSE, STRATEGIES, SYMBOLS, buffett_90_10, build_strategies, buy_hold_spy, configure,
                            dual_momentum, golden_cross_spy, mean_reversion_rsi2, month_end_mask, multi_asset_momentum,
                            multi_asset_momentum_vt, rsi, sixty_forty, targets_for, trend_qqq, volatility_target)


def synthetic(days=900, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=days)
    rets = rng.normal(0.0004, 0.01, size=(days, len(SYMBOLS)))
    return pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=SYMBOLS)


def test_buy_hold_matches_asset_return():
    c = synthetic()
    start = c.index[300]
    res = simulate(c, STRATEGIES["buy_hold_spy"].fn(c), start, 10_000, cost=0.0)
    expected = 10_000 * c["SPY"].iloc[-1] / c["SPY"].loc[start]
    assert res.equity.iloc[-1] == pytest.approx(expected)
    assert res.switches.sum() == 1  # the initial buy


def test_no_lookahead():
    """Changing tomorrow's price must not change today's decision or equity."""
    c = synthetic()
    c2 = c.copy()
    c2.iloc[-1] *= 3
    start = c.index[400]
    for s in STRATEGIES.values():
        a = simulate(c, s.fn(c), start, 10_000, 0.0005)
        b = simulate(c2, s.fn(c2), start, 10_000, 0.0005)
        pd.testing.assert_series_equal(a.equity.iloc[:-1], b.equity.iloc[:-1])


def test_trend_rule():
    idx = pd.bdate_range("2020-01-01", periods=300)
    up = pd.Series(np.linspace(100, 200, 300), index=idx)
    c = pd.DataFrame({"QQQ": up, "BIL": 100.0})
    w = trend_qqq(c)
    assert w.iloc[:199].isna().all().all()
    assert (w.iloc[199:]["QQQ"] == 1).all()
    c["QQQ"] = up[::-1].values
    assert (trend_qqq(c).iloc[199:]["BIL"] == 1).all()


def test_dual_momentum_monthly_and_defensive():
    c = synthetic(days=600)
    w = dual_momentum(c).dropna()
    assert (w.sum(axis=1) == 1).all()
    changes = w.ne(w.shift()).any(axis=1).iloc[1:]
    assert changes[changes].index.isin(w.index[month_end_mask(w.index)]).all()
    crash = c.copy()
    crash["SPY"] = crash["EFA"] = np.linspace(200, 50, len(c))
    assert (dual_momentum(crash).dropna()["AGG"] == 1).all()


def test_month_end_mask():
    idx = pd.bdate_range("2024-01-29", "2024-02-02")
    m = month_end_mask(idx)
    assert list(m) == [False, False, True, False, False]
    assert month_end_mask(pd.bdate_range("2024-01-25", "2024-01-31")).iloc[-1]


def test_hindsight_best_trade():
    p = pd.Series([5, 3, 8, 1, 6, 2], index=pd.bdate_range("2024-01-01", periods=6))
    best, buy, sell = hindsight_best_trade(p)
    assert best == pytest.approx(5.0)  # buy at 1, sell at 6
    assert (buy, sell) == (p.index[3], p.index[4])
    assert hindsight_best_trade(pd.Series([3, 2, 1], index=p.index[:3]))[1] is None


def test_metrics_and_drawdown():
    eq = pd.Series([100, 120, 90, 130], index=pd.bdate_range("2024-01-30", periods=4))
    assert max_drawdown(eq) == pytest.approx(-0.25)
    m = monthly_returns(eq)
    assert m.iloc[0] == pytest.approx(0.2)        # Jan: 100 -> 120
    assert m.iloc[1] == pytest.approx(130 / 120 - 1)


def test_costs_reduce_returns():
    c = synthetic()
    s = STRATEGIES["trend_qqq"]
    free = simulate(c, s.fn(c), c.index[300], 10_000, 0.0)
    paid = simulate(c, s.fn(c), c.index[300], 10_000, 0.001)
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]


def test_reports_render():
    c = synthetic()
    res = {k: simulate(c, s.fn(c), c.index[300], 10_000, 0.0005) for k, s in STRATEGIES.items()}
    assert "Daily trading report" in daily_report(res, 10_000).markdown()
    month = c.index[-40].to_period("M")
    stats = {k: metrics(r) for k, r in res.items()}
    md = monthly_report(res, 10_000, month, c["SPY"], stats).markdown()
    assert "Hindsight check" in md and "Long-run backtest" in md
    assert "<table" in backtest_report(res).html()


def test_plan_orders_sells_first():
    orders = broker.plan_orders(pd.Series({"QQQ": 1.0}), 10_000, {"BIL": 9_800},
                                {"QQQ": 500, "BIL": 90}, cash_buffer=0.02, min_trade=0.01)
    assert [o["side"] for o in orders] == ["sell", "buy"]
    assert orders[0]["close_all"] and orders[1]["notional"] == pytest.approx(9_800)


def test_broker_refuses_live_keys(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY_ID", "AKLIVEKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "x")
    with pytest.raises(broker.BrokerError, match="PAPER"):
        broker._session()
    assert broker.PAPER_URL == "https://paper-api.alpaca.markets"


def test_volatility_target_only_reduces_exposure():
    c = synthetic(days=400, seed=3)
    raw = trend_qqq(c)
    vt = volatility_target(raw, c, target_vol=0.02, window=20)  # very low target -> must scale down
    ok = vt.dropna()
    assert ((ok.sum(axis=1) - 1).abs() < 1e-9).all()               # fully allocated: risky + cash = 100 %
    assert (ok["QQQ"] <= raw.loc[ok.index, "QQQ"] + 1e-12).all()  # never above the raw signal
    assert (ok["QQQ"] < raw.loc[ok.index, "QQQ"]).any()           # and actually reduced somewhere
    calm = volatility_target(raw, c, target_vol=5.0, window=20).dropna()
    pd.testing.assert_frame_equal(calm, raw.loc[calm.index], check_dtype=False)  # high target -> unchanged


def test_multi_asset_momentum_picks_winners_and_goes_to_cash():
    c = synthetic(days=600, seed=1)
    n = len(c)
    c["GLD"] = np.linspace(100, 400, n)     # strongest
    c["TLT"] = np.linspace(100, 300, n)     # second
    for s in ["SPY", "QQQ", "EFA", "EEM"]:
        c[s] = np.linspace(100, 90, n)      # losers
    c["BIL"] = np.linspace(100, 102, n)
    w = multi_asset_momentum(c, top_n=2).dropna()
    assert (w[["GLD", "TLT"]] == 0.5).all().all() and (w[["SPY", "QQQ", "EFA", "EEM", "BIL"]] == 0).all().all()
    changes = w.ne(w.shift()).any(axis=1).iloc[1:]
    assert changes[changes].index.isin(w.index[month_end_mask(w.index)]).all()
    for s in MOMENTUM_UNIVERSE:
        c[s] = np.linspace(100, 90, n)      # everything loses to T-bills
    assert (multi_asset_momentum(c, top_n=2).dropna()["BIL"] == 1).all()


def test_configure_rebuilds_strategy_table():
    configure({"trend": {"window": 150}, "risk": {"target_vol": 0.08}, "momentum": {"top_n": 3}})
    try:
        assert "150-day" in STRATEGIES["trend_qqq"].description
        assert "8%" in STRATEGIES["trend_qqq_vt"].name
        assert "top 3" in STRATEGIES["multi_asset_momentum"].name
    finally:
        STRATEGIES.clear(); STRATEGIES.update(build_strategies())


def test_robustness_tables():
    c = synthetic(days=1100, seed=2)
    start = c.index[320]
    header, rows = parameter_sensitivity(c, start, 10_000, 0.0005)
    assert len(rows) == 5 + 5 + 4 + 5 + 3 + 3 + 5 + 4 and len(header) == 6
    res = {k: simulate(c, s.fn(c), start, 10_000, 0.0005) for k, s in STRATEGIES.items()}
    header, rows = rolling_win_rate(res, years=1)
    assert len(rows) == len(STRATEGIES) - 1 and all(r[2].endswith("%") for r in rows)
    header, rows = trailing(res)
    assert rows[0][-1] == "benchmark" and all(r[-1] in {"yes", "no", "benchmark"} for r in rows)


def test_trailing_stop_exits_and_reenters_after_cooldown():
    idx = pd.bdate_range("2024-01-01", periods=80)
    px = np.full(80, 100.0)
    px[30:] = 80.0   # a 20 % drop on day 30
    c = pd.DataFrame({"QQQ": px, "BIL": 100.0}, index=idx)
    raw = pd.DataFrame({"QQQ": 1.0, "BIL": 0.0}, index=idx)
    w = apply_risk_rules(raw, c, RiskRules(stop_loss=0.15, portfolio_drawdown=None, cooldown_days=10))
    assert w["QQQ"].iloc[29] == 1.0
    assert (w["QQQ"].iloc[30:40] == 0).all() and (w["BIL"].iloc[30:40] == 1).all()  # stopped out, in cash
    assert w["QQQ"].iloc[40] == 1.0                                              # back in after the cooldown
    assert ((w.sum(axis=1) - 1).abs() < 1e-9).all()


def test_circuit_breaker_goes_to_cash_on_portfolio_drawdown():
    idx = pd.bdate_range("2024-01-01", periods=60)
    px = 100 * np.cumprod(np.r_[np.ones(20), np.full(10, 0.97), np.ones(30)])  # -26 % over 10 days
    c = pd.DataFrame({"SPY": px, "BIL": 100.0}, index=idx)
    raw = pd.DataFrame({"SPY": 1.0, "BIL": 0.0}, index=idx)
    w = apply_risk_rules(raw, c, RiskRules(stop_loss=None, portfolio_drawdown=0.20, cooldown_days=15))
    first_out = w.index[(w["SPY"] == 0)][0]
    assert 20 < idx.get_loc(first_out) < 30                      # tripped during the slide
    assert (w["SPY"].iloc[idx.get_loc(first_out):idx.get_loc(first_out) + 15] == 0).all()
    assert w["SPY"].iloc[-1] == 1.0                              # resumed afterwards


def test_risk_rules_apply_only_to_active_strategies():
    configure({"stops": {"enabled": True, "stop_loss": 0.15, "portfolio_drawdown": 0.2}})
    try:
        assert strategies.RULES is not None
        c = synthetic(days=500, seed=5)
        pd.testing.assert_frame_equal(targets_for("buy_hold_spy", c), buy_hold_spy(c))      # passive: untouched
        pd.testing.assert_frame_equal(targets_for("buffett_90_10", c), buffett_90_10(c))
        w = targets_for("trend_qqq", c).dropna()
        assert ((w.sum(axis=1) - 1).abs() < 1e-9).all() and "BIL" in w.columns              # active: rules applied
    finally:
        configure({})
        assert strategies.RULES is None


def test_classic_strategies():
    c = synthetic(days=600, seed=4)
    assert (buffett_90_10(c)[["SPY", "BIL"]].sum(axis=1) == 1).all() and (sixty_forty(c)["AGG"] == 0.4).all()
    g = golden_cross_spy(c).dropna()
    assert set(np.unique(g.values)) <= {0.0, 1.0} and (g.sum(axis=1) == 1).all()
    r = rsi(pd.Series(np.linspace(1, 2, 50)), 2)
    assert (r.iloc[5:] > 99).all()                                 # a pure uptrend saturates RSI near 100
    m = mean_reversion_rsi2(c).dropna()
    assert (m.sum(axis=1) == 1).all() and (m["SPY"] == 1).any() and (m["BIL"] == 1).any()


def test_needs_protection_only_for_uncovered_whole_shares():
    positions = [{"symbol": "QQQ", "qty": "12.4"}, {"symbol": "GLD", "qty": "0.7"}, {"symbol": "TLT", "qty": "5"}]
    orders = [{"symbol": "TLT", "type": "trailing_stop", "side": "sell"}, {"symbol": "QQQ", "type": "market", "side": "buy"}]
    assert broker.needs_protection(positions, orders) == [{"symbol": "QQQ", "qty": 12}]


def test_breaker_active_from_equity_history():
    calm = [100, 101, 99, 102, 103]
    assert not broker.breaker_active(calm, 0.20, 20)
    crash = [100, 105, 90, 80, 82]                      # -24 % from the 105 peak, 1 day ago
    assert broker.breaker_active(crash, 0.20, 20)
    old_crash = crash + [83] * 25                        # tripped 26 points ago, cooldown 20 -> over
    assert not broker.breaker_active(old_crash, 0.20, 20)
    assert broker.breaker_active(old_crash, 0.20, 30)


def test_monitor_is_a_no_op_without_paper_orders(monkeypatch):
    monkeypatch.delenv("ENABLE_PAPER_ORDERS", raising=False)
    assert broker.monitor(15.0, 0.2, 20) == {"status": "disabled"}


def test_trend_hysteresis_delays_and_filters_switches():
    idx = pd.bdate_range("2020-01-01", periods=320)
    px = np.full(320, 100.0)
    px[300:] = 100.5                               # 0.5 % above a flat 200-day average
    c = pd.DataFrame({"QQQ": px, "BIL": 100.0}, index=idx)
    assert trend_qqq(c).dropna()["QQQ"].iloc[-1] == 1.0                           # plain rule enters
    assert trend_qqq(c, band=0.02, confirm_days=3).dropna()["QQQ"].iloc[-1] == 0.0  # band blocks it
    spike = px.copy(); spike[305] = 103.0           # one day above the band
    c2 = pd.DataFrame({"QQQ": spike, "BIL": 100.0}, index=idx)
    assert trend_qqq(c2, band=0.02, confirm_days=3).dropna()["QQQ"].sum() == 0    # no entry on one day
    run = px.copy(); run[305:308] = 103.0            # three consecutive days above the band
    c3 = pd.DataFrame({"QQQ": run, "BIL": 100.0}, index=idx)
    w = trend_qqq(c3, band=0.02, confirm_days=3).dropna()
    assert w["QQQ"].loc[idx[306]] == 0.0 and w["QQQ"].loc[idx[307]] == 1.0        # enters on the third day


def test_validate_rejects_bad_or_stale_data():
    c = synthetic(days=300)
    validate(c, today=c.index[-1])
    bad = c.copy(); bad.iloc[100, 0] *= 2
    with pytest.raises(DataError, match="implausible"):
        validate(bad, today=c.index[-1])
    with pytest.raises(DataError, match="stale"):
        validate(c, today=c.index[-1] + pd.Timedelta(days=20))
    neg = c.copy(); neg.iloc[5, 1] = 0.0
    with pytest.raises(DataError, match="non-positive"):
        validate(neg, today=c.index[-1])


def test_next_open_execution_buys_one_day_later():
    """A decision at close k is filled at the next open; with open == close that is close k+1."""
    c = synthetic(days=400, seed=6)
    k = 300
    close_fill = simulate(c, buy_hold_spy(c), c.index[k], 10_000, 0.0)
    open_fill = simulate(c, buy_hold_spy(c), c.index[k], 10_000, 0.0, opens=c)
    assert close_fill.equity.iloc[-1] == pytest.approx(10_000 * c["SPY"].iloc[-1] / c["SPY"].iloc[k])
    assert open_fill.equity.iloc[-1] == pytest.approx(10_000 * c["SPY"].iloc[-1] / c["SPY"].iloc[k + 1])
    t = trend_qqq(c)
    assert simulate(c, t, c.index[k], 10_000, 0.0005, opens=c).switches.sum() == simulate(c, t, c.index[k], 10_000, 0.0005).switches.sum()


def test_after_tax_estimate():
    c = synthetic(days=900, seed=7)
    res = simulate(c, trend_qqq(c), c.index[300], 10_000, 0.0005)
    t = after_tax(res, 0.26375)
    assert t["tax_paid"] >= 0 and t["after_tax_value"] <= res.equity.iloc[-1] + 1e-9
    passive = simulate(c, buy_hold_spy(c), c.index[300], 10_000, 0.0)
    assert after_tax(passive, 0.26375)["tax_paid"] == 0.0        # nothing realized -> no tax


def test_vol_target_deadband_reduces_size_changes():
    c = synthetic(days=600, seed=8)
    raw = trend_qqq(c)
    jumpy = volatility_target(raw, c, target_vol=0.05, window=20, rebalance_band=0.0).dropna()
    calm = volatility_target(raw, c, target_vol=0.05, window=20, rebalance_band=0.10).dropna()
    changes = lambda w: int((w["QQQ"].diff().abs() > 1e-12).sum())
    assert changes(calm) < changes(jumpy)
    assert ((calm.sum(axis=1) - 1).abs() < 1e-9).all()
    m = multi_asset_momentum_vt(c).dropna()
    assert ((m.sum(axis=1) - 1).abs() < 1e-9).all() and (m[MOMENTUM_UNIVERSE] <= 0.5 + 1e-9).all().all()
