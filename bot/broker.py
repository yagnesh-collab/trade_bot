"""Alpaca PAPER trading only. Live trading is intentionally not implemented.

Runs only when ENABLE_PAPER_ORDERS=true and ALPACA_KEY_ID / ALPACA_SECRET_KEY are set.
Otherwise it prints the orders it *would* place (dry run).
"""
from __future__ import annotations

import pandas as pd
import requests

from .config import env, env_flag

PAPER_URL = "https://paper-api.alpaca.markets"  # hard-coded: this module can never reach a live account


class BrokerError(RuntimeError):
    pass


def _session() -> requests.Session:
    key, secret = env("ALPACA_KEY_ID"), env("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise BrokerError("ALPACA_KEY_ID / ALPACA_SECRET_KEY not set")
    if not key.startswith("PK"):
        raise BrokerError("Refusing to run: key is not an Alpaca PAPER key (paper keys start with 'PK')")
    s = requests.Session()
    s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    return s


def _call(s: requests.Session, method: str, path: str, **kw):
    r = s.request(method, PAPER_URL + path, timeout=30, **kw)
    if r.status_code >= 400:
        # Never echo request headers (they contain the keys); response body is safe to show.
        raise BrokerError(f"Alpaca {method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else None


def plan_orders(target: pd.Series, equity: float, positions: dict[str, float], prices: dict[str, float],
                cash_buffer: float, min_trade: float) -> list[dict]:
    """Turn target weights into orders. Sells first so cash is freed before buys."""
    investable = equity * (1 - cash_buffer)
    orders = []
    for sym in sorted(set(target.index) | set(positions)):
        want = investable * float(target.get(sym, 0.0))
        have = positions.get(sym, 0.0)
        diff = want - have
        if want == 0 and have > 0:
            orders.append({"symbol": sym, "side": "sell", "close_all": True, "value": have})
        elif abs(diff) >= min_trade * equity:
            side = "buy" if diff > 0 else "sell"
            orders.append({"symbol": sym, "side": side, "notional": round(abs(diff), 2),
                           "qty": round(abs(diff) / prices[sym], 6) if side == "sell" else None})
    return sorted(orders, key=lambda o: o["side"] != "sell")


def rebalance(target: pd.Series, prices: dict[str, float], cash_buffer: float, min_trade: float) -> list[dict]:
    target = target[target > 1e-9]
    live = env_flag("ENABLE_PAPER_ORDERS")
    if not live:
        print("ENABLE_PAPER_ORDERS is not true — dry run, no orders sent.")
        positions, equity = {}, 10_000.0
    else:
        s = _session()
        acct = _call(s, "GET", "/v2/account")
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            raise BrokerError("Account is blocked")
        equity = float(acct["equity"])
        positions = {p["symbol"]: float(p["market_value"]) for p in _call(s, "GET", "/v2/positions")}

    orders = plan_orders(target, equity, positions, prices, cash_buffer, min_trade)
    for o in orders:
        print(f"{'ORDER' if live else 'DRY-RUN'}: {o['side']} {o['symbol']} "
              f"{'all' if o.get('close_all') else '$' + format(o['notional'], ',.2f')}")
        if not live:
            continue
        if o.get("close_all"):
            _call(s, "DELETE", f"/v2/positions/{o['symbol']}")
        else:
            body = {"symbol": o["symbol"], "side": o["side"], "type": "market", "time_in_force": "day"}
            if o["side"] == "buy":
                body["notional"] = str(o["notional"])
            else:
                body["qty"] = str(o["qty"])
            _call(s, "POST", "/v2/orders", json=body)
    if not orders:
        print("Portfolio already on target — no orders.")
    return orders
