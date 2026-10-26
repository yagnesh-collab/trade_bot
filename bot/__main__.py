"""Command line: python -m bot {daily,monthly,backtest} [--no-email]"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from . import broker
from .config import ROOT, load_config
from .data import fetch_closes
from .engine import Result, metrics, simulate
from .report import Report, backtest_report, daily_report, monthly_report
from .strategies import STRATEGIES, SYMBOLS

OUT = ROOT / "out"


def run_all(closes: pd.DataFrame, start, capital: float, cost: float) -> dict[str, Result]:
    return {k: simulate(closes, s.fn(closes), start, capital, cost) for k, s in STRATEGIES.items()}


def common_start(closes: pd.DataFrame, wanted: str) -> pd.Timestamp:
    """First date on/after `wanted` where every strategy already has a signal."""
    ready = max(s.fn(closes).dropna().index[0] for s in STRATEGIES.values())
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


def main() -> None:
    p = argparse.ArgumentParser(prog="bot")
    p.add_argument("command", choices=["daily", "monthly", "backtest"])
    p.add_argument("--no-email", action="store_true")
    p.add_argument("--force", action="store_true", help="daily: report even if there is no new close")
    p.add_argument("--month", help="YYYY-MM for the monthly report (default: last complete month)")
    args = p.parse_args()

    cfg = load_config()
    capital, cost = cfg["paper"]["initial_capital"], cfg["costs"]["per_turnover"]
    email = not args.no_email

    if args.command == "backtest":
        closes = fetch_closes(SYMBOLS, cfg["backtest"]["start_date"])
        results = run_all(closes, common_start(closes, cfg["backtest"]["start_date"]), capital, cost)
        publish(backtest_report(results), "backtest", email)
        return

    paper_start = pd.Timestamp(cfg["paper"]["start_date"])
    warmup = paper_start - pd.DateOffset(years=cfg["data"]["lookback_years"])
    closes = fetch_closes(SYMBOLS, f"{warmup:%Y-%m-%d}")
    if closes.index[-1] < paper_start:
        print(f"Paper trading starts {paper_start:%Y-%m-%d}; no data yet.")
        return
    results = run_all(closes, paper_start, capital, cost)

    if args.command == "daily":
        today_ny = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
        if closes.index[-1] < today_ny and not args.force:
            print(f"No new close for {today_ny:%Y-%m-%d} (market holiday?) — nothing to report.")
            return
        publish(daily_report(results, capital), "daily", email)
        b = cfg["broker"]
        key = b["strategy"]
        broker.rebalance(results[key].targets.iloc[-1], closes.iloc[-1].to_dict(),
                         b["cash_buffer"], b["min_trade_fraction"])
    else:
        month = (pd.Period(args.month, "M") if args.month
                 else pd.Timestamp.now(tz="America/New_York").tz_localize(None).to_period("M") - 1)
        if month < paper_start.to_period("M"):
            print(f"{month} is before paper trading started.")
            return
        bt_closes = fetch_closes(SYMBOLS, cfg["backtest"]["start_date"])
        bt = run_all(bt_closes, common_start(bt_closes, cfg["backtest"]["start_date"]), capital, cost)
        publish(monthly_report(results, capital, month, closes["SPY"], {k: metrics(r) for k, r in bt.items()}),
                f"monthly-{month}", email)


if __name__ == "__main__":
    main()
