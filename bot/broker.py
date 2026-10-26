"""Alpaca PAPER trading only. Live trading is intentionally not implemented.

Runs only when ENABLE_PAPER_ORDERS=true and ALPACA_KEY_ID / ALPACA_SECRET_KEY are set.
Otherwise it prints the orders it *would* place (dry run).

Protection during market hours does not depend on any workflow running: every position gets a
trailing-stop order at the broker (`protect`), which Alpaca executes itself. The `monitor` command,
run every 30 minutes while the market is open, re-creates missing stops and enforces the portfolio
circuit breaker from the account's own equity history.
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


def _open_orders(s: requests.Session, symbol: str | None = None) -> list[dict]:
    params = {"status": "open", "limit": 500}
    if symbol:
        params["symbols"] = symbol
    return _call(s, "GET", "/v2/orders", params=params) or []


def cancel_open_orders(s: requests.Session, symbol: str) -> int:
    """Cancel open orders for one symbol (a stale trailing stop must not sell what we are about to re-buy)."""
    orders = _open_orders(s, symbol)
    for o in orders:
        _call(s, "DELETE", f"/v2/orders/{o['id']}")
    return len(orders)


def needs_protection(positions: list[dict], open_orders: list[dict]) -> list[dict]:
    """Positions of at least one whole share that have no open trailing-stop sell order."""
    covered = {o.get("symbol") for o in open_orders if o.get("type") == "trailing_stop" and o.get("side") == "sell"}
    todo = []
    for p in positions:
        qty = int(float(p.get("qty", 0)))
        if qty >= 1 and p["symbol"] not in covered:
            todo.append({"symbol": p["symbol"], "qty": qty})
    return todo


def protect(s: requests.Session, stop_pct: float) -> list[dict]:
    """Place a good-till-cancelled trailing-stop sell for every unprotected position (whole shares only;
    Alpaca does not accept fractional stop orders, so a fractional remainder stays unprotected)."""
    todo = needs_protection(_call(s, "GET", "/v2/positions") or [], _open_orders(s))
    for t in todo:
        print(f"STOP: trailing-stop sell {t['qty']} {t['symbol']} at {stop_pct:.1f}% below its high")
        _call(s, "POST", "/v2/orders", json={"symbol": t["symbol"], "qty": str(t["qty"]), "side": "sell",
                                             "type": "trailing_stop", "trail_percent": f"{stop_pct:.2f}",
                                             "time_in_force": "gtc"})
    return todo


def breaker_active(equity_history: list[float], dd_limit: float, cooldown_days: int) -> bool:
    """True when, within the last `cooldown_days` daily points, equity fell `dd_limit` below its running
    peak. Same rule as the simulation; the peak restarts at the trip so trading can resume afterwards."""
    vals = [float(v) for v in equity_history if v is not None]
    peak, tripped_at = 0.0, None
    for i, v in enumerate(vals):
        peak = max(peak, v)
        if peak > 0 and v / peak - 1 < -dd_limit:
            tripped_at, peak = i, v
    return tripped_at is not None and (len(vals) - 1 - tripped_at) < cooldown_days


def equity_history(s: requests.Session) -> list[float]:
    h = _call(s, "GET", "/v2/account/portfolio/history", params={"period": "3M", "timeframe": "1D"}) or {}
    return [float(v) for v in (h.get("equity") or []) if v is not None]


def monitor(stop_pct: float, dd_limit: float | None, cooldown_days: int, target: pd.Series | None = None,
            prices: dict[str, float] | None = None, tickers: dict[str, str] | None = None,
            cash_buffer: float = 0.02, min_trade: float = 0.01) -> dict:
    """Market-hours check: re-create missing trailing stops; enforce the circuit breaker; and, when the
    account does not match the strategy's target and nothing is pending, place the missing orders (so a
    failed or skipped daily run heals itself). Reports status."""
    if not env_flag("ENABLE_PAPER_ORDERS") or not env("ALPACA_KEY_ID"):
        print("Paper orders are disabled (ENABLE_PAPER_ORDERS / keys) — nothing to monitor.")
        return {"status": "disabled"}
    s = _session()
    clock = _call(s, "GET", "/v2/clock") or {}
    acct = _call(s, "GET", "/v2/account")
    equity = float(acct["equity"])
    hist = equity_history(s) + [equity]
    drawdown = equity / max(hist) - 1 if hist and max(hist) > 0 else 0.0
    positions = _call(s, "GET", "/v2/positions") or []
    result = {"status": "ok", "market_open": bool(clock.get("is_open")), "equity": equity,
              "drawdown": drawdown, "positions": len(positions), "new_stops": 0, "breaker": False}
    if not clock.get("is_open"):
        print(f"Market closed (next open {clock.get('next_open')}). Equity ${equity:,.2f}, drawdown {drawdown:+.2%}.")
        return result
    if dd_limit is not None and positions and breaker_active(hist, dd_limit, cooldown_days):
        print(f"CIRCUIT BREAKER: equity ${equity:,.2f} is {drawdown:+.2%} from its peak — closing all positions, "
              f"{cooldown_days} trading days cooldown.")
        _call(s, "DELETE", "/v2/orders")
        _call(s, "DELETE", "/v2/positions", params={"cancel_orders": "true"})
        result.update(breaker=True, positions=0)
        return result
    if stop_pct > 0:
        result["new_stops"] = len(protect(s, stop_pct))
    result["orders"] = 0
    if target is not None and prices is not None:
        pending = [o for o in _open_orders(s) if o.get("type") != "trailing_stop"]
        if pending:
            print(f"{len(pending)} order(s) still pending — not rebalancing until they fill.")
        else:
            result["orders"] = len(rebalance(target, prices, cash_buffer, min_trade, dd_limit, cooldown_days, tickers))
    print(f"Market open. Equity ${equity:,.2f}, drawdown {drawdown:+.2%}, positions {len(positions)}, "
          f"stops added {result['new_stops']}, orders {result['orders']}.")
    return result


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


def rebalance(target: pd.Series, prices: dict[str, float], cash_buffer: float, min_trade: float,
              dd_limit: float | None = None, cooldown_days: int = 20,
              tickers: dict[str, str] | None = None) -> list[dict]:
    """Bring the paper account to the target weights. Idempotent: the orders are the difference between
    what the account holds and what the strategy wants, so running it twice places nothing the second time.
    `tickers` maps canonical symbols to the broker's tickers."""
    tickers = tickers or {}
    to_ticker = lambda sym: tickers.get(sym, sym)
    to_symbol = {v: k for k, v in tickers.items()}
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
        positions = {to_symbol.get(p["symbol"], p["symbol"]): float(p["market_value"])
                     for p in _call(s, "GET", "/v2/positions")}
        if dd_limit is not None and breaker_active(equity_history(s) + [equity], dd_limit, cooldown_days):
            print(f"Circuit breaker cooldown is active — holding cash instead of the signal.")
            target = target.iloc[0:0]

    orders = plan_orders(target, equity, positions, prices, cash_buffer, min_trade)
    for o in orders:
        print(f"{'ORDER' if live else 'DRY-RUN'}: {o['side']} {o['symbol']} "
              f"{'all' if o.get('close_all') else '$' + format(o['notional'], ',.2f')}")
        if not live:
            continue
        tk = to_ticker(o["symbol"])
        cancel_open_orders(s, tk)  # a stale trailing stop must not fight the new order
        if o.get("close_all"):
            _call(s, "DELETE", f"/v2/positions/{tk}")
        else:
            body = {"symbol": tk, "side": o["side"], "type": "market", "time_in_force": "day"}
            if o["side"] == "buy":
                body["notional"] = str(o["notional"])
            else:
                body["qty"] = str(o["qty"])
            _call(s, "POST", "/v2/orders", json=body)
    if not orders:
        print("Portfolio already on target — no orders.")
    return orders
