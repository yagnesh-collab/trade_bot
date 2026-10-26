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

Why these strategies:

- **Trend-following and dual momentum** are among the best-documented rules-based strategies. Both aim to stay invested in rising markets and step aside during long crashes (2008, 2020, 2022). They usually cut the deepest losses sharply, but they can lag buy-and-hold in choppy markets.
- **The benchmark** keeps the comparison honest. If neither strategy beats plain SPY after 6–12 months, the right decision is to just buy and hold.
- **ETFs only**: SPY, QQQ, EFA, AGG and BIL are liquid and cheap to trade, and they can't go bankrupt the way a single company can.

The simulation includes trading costs and a one-day signal delay: a decision made at a day's close is traded the next day. That way the bot can never "see the future".

## What you receive

- **Daily email** (around 23:15 Berlin time, Mon–Fri): each strategy's holding, any BUY/SELL switch, today's change, value, return since start, and largest drop so far.
- **Monthly email** (on the 1st): last month's return per strategy, ranked against the benchmark; the number of up months so far; the best possible SPY trade that month in hindsight, for comparison; and the 20-year backtest for context.
- **Backtest** (run it whenever you like): 20 years of results with calendar-year returns.

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
paper orders. Changes take effect on the next run. Because the paper portfolios are recalculated from
the start date every day, no saved state can drift or get corrupted.

## Security summary

- No secrets in the repo. `.env` and key files are git-ignored, and push protection blocks accidents.
- Secrets live in a GitHub environment and are passed only to the step that needs them.
- Workflows have read-only repository access (`contents: read`), use `persist-credentials: false`, and pin third-party actions to exact commit hashes. Dependabot keeps those hashes and Python packages up to date.
- The broker module is hard-wired to Alpaca paper trading.
