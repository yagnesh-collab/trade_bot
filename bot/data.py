"""Daily price data (dividend/split-adjusted open/high/low/close) from Yahoo Finance, with sanity checks
before anything is traded on it. Symbols are canonical names (SPY, QQQ, ...); `[universe]` in config.toml
can map each one to another ticker, e.g. a UCITS ETF listed in Europe."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_DAILY_MOVE = 0.35   # no ETF in this universe moves this much in a day; anything larger is a data error
MAX_STALE_DAYS = 7      # refuse to trade on data older than a week


class DataError(RuntimeError):
    pass


@dataclass
class MarketData:
    closes: pd.DataFrame
    opens: pd.DataFrame
    highs: pd.DataFrame
    lows: pd.DataFrame
    tickers: dict[str, str]   # canonical symbol -> ticker actually downloaded / traded


def validate(closes: pd.DataFrame, today: pd.Timestamp | None = None) -> None:
    """Raise DataError on prices that must not be traded on: non-positive, implausible jumps, stale."""
    if (closes <= 0).any().any():
        raise DataError("non-positive price in the data")
    moves = closes.pct_change().abs()
    mask = (moves > MAX_DAILY_MOVE).to_numpy()  # NaN on the first row compares False
    if mask.any():
        i, j = np.argwhere(mask)[0]
        raise DataError(f"implausible daily move of {float(moves.iloc[i, j]):.0%} in {moves.columns[j]} "
                        f"on {moves.index[i]:%Y-%m-%d}")
    today = today or pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
    last = closes.index[-1]
    if (today - last).days > MAX_STALE_DAYS:
        raise DataError(f"stale data: last close is {last:%Y-%m-%d}")


def fetch_market(symbols: list[str], start: str, universe: dict[str, str] | None = None) -> MarketData:
    import yfinance as yf  # imported lazily so tests run without network

    tickers = {s: (universe or {}).get(s) or s for s in symbols}
    raw = yf.download(sorted(set(tickers.values())), start=start, auto_adjust=True, progress=False, threads=False)
    if raw.empty:
        raise DataError("No price data downloaded")
    back = {t: s for s, t in tickers.items()}

    def field(name: str) -> pd.DataFrame:
        if isinstance(raw.columns, pd.MultiIndex):
            f = raw[name]
        else:  # a single ticker comes back without the second column level
            f = raw[[name]].rename(columns={name: next(iter(tickers.values()))})
        f = f.rename(columns=back).reindex(columns=symbols)
        f.index = pd.to_datetime(f.index).tz_localize(None)
        return f

    closes = field("Close").dropna(how="all").ffill()
    missing = [s for s in symbols if closes[s].isna().all()]
    if missing:
        raise DataError(f"Missing data for: {missing} (tickers {[tickers[m] for m in missing]})")
    keep = closes.dropna().index  # the period in which every symbol exists
    closes = closes.loc[keep]
    opens, highs, lows = (field(n).ffill().reindex(keep).fillna(closes) for n in ("Open", "High", "Low"))
    return MarketData(closes, opens, highs, lows, tickers)


def fetch_closes(symbols: list[str], start: str, universe: dict[str, str] | None = None) -> pd.DataFrame:
    return fetch_market(symbols, start, universe).closes
