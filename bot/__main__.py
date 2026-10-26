"""Command line: python -m bot {daily,monthly,backtest,monitor} [--no-email]"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from . import broker
from .config import ROOT, load_config
from .data import DataError, MarketData, fetch_market, validate
from .engine import Result, after_tax, metrics, simulate
from .report import Report, backtest_report, daily_report, monthly_report
from .robustness import parameter_sensitivity, rolling_win_rate, trailing
from .strategies import STRATEGIES, SYMBOLS, configure, targets_for

OUT = ROOT / "out"


def run_all(md: MarketData, start, capital: float, cost: float) -> dict[str, Result]:
    """Every strategy with its risk rules, executed at the next day's open."""
    return {k: simulate(md.closes, targets_for(k, md.closes, md.highs, md.lows), start, capital, cost, md.opens)
            for k in STRATEGIES}


def common_start(md: MarketData, wanted: str) -> pd.Timestamp:
    """First date on/after `wanted` where every strategy (rules included) already has a signal."""
    ready = max(targets_for(k, md.closes, md.highs, md.lows).dropna().index[0] for k in STRATEGIES)
    return max(pd.Timestamp(wanted), ready)


def publish(rep: Report, name: str, email: bool) -> None:
    OUT.mkdir(exist_ok=True)
    md = rep.markdown()
    (OUT / f"{name}.md").write_text(md)
    (OUT / f"{name}.html").write_text(rep.html())
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md + "\n")
    print(md)
    if email:
        from .notify import send_email
        send_email(rep.title, md, rep.html())


def warn(subject: str, text: str, email: bool) -> None:
    print(f"WARNING: {text}")
    if email:
        from .notify import send_email
        send_email(subject, text, f"<p>{text}</p>")


def main() -> None:
    p = argparse.ArgumentParser(prog="bot")
    p.add_argument("command", choices=["daily", "monthly", "backtest", "monitor"])
    p.add_argument("--no-email", action="store_true")
    p.add_argument("--force", action="store_true", help="daily: report even if there is no new close")
    p.add_argument("--month", help="YYYY-MM for the monthly report (default: last complete month)")
    args = p.parse_args()

    cfg = load_config()
    configure(cfg)
    capital, cost = cfg["paper"]["initial_capital"], cfg["costs"]["per_turnover"]
    universe = cfg.get("universe", {})
    tax_rate = float(cfg.get("tax", {}).get("rate", 0.0)) or None
    email = not args.no_email
    stops = cfg.get("stops", {})
    dd_limit = stops.get("portfolio_drawdown") if stops.get("enabled") else None
    cooldown = int(stops.get("cooldown_days", 20))
    b = cfg["broker"]

    if args.command == "backtest":
        md = fetch_market(SYMBOLS, cfg["backtest"]["start_date"], universe)
        start = common_start(md, cfg["backtest"]["start_date"])
        results = run_all(md, start, capital, cost)
        extra = [("Robustness: nearby parameters (an edge that vanishes here was noise)",
                  *parameter_sensitivity(md, start, capital, cost)),
                 ("Robustness: rolling 3-year windows vs the benchmark", *rolling_win_rate(results))]
        publish(backtest_report(results, extra, {k: after_tax(r, tax_rate) for k, r in results.items()} if tax_rate else None,
                                tax_rate), "backtest", email)
        return

    paper_start = pd.Timestamp(cfg["paper"]["start_date"])
    warmup = paper_start - pd.DateOffset(years=cfg["data"]["lookback_years"])
    md = fetch_market(SYMBOLS, f"{warmup:%Y-%m-%d}", universe)
    try:
        validate(md.closes)
    except DataError as exc:
        warn("Trading bot: data check failed, no orders placed", f"Data check failed: {exc}. Nothing was traded.", email)
        return
    if md.closes.index[-1] < paper_start:
        print(f"Paper trading starts {paper_start:%Y-%m-%d}; no data yet.")
        return
    results = run_all(md, paper_start, capital, cost)
    key = b["strategy"]
    latest_target = results[key].targets.iloc[-1]
    prices = md.closes.iloc[-1].to_dict()

    if args.command == "monitor":
        res = broker.monitor(float(b.get("trailing_stop_percent", 0.0)), dd_limit, cooldown,
                             target=latest_target, prices=prices, tickers=md.tickers,
                             cash_buffer=b["cash_buffer"], min_trade=b["min_trade_fraction"])
        if res.get("breaker") and email:
            from .notify import send_email
            text = (f"The portfolio circuit breaker closed all paper positions: equity ${res['equity']:,.2f}, "
                    f"{res['drawdown']:+.2%} from its peak. Cooldown: {cooldown} trading days.")
            send_email("Trading bot: circuit breaker triggered", text, f"<p>{text}</p>")
        return

    if args.command == "daily":
        today_ny = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
        if md.closes.index[-1] < today_ny and not args.force:
            print(f"No new close for {today_ny:%Y-%m-%d} (market holiday?) — nothing to report.")
            return
        publish(daily_report(results, capital), "daily", email)
        broker.rebalance(latest_target, prices, b["cash_buffer"], b["min_trade_fraction"], dd_limit, cooldown,
                         tickers=md.tickers)
    else:
        month = (pd.Period(args.month, "M") if args.month
                 else pd.Timestamp.now(tz="America/New_York").tz_localize(None).to_period("M") - 1)
        if month < paper_start.to_period("M"):
            print(f"{month} is before paper trading started.")
            return
        bt = fetch_market(SYMBOLS, cfg["backtest"]["start_date"], universe)
        bt_res = run_all(bt, common_start(bt, cfg["backtest"]["start_date"]), capital, cost)
        publish(monthly_report(results, capital, month, md.closes["SPY"], {k: metrics(r) for k, r in bt_res.items()},
                               trailing(results)),
                f"monthly-{month}", email)


if __name__ == "__main__":
    main()
