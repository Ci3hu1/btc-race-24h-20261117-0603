"""Local replay for btc-race-24h-20261117-0603.

Re-implements the harness's bars fill math EXACTLY (from the kit's harness
source, sim/bars/engine.ts) in integer math, and drives the real bot
(bot.py, via the vendored shim's BarsState) over the dev window. Also runs an
out-of-sample check over ~90 days of BTCUSDT 1-minute closes.

Usage:
  python3 replay.py --dev            # dev window, both variants, twice each
  python3 replay.py --oos /tmp/oos  # 90 daily 24h windows, both variants
  python3 replay.py --dev --guards-only  # only the shipped variant

The dev replay is cross-checked against `grade-local` (the real harness):
the two must agree on net PnL, fills and trade count exactly.
"""

import argparse
import gzip
import json
import math
import os
import sys
import zipfile
from datetime import date, timedelta

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import bot as botmod

KIT = os.path.expanduser(
    "~/workspace/tools/makefun/btc-race-24h-20261117-0603-kit"
    "/btc-race-24h-20261117-0603-kit")

BPS = 10_000
USDC_DECIMALS = 6
START_CAPITAL = 1_000_000_000  # 1,000 USDC in quote units
QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
FEE_BPS = 4
SLIPPAGE_BPS = 2
MIN_NOTIONAL = 1_000_000
MAX_OPEN_POSITIONS = 1
MAX_ORDERS_PER_BAR = 16
MAX_WEIGHT_BPS = 10_000
SYMBOL = "CBBTC"
# Median CBBTC close depth on the dev window (USDC raw units per hop), used
# as the fixed pool depth for the out-of-sample windows, where no CBBTC
# depth history exists.
OOS_DEPTH = 880_626_445_729


# ---------------------------------------------------------------- fill math
# Faithful port of sim/bars/engine.ts (BarsPool, barsValue, barsBuyFill,
# barsSellFill). All integers; every division truncates toward zero, as in
# the TypeScript bigint arithmetic.

def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


class BarsPool:
    """The pool one symbol's fills walk at one open (or a flatten's close)."""

    def __init__(self, yn, yd, kn, kd, unit):
        g = _gcd(yn, yd)
        self.yn = yn // g
        self.yd = yd // g
        self.kn = kn
        self.kd = kd
        self.unit = unit

    @staticmethod
    def of(depth, quote_scale):
        if not depth or any(d < 1 for d in depth):
            raise ValueError("a fill needs a depth above 0 on every hop")
        d = [x * 10 ** quote_scale for x in depth]
        prod = 1
        for x in d:
            prod *= x
        total = 0
        for i in range(len(d)):
            other = 1
            for j, x in enumerate(d):
                if j != i:
                    other *= x
            total += other
        g = _gcd(prod, total)
        return BarsPool(prod // g, total // g,
                        (prod // g) ** 2, (total // g) ** 2,
                        10 ** USDC_DECIMALS)

    def buy(self, spend):
        yn2 = self.yn + spend * self.unit * self.yd
        return {"num": self.yn * yn2 * self.kd,
                "den": self.yd * self.yd * self.kn,
                "after": BarsPool(yn2, self.yd, self.kn, self.kd, self.unit)}

    def sell(self, value):
        t = self.kn * self.yd + value * self.unit * self.yn * self.kd
        return {"num": t * self.yd,
                "den": self.kd * self.yn * self.yn,
                "after": BarsPool(self.kn * self.yn, t, self.kn, self.kd, self.unit)}


def bars_value(qty, price, quote_scale=QUOTE_SCALE,
               qty_scale=QTY_SCALE, price_scale=PRICE_SCALE):
    return qty * price * 10 ** quote_scale // 10 ** (qty_scale + price_scale)


def bars_buy_fill(notional, open_price, pool, fee_bps=FEE_BPS,
                  slippage_bps=SLIPPAGE_BPS):
    spend = notional * (BPS - fee_bps) // BPS
    if not isinstance(pool, BarsPool):
        pool = BarsPool.of(pool, QUOTE_SCALE)
    m = pool.buy(spend)
    price = open_price * (BPS + slippage_bps) * m["num"] // (BPS * m["den"])
    qty = spend * 10 ** (QTY_SCALE + PRICE_SCALE) // (price * 10 ** QUOTE_SCALE)
    return {"price": price, "spend": spend, "fee": notional - spend,
            "qty": qty, "after": m["after"]}


def bars_sell_fill(qty, open_price, pool, fee_bps=FEE_BPS,
                   slippage_bps=SLIPPAGE_BPS):
    if not isinstance(pool, BarsPool):
        pool = BarsPool.of(pool, QUOTE_SCALE)
    m = pool.sell(bars_value(qty, open_price))
    slipped = open_price * (BPS - slippage_bps) * m["den"] // (BPS * m["num"])
    price = slipped if slipped >= 1 else 1
    gross = bars_value(qty, price)
    proceeds = gross * (BPS - fee_bps) // BPS
    return {"price": price, "gross": gross, "fee": gross - proceeds,
            "proceeds": proceeds, "after": m["after"]}


# ---------------------------------------------------------------- engine
# A minimal, faithful re-implementation of the bars engine's decide/resolve
# loop for one symbol, mirroring sim/bars/engine.ts (decide, resolve,
# stepTo, flatten). The bot only ever sends at most one order per batch, so
# the multi-order bookkeeping is kept but never exercised beyond that.

def make_hello():
    return {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": "1",
        "symbols": [{"symbol": SYMBOL,
                     "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                     "price_scale": PRICE_SCALE, "qty_scale": QTY_SCALE}],
        "capital": {"quote": "USDC", "quote_scale": QUOTE_SCALE,
                    "quote_units": str(START_CAPITAL)},
        "bar_interval": "1m",
        "first_close_ms": 0,
        "caps": {"max_open_positions": MAX_OPEN_POSITIONS,
                 "max_orders_per_bar": MAX_ORDERS_PER_BAR,
                 "max_weight_bps": MAX_WEIGHT_BPS,
                 "shorts": False, "leverage": False},
        "fees": {"fee_bps": FEE_BPS, "slippage_bps": SLIPPAGE_BPS,
                 "min_notional_quote_units": str(MIN_NOTIONAL)},
        "latency_bars": 1,
        "warmup_bars": 0,
    }


class Replay:
    def __init__(self, bars):
        # bars: list of dicts with t, o, h, l, c, o_depth, c_depth (ints).
        self.bars = bars
        self.cash = START_CAPITAL
        self.qty = 0
        self.cost = 0
        self.trades = 0
        self.fills = []
        self.equity_curve = []
        self.action_log = []
        self.used_ids = set()
        self.rejected = []

    def _value(self, qty, price):
        return bars_value(qty, price)

    def _equity(self, close):
        e = self.cash
        if self.qty > 0:
            e += self._value(self.qty, close)
        return e

    def _account(self, close):
        pos = []
        if self.qty > 0:
            pos.append({"symbol": SYMBOL, "qty": str(self.qty),
                        "cost_quote_units": str(self.cost)})
        return {"cash_quote_units": str(self.cash), "positions": pos,
                "equity_quote_units": str(self._equity(close))}

    def run(self, guards):
        botmod.GUARDS = guards
        strategy = botmod.LevelBuyHold()
        state = botmod.makefun_bars.BarsState(make_hello())
        pending = []  # orders decided on the previous batch
        n = len(self.bars)
        for k, bar in enumerate(self.bars):
            fills = self._resolve(pending, bar, k)
            close = bar["c"]
            batch = {"type": "batch", "seq": k, "t": bar["t"],
                     "bars": [{"symbol": SYMBOL, "t": bar["t"],
                               "o": str(bar["o"]), "h": str(bar["h"]),
                               "l": str(bar["l"]), "c": str(bar["c"]),
                               "o_depth": [str(d) for d in bar["o_depth"]],
                               "c_depth": [str(d) for d in bar["c_depth"]]}],
                     "fills": fills,
                     "account": self._account(close)}
            state.apply_batch(batch)
            orders = state.to_orders(strategy.on_batch(state, batch))
            self.action_log.append((k, json.dumps(orders, sort_keys=True)))
            state.record_orders(orders)
            self.equity_curve.append(self._equity(close))
            pending = self._decide(orders, k, close, is_last=(k == n - 1))
        # Orders decided on the last batch are rejected (after_end); the
        # position is flattened at the last close, no max_slippage.
        for p in pending:
            self.rejected.append({"seq": p["seq"], "reason": "after_end",
                                  "side": p["order"]["side"]})
        if self.qty > 0:
            last = self.bars[-1]
            fill = bars_sell_fill(self.qty, last["c"], last["c_depth"])
            self.cash += fill["proceeds"]
            self.trades += 1
            self.fills.append({"client_id": "_flatten_0", "symbol": SYMBOL,
                               "side": "sell", "status": "filled",
                               "reason": None, "decided_bar": last["t"],
                               "fill_bar": last["t"],
                               "price": str(fill["price"]),
                               "qty": str(self.qty),
                               "notional_quote_units": str(fill["proceeds"]),
                               "fee_quote_units": str(fill["fee"])})
            self.qty = 0
            self.cost = 0
        self.equity_curve.append(self.cash)
        return self._summary()

    def _decide(self, orders, seq, close, is_last):
        pending = []
        equity = self._equity(close)
        buys = 0
        for index, order in enumerate(orders):
            side = order["side"]
            amount = int(order["notional_quote_units"]) if side == "buy" \
                else int(order["qty"])
            reject = None
            if index >= MAX_ORDERS_PER_BAR:
                reject = "max_orders_per_bar"
            elif order["client_id"] in self.used_ids:
                reject = "duplicate_client_id"
            elif amount == 0:
                reject = "zero_amount"
            elif is_last:
                reject = "after_end"
            elif side == "buy":
                cash_left = self.cash - buys
                held_value = self._value(self.qty, close)
                if amount == 0:
                    reject = "insufficient_cash" if cash_left <= 0 else "max_weight"
                elif amount < MIN_NOTIONAL:
                    reject = "below_min_notional"
                elif amount > cash_left:
                    reject = "insufficient_cash"
                elif self.qty == 0 and 1 > MAX_OPEN_POSITIONS:
                    reject = "max_open_positions"
                elif (held_value + amount) * BPS > MAX_WEIGHT_BPS * equity:
                    reject = "max_weight"
                if reject is None:
                    buys += amount
            else:
                if amount == 0 or amount > self.qty:
                    reject = "insufficient_position"
                elif amount != self.qty and \
                        self._value(amount, close) < MIN_NOTIONAL:
                    reject = "below_min_notional"
            self.used_ids.add(order["client_id"])
            if reject is not None:
                self.rejected.append({"seq": seq, "reason": reject,
                                      "side": side})
            else:
                pending.append({"order": order, "seq": seq, "index": index,
                                "decided_bar": self.bars[seq]["t"],
                                "fill_bar": self.bars[seq]["t"] + 60_000,
                                "close": close, "amount": amount})
        return pending

    def _resolve(self, pending, bar, k):
        fills = []
        pools = {}
        for p in pending:
            order = p["order"]
            close = p["close"]
            max_slip = int(order["max_slippage_bps"])
            pool = pools.get(SYMBOL)
            if pool is None:
                pool = BarsPool.of(bar["o_depth"], QUOTE_SCALE)
            base = {"client_id": order["client_id"], "symbol": SYMBOL,
                    "side": order["side"], "decided_bar": p["decided_bar"],
                    "fill_bar": bar["t"]}
            if order["side"] == "buy":
                fill = bars_buy_fill(p["amount"], bar["o"], pool)
                if fill["price"] * BPS > close * (BPS + max_slip):
                    fills.append({**base, "status": "rejected",
                                  "reason": "max_slippage", "price": "0",
                                  "qty": "0", "notional_quote_units": "0",
                                  "fee_quote_units": "0"})
                    self.rejected.append({"seq": p["seq"],
                                          "reason": "max_slippage",
                                          "side": "buy"})
                    continue
                if fill["qty"] == 0:
                    fills.append({**base, "status": "rejected",
                                  "reason": "zero_amount", "price": "0",
                                  "qty": "0", "notional_quote_units": "0",
                                  "fee_quote_units": "0"})
                    continue
                pools[SYMBOL] = fill["after"]
                self.cash -= p["amount"]
                self.qty += fill["qty"]
                self.cost += p["amount"]
                self.trades += 1
                f = {**base, "status": "filled", "reason": None,
                     "price": str(fill["price"]),
                     "qty": str(fill["qty"]),
                     "notional_quote_units": str(p["amount"]),
                     "fee_quote_units": str(fill["fee"])}
                fills.append(f)
                self.fills.append(f)
            else:
                fill = bars_sell_fill(p["amount"], bar["o"], pool)
                if fill["price"] * BPS < close * (BPS - max_slip):
                    fills.append({**base, "status": "rejected",
                                  "reason": "max_slippage", "price": "0",
                                  "qty": "0", "notional_quote_units": "0",
                                  "fee_quote_units": "0"})
                    self.rejected.append({"seq": p["seq"],
                                          "reason": "max_slippage",
                                          "side": "sell"})
                    continue
                pools[SYMBOL] = fill["after"]
                self.cash += fill["proceeds"]
                self.cost = self.cost * (self.qty - p["amount"]) // self.qty
                self.qty -= p["amount"]
                self.trades += 1
                f = {**base, "status": "filled", "reason": None,
                     "price": str(fill["price"]),
                     "qty": str(p["amount"]),
                     "notional_quote_units": str(fill["proceeds"]),
                     "fee_quote_units": str(fill["fee"])}
                fills.append(f)
                self.fills.append(f)
        return fills

    def _summary(self):
        net = self.cash - START_CAPITAL
        peak = 0
        max_dd = 0.0
        for e in self.equity_curve:
            if e > peak:
                peak = e
            if peak > 0:
                dd = (peak - e) / peak
                if dd > max_dd:
                    max_dd = dd
        fees = sum(int(f["fee_quote_units"]) for f in self.fills)
        return {"net_pnl_quote_units": net,
                "net_pnl_usdc": net / 1_000_000,
                "final_cash_usdc": self.cash / 1_000_000,
                "trades": self.trades,
                "fills": len(self.fills),
                "fees_usdc": fees / 1_000_000,
                "max_drawdown_pct": max_dd * 100,
                "rejected": self.rejected,
                "action_log": self.action_log}


def load_dev_bars():
    path = os.path.join(KIT, "dataset", "bars", "CBBTC.ndjson.gz")
    bars = []
    with gzip.open(path, "rt") as f:
        for line in f:
            r = json.loads(line)
            bars.append({"t": int(r["t"]), "o": int(r["o"]),
                         "h": int(r["h"]), "l": int(r["l"]),
                         "c": int(r["c"]),
                         "o_depth": [int(d) for d in r["o_depth"]],
                         "c_depth": [int(d) for d in r["c_depth"]]})
    return bars


def load_oos_days(oos_dir):
    """BTCUSDT 1-minute closes per calendar day, 2026-08-19..2026-11-16."""
    days = []
    start = date(2026, 8, 19)
    for i in range(90):
        d = start + timedelta(days=i)
        zpath = os.path.join(oos_dir, f"BTCUSDT-1m-{d.isoformat()}.zip")
        closes = []
        with zipfile.ZipFile(zpath) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                for line in f:
                    parts = line.decode().strip().split(",")
                    if len(parts) < 5:
                        continue
                    try:
                        closes.append(float(parts[4]))
                    except ValueError:
                        continue
        days.append((d.isoformat(), closes))
    return days


def oos_bars(closes):
    """One synthetic bar per close: open is the previous close, depth fixed."""
    bars = []
    prev = None
    for i, c in enumerate(closes):
        price = int(round(c * 10 ** PRICE_SCALE))
        open_price = price if prev is None else prev
        bars.append({"t": i * 60_000, "o": open_price,
                     "h": max(open_price, price),
                     "l": min(open_price, price), "c": price,
                     "o_depth": [OOS_DEPTH], "c_depth": [OOS_DEPTH]})
        prev = price
    return bars


def fmt_usd(units):
    sign = "-" if units < 0 else ""
    return f"{sign}${abs(units) / 1_000_000:,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--oos", default=None)
    ap.add_argument("--guards-only", action="store_true")
    args = ap.parse_args()

    if args.dev:
        bars = load_dev_bars()
        print(f"dev window: {len(bars)} bars, "
              f"first close ${bars[0]['c'] / 1e5:,.2f}, "
              f"last close ${bars[-1]['c'] / 1e5:,.2f}")
        variants = [True] if args.guards_only else [True, False]
        for guards in variants:
            label = "guard-on (TP+3/SL-3/trail)" if guards else "hold-only"
            runs = []
            for run_no in (1, 2):
                r = Replay(bars)
                s = r.run(guards)
                runs.append(s)
                print(f"[{label}] run {run_no}: net PnL "
                      f"{fmt_usd(s['net_pnl_quote_units'])} "
                      f"({s['net_pnl_quote_units']} micro-USDC), "
                      f"trades {s['trades']}, fills {s['fills']}, "
                      f"fees {fmt_usd(int(s['fees_usdc'] * 1_000_000))}, "
                      f"max DD {s['max_drawdown_pct']:.2f}%, "
                      f"rejected {len(s['rejected'])}")
            same = runs[0]["action_log"] == runs[1]["action_log"]
            same_pnl = (runs[0]["net_pnl_quote_units"]
                        == runs[1]["net_pnl_quote_units"])
            print(f"[{label}] determinism: action logs identical: {same}, "
                  f"net PnL identical: {same_pnl}")
            if runs[0]["rejected"]:
                print(f"[{label}] rejected orders: {runs[0]['rejected']}")

    if args.oos:
        days = load_oos_days(args.oos)
        print(f"OOS: {len(days)} daily windows, "
              f"closes per day: min {min(len(c) for _, c in days)}, "
              f"max {max(len(c) for _, c in days)}")
        for guards in ([True] if args.guards_only else [True, False]):
            label = "guard-on" if guards else "hold-only"
            pnls = []
            traded = []
            for day, closes in days:
                if len(closes) < 100:
                    continue
                r = Replay(oos_bars(closes[:1440]))
                s = r.run(guards)
                pnls.append(s["net_pnl_quote_units"])
                if s["trades"] > 0:
                    traded.append((day, s["net_pnl_quote_units"],
                                   s["trades"]))
            n = len(pnls)
            mean = sum(pnls) / n
            pos = sum(1 for p in pnls if p > 0)
            neg = sum(1 for p in pnls if p < 0)
            zero = n - pos - neg
            tmean = (sum(p for _, p, _ in traded) / len(traded)
                     if traded else 0)
            print(f"[OOS {label}] windows {n}, mean PnL/window "
                  f"{fmt_usd(int(mean))}, median "
                  f"{fmt_usd(sorted(pnls)[n // 2])}, "
                  f"positive {pos}, negative {neg}, zero/no-trade {zero}")
            print(f"[OOS {label}] traded windows: {len(traded)}, "
                  f"mean PnL on traded windows {fmt_usd(int(tmean))}, "
                  f"best {fmt_usd(max(pnls))}, worst {fmt_usd(min(pnls))}")
            print(f"[OOS {label}] traded days: "
                  + ", ".join(f"{d}:{fmt_usd(p)}({t}t)"
                              for d, p, t in traded))


if __name__ == "__main__":
    main()
