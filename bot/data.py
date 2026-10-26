"""Daily price data (dividend/split adjusted closes) from Yahoo Finance."""
from __future__ import annotations

import pandas as pd


def fetch_closes(symbols: list[str], start: str) -> pd.DataFrame:
    import yfinance as yf  # imported lazily so tests run without network

    raw = yf.download(sorted(set(symbols)), start=start, auto_adjust=True,
                      progress=False, threads=False)
    if raw.empty:
        raise RuntimeError("No price data downloaded")
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    closes = closes.dropna(how="all").ffill()
    missing = [s for s in symbols if s not in closes.columns or closes[s].isna().all()]
    if missing:
        raise RuntimeError(f"Missing data for: {missing}")
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    return closes.dropna()
