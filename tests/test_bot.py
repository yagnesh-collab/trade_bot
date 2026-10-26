"""Offline tests using synthetic prices (no network, no secrets)."""
import numpy as np
import pandas as pd
import pytest

from bot import broker
from bot.engine import hindsight_best_trade, max_drawdown, metrics, monthly_returns, simulate
from bot.report import backtest_report, daily_report, monthly_report
from bot.strategies import STRATEGIES, SYMBOLS, dual_momentum, month_end_mask, trend_qqq


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
