# trade_bot

A rules-based trading bot for US ETFs. It runs three strategies side by side as **paper portfolios**
($10,000 of fake money each). After every US trading day it emails you a report, and on the 1st of
each month it emails a monthly summary. It runs on GitHub Actions, so your computer doesn't need to be on.

> **Read this first.** No strategy makes a profit every month. These strategies have historically
> made money *over years*, and they still have losing months. The bot exists to measure, with real
> numbers, which strategy works before any real money is involved. Not financial advice.

## The strategies

| Key | Strategy | Rule | Trades |
|---|---|---|---|
| `buy_hold_spy` | Buy & hold S&P 500 (benchmark) | Always 100 % SPY | Never |
| `trend_qqq` | Trend-following Nasdaq-100 | Hold QQQ while it closes above its 200-day average; otherwise hold T-bills (BIL) | A few times a year |
| `dual_momentum` | Dual momentum | At each month-end: if US stocks beat T-bills over 12 months, hold whichever of SPY / EFA (international) did better; otherwise hold bonds (AGG) | Up to monthly |
| `trend_qqq_vt` | Trend-following Nasdaq-100, **volatility-targeted** | Same signal as `trend_qqq`, but the position is sized so recent volatility stays near 10 % a year; the rest sits in BIL. Never leveraged | A few times a year, plus small size changes |
| `buffett_90_10` | Buffett 90/10 (passive) | 90 % SPY, 10 % T-bills, never trades. Warren Buffett's own advice for private investors | Never |
| `sixty_forty` | 60/40 (passive) | 60 % SPY, 40 % bonds (AGG) | Never |
| `golden_cross_spy` | Golden cross | SPY while its 50-day average is above its 200-day average, else T-bills | A few times a year |
| `mean_reversion_rsi2` | Mean reversion RSI(2) | Buy SPY after a sharp dip in an uptrend (RSI(2) below 10), sell when it closes above its 5-day average | Often: costs matter |
| `multi_asset_momentum` | **Multi-asset momentum** | At each month-end: rank SPY, QQQ, EFA, EEM (emerging markets), GLD (gold), TLT (long bonds) by 12-month return; hold the best two that beat T-bills, equal-weighted; anything left in BIL | Up to monthly |

Why these strategies:

- **Trend-following and dual momentum** are among the best-documented rules-based strategies. Both aim to stay invested in rising markets and step aside during long crashes (2008, 2020, 2022). They usually cut the deepest losses sharply, but they can lag buy-and-hold in choppy markets.
- **The benchmark** keeps the comparison honest. If neither strategy beats plain SPY after 6–12 months, the right decision is to just buy and hold.
- **Volatility targeting** is the single most useful risk control a simple strategy can add: it cuts position size when markets get wild (exactly when crashes happen) and never adds leverage. Over long periods it tends to keep the return and shrink the worst drawdowns.
- **Multi-asset momentum** spreads the bet over assets that rarely fall together (stocks, gold, long bonds). Diversification is the one improvement that raises return per unit of risk without needing to predict anything.
- **ETFs only**: SPY, QQQ, EFA, EEM, AGG, GLD, TLT and BIL are liquid and cheap to trade, and they can't go bankrupt the way a single company can.

## Risk rules (what disciplined traders actually use)

Every active strategy runs with the same risk overlay, configured in `[stops]` of `config.toml`:

| Rule | Default | What it does |
|---|---|---|
| Trailing stop-loss | 15 % | Exit a holding that falls 15 % below its highest close since entry; stay out 20 trading days, then follow the signal again |
| Portfolio circuit breaker | 20 % | If the whole portfolio is 20 % below its peak, go to cash for 20 trading days, then resume |
| Position cap | off (100 %) | Upper limit for any single asset |
| Volatility targeting | 10 % (the `_vt` strategy) | Size the position so recent volatility matches the target; never leveraged |

The passive allocations (benchmark, Buffett 90/10, 60/40) deliberately get no stops: their whole point is to hold through everything, and they show you what the rules cost or save. The backtest's sensitivity table also runs the trend strategy with stops of 5 to 30 % and breakers of 10 to 30 %, so you can see whether a given stop level helps or hurts over 20 years; stops often reduce the worst drawdown and cost a little return, which is a trade you choose consciously.

About Warren Buffett: he is not a trader. He buys businesses and holds them for decades; no rule-based bot copies that judgment. The nearest thing is his own advice for private investors, which is in the table above as `buffett_90_10`, and it is a benchmark every active strategy has to beat.

What this does **not** do: produce a profit every month. No rule-based strategy can. The point of the extra tables below is to find out, with numbers, whether an edge is real before any real money is involved.

The simulation includes trading costs and a one-day signal delay: a decision made at a day's close is traded the next day. That way the bot can never "see the future".

## What you receive

- **Daily email** (around 23:15 Berlin time, Mon–Fri): each strategy's holding, any BUY/SELL switch, today's change, value, return since start, and largest drop so far.
- **Monthly email** (on the 1st): last month's return per strategy, ranked against the benchmark; the number of up months so far; the best possible SPY trade that month in hindsight, for comparison; and the 20-year backtest for context.
- **Backtest** (run it whenever you like): 20 years of results with calendar-year returns, plus two robustness tables: the same strategies with nearby parameters (if the result changes a lot when the moving-average window moves from 200 to 250 days, the edge was noise), and how often each strategy beat the benchmark over every rolling 3-year window.
- **Decision rule** in the monthly email: a "last 12 months vs the benchmark" table and the pre-committed rule that real money only moves after at least 6 months of paper results, and only into a strategy that beat the benchmark on both return and max drop. If none does, the answer is buy and hold.

Every report is also shown on the run's summary page under **Actions**.

## Setup (about 15 minutes)

### 1. Make the repository private
Go to **Settings → General → Danger Zone → Change visibility → Private**. Your reports and settings
are then visible only to you. The daily runs fit easily inside GitHub's free monthly Actions minutes.

### 2. Turn on secret protection
Go to **Settings → Code security**. Enable **Secret Protection** (secret scanning) and **Push protection**,
and **Dependabot alerts**. If you ever try to push a key by mistake, GitHub will block the push.

### 3. Create a Gmail app password (for the emails)
1. Turn on 2-step verification for your Google account.
2. Create an app password at https://myaccount.google.com/apppasswords and copy the 16 characters.
   Use the app password, never your normal Google password. You can revoke it at any time.

### 4. Add the secrets to GitHub
Go to **Settings → Environments → New environment**, name it `trading`, then **Add environment secret** for each:

| Secret | Value |
|---|---|
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the app password from step 3 |
| `REPORT_TO` | where the reports should go (can be the same address) |
| `SMTP_HOST` / `SMTP_PORT` | optional; defaults to `smtp.gmail.com` / `465` |

Secrets never appear in the code, the logs or the reports. GitHub masks them in output.

### 5. Test it
1. Go to **Actions → Backtest → Run workflow**. You'll get an email with the 20-year results.
2. Go to **Actions → Daily report → Run workflow** and tick *force*. You'll get your first daily email.

After that it runs on its own every trading day.

## Optional: Alpaca paper trading (real broker, fake money)

The bot can also place orders in an Alpaca **paper** account for one strategy (`[broker] strategy` in
`config.toml`, default `trend_qqq`). This tests real order handling with no risk.

1. Create a free account at https://alpaca.markets, switch to **Paper Trading**, and generate API keys. Paper keys start with `PK`.
2. Add `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` as secrets in the `trading` environment.
3. Under **Settings → Secrets and variables → Actions → Variables**, add the variable `ENABLE_PAPER_ORDERS` = `true`.

Safety built in:

- The code can only talk to `paper-api.alpaca.markets`.
- It refuses any key that isn't a paper key.
- Without `ENABLE_PAPER_ORDERS=true` it only prints what it would do.
- Orders are submitted after the close, so they fill at the next market open.

**Live trading is intentionally not implemented.** Consider it only after at least 3–6 months of paper results that beat the benchmark. Even then, start with money you can afford to lose and an account with withdrawals disabled for the API.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                      # offline tests
python -m bot backtest --no-email        # 20-year backtest, printed to the terminal
python -m bot daily --no-email --force   # today's report
python -m bot monthly --no-email --month 2026-11
```

For email locally, copy `.env.example` to `.env`, fill it in, then run `set -a; source .env; set +a`.
`.env` is git-ignored.

## Changing things

Change settings in `config.toml`: start date, starting capital, trading costs, which strategy drives
paper orders, the moving-average window, the volatility target, the momentum lookback and how many assets
the multi-asset strategy holds. Changes take effect on the next run. Because the paper portfolios are recalculated from
the start date every day, no saved state can drift or get corrupted.

## Security summary

- No secrets in the repo. `.env` and key files are git-ignored, and push protection blocks accidents.
- Secrets live in a GitHub environment and are passed only to the step that needs them.
- Workflows have read-only repository access (`contents: read`), use `persist-credentials: false`, and pin third-party actions to exact commit hashes. Dependabot keeps those hashes and Python packages up to date.
- The broker module is hard-wired to Alpaca paper trading.
