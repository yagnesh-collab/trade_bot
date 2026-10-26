"""Build daily / monthly / backtest reports as simple tables rendered to Markdown and HTML."""
from __future__ import annotations

import html
from dataclasses import dataclass, field

import pandas as pd

from .engine import Result, hindsight_best_trade, max_drawdown, metrics, monthly_returns
from .strategies import STRATEGIES

DISCLAIMER = ("Paper trading / simulation only. Past performance does not guarantee future results. "
              "Not financial advice.")


@dataclass
class Report:
    title: str
    lines: list[str] = field(default_factory=list)
    tables: list[tuple[str, list[str], list[list[str]]]] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.lines.append(text)

    def table(self, caption: str, header: list[str], rows: list[list[str]]) -> None:
        self.tables.append((caption, header, rows))

    def markdown(self) -> str:
        out = [f"# {self.title}", ""] + [f"{l}  " for l in self.lines] + [""]
        for caption, header, rows in self.tables:
            out += [f"## {caption}", "", "| " + " | ".join(header) + " |",
                    "|" + "---|" * len(header)]
            out += ["| " + " | ".join(r) + " |" for r in rows]
            out.append("")
        out.append(f"_{DISCLAIMER}_")
        return "\n".join(out)

    def html(self) -> str:
        e = html.escape
        parts = [f"<h2>{e(self.title)}</h2>"] + [f"<p style='margin:2px 0'>{e(l)}</p>" for l in self.lines]
        for caption, header, rows in self.tables:
            parts.append(f"<h3>{e(caption)}</h3><table style='border-collapse:collapse;font-family:sans-serif;font-size:13px'>")
            parts.append("<tr>" + "".join(f"<th style='border:1px solid #ccc;padding:4px 8px;background:#f4f4f4'>{e(h)}</th>" for h in header) + "</tr>")
            for r in rows:
                parts.append("<tr>" + "".join(f"<td style='border:1px solid #ccc;padding:4px 8px'>{e(c)}</td>" for c in r) + "</tr>")
            parts.append("</table>")
        parts.append(f"<p style='color:#777;font-size:12px'>{e(DISCLAIMER)}</p>")
        return "<div>" + "".join(parts) + "</div>"


def pct(x: float) -> str:
    return "n/a" if pd.isna(x) else f"{x * 100:+.2f}%"


def usd(x: float) -> str:
    return f"${x:,.2f}"


def holding(weights: pd.Series) -> str:
    w = weights[weights > 1e-9]
    if w.empty:
        return "Cash"
    return ", ".join(f"{s}" if abs(v - 1) < 1e-9 else f"{s} {v:.0%}" for s, v in w.items())


def daily_report(results: dict[str, Result], capital: float) -> Report:
    any_res = next(iter(results.values()))
    day = any_res.equity.index[-1]
    rep = Report(f"Daily trading report — {day:%a %d %b %Y}")
    rep.note(f"Paper portfolios started {any_res.equity.index[0]:%d %b %Y} with {usd(capital)} each.")

    rows, actions = [], []
    for key, res in sorted(results.items(), key=lambda kv: -kv[1].equity.iloc[-1]):
        now, nxt = holding(res.holdings.iloc[-1]), holding(res.targets.iloc[-1])
        action = "HOLD" if now == nxt else f"SWITCH {now} → {nxt}"
        if action != "HOLD":
            actions.append(f"{STRATEGIES[key].name}: {action} (at next close)")
        rows.append([STRATEGIES[key].name, now, action, pct(res.returns.iloc[-1]),
                     usd(res.equity.iloc[-1]), pct(res.equity.iloc[-1] / capital - 1),
                     pct(max_drawdown(res.equity))])
    rep.note("Signals today: " + ("; ".join(actions) if actions else "no changes — all strategies HOLD."))
    rep.table("Strategies (ranked by value)",
              ["Strategy", "Holding", "Signal", "Today", "Value", "Since start", "Max drop"], rows)
    return rep


def monthly_report(results: dict[str, Result], capital: float, month: pd.Period,
                   spy: pd.Series, backtest: dict[str, dict] | None) -> Report:
    rep = Report(f"Monthly trading report — {month.strftime('%B %Y')}")
    rows = []
    for key, res in results.items():
        m = monthly_returns(res.equity)
        if month not in m.index:
            continue
        rows.append([STRATEGIES[key].name, pct(m[month]), usd(res.equity[res.equity.index.to_period("M") <= month].iloc[-1]),
                     pct(res.equity[res.equity.index.to_period("M") <= month].iloc[-1] / capital - 1),
                     f"{(m[m.index <= month] > 0).sum()}/{(m.index <= month).sum()}",
                     str(int(res.switches[res.switches.index.to_period("M") == month].sum()))])
    rows.sort(key=lambda r: -float(r[1].rstrip("%")) if r[1] != "n/a" else 0)
    rep.table("Paper portfolios this month (ranked)",
              ["Strategy", "Month", "Value", "Since start", "Up months", "Trades"], rows)

    spy_m = spy[spy.index.to_period("M") == month]
    if len(spy_m) > 1:
        best, buy, sell = hindsight_best_trade(spy_m)
        rep.note(f"Hindsight check: the best possible single SPY trade this month was {pct(best)} "
                 f"(buy {buy:%d %b}, sell {sell:%d %b}) — only knowable after the fact."
                 if buy is not None else "Hindsight check: SPY only fell this month; the best trade was no trade.")

    if backtest:
        rep.table("Long-run backtest for context", *backtest_rows(backtest))
    return rep


def backtest_rows(stats: dict[str, dict]) -> tuple[list[str], list[list[str]]]:
    header = ["Strategy", "Yearly (CAGR)", "Max drop", "Sharpe", "Up months", "Worst month", "Trades", "$10k became"]
    rows = [[STRATEGIES[k].name, pct(s["cagr"]), pct(s["max_drawdown"]), f"{s['sharpe']:.2f}",
             f"{s['positive_months']:.0%}", pct(s["worst_month"]), str(s["trades"]),
             usd(s["final_value"])] for k, s in stats.items()]
    return header, rows


def backtest_report(results: dict[str, Result]) -> Report:
    any_res = next(iter(results.values()))
    start, end = any_res.equity.index[0], any_res.equity.index[-1]
    rep = Report(f"Backtest {start:%b %Y} – {end:%b %Y}")
    rep.note("Simulated with daily closes, 1-day signal delay and trading costs. "
             "No strategy here wins every month — look at yearly return AND max drop together.")
    stats = {k: metrics(r) for k, r in results.items()}
    rep.table("Summary", *backtest_rows(stats))

    yearly = pd.DataFrame({k: r.equity.groupby(r.equity.index.year).last() for k, r in results.items()})
    first = pd.Series({k: r.equity.iloc[0] for k, r in results.items()})
    yr = yearly / yearly.shift(1).fillna(first) - 1
    rep.table("Calendar-year returns", ["Year"] + [STRATEGIES[k].name for k in yr.columns],
              [[str(y)] + [pct(v) for v in row] for y, row in yr.iterrows()])
    return rep
