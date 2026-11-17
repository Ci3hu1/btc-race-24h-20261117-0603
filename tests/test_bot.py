"""Unit tests for bot.py (level-buy hold) and the replay fill math.

Run: python3 -m unittest discover -s tests -v
Pure standard library. The strategy tests drive LevelBuyHold through the
vendored shim's BarsState with hand-built batches; the fill tests check the
replay's port of the harness fill math against independently computed exact
rational arithmetic.
"""

import os
import sys
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as botmod
import replay

BPS = 10_000
LEVEL = 6_500_000_000


def hello(warmup=0):
    h = replay.make_hello()
    h["warmup_bars"] = warmup
    return h


def bar(seq, close, open_price=None, depth=880_626_445_729):
    o = close if open_price is None else open_price
    return {"symbol": "CBBTC", "t": seq * 60_000, "o": str(o),
            "h": str(max(o, close)), "l": str(min(o, close)),
            "c": str(close), "o_depth": [str(depth)],
            "c_depth": [str(depth)]}


class Harness:
    """Feeds batches to a strategy, applying fills with the replay engine."""

    def __init__(self, guards=True, warmup=0):
        botmod.GUARDS = guards
        self.strategy = botmod.LevelBuyHold()
        self.state = botmod.makefun_bars.BarsState(hello(warmup))
        self.engine = replay.Replay([])
        self.engine.bars = [{"t": i * 60_000} for i in range(10000)]
        self.engine.cash = replay.START_CAPITAL
        self.pending = []
        self.seq = -1

    def step(self, close, open_price=None, depth=880_626_445_729):
        self.seq += 1
        b = bar(self.seq, close, open_price, depth)
        fills = self.engine._resolve(
            self.pending,
            {"t": self.seq * 60_000, "o": b["o"] and int(b["o"]),
             "c": close, "o_depth": [depth], "c_depth": [depth]},
            self.seq)
        batch = {"type": "batch", "seq": self.seq, "t": self.seq * 60_000,
                 "bars": [b], "fills": fills,
                 "account": self.engine._account(close)}
        self.state.apply_batch(batch)
        orders = self.state.to_orders(self.strategy.on_batch(self.state, batch))
        self.state.record_orders(orders)
        self.pending = self.engine._decide(
            orders, self.seq, close, is_last=False)
        return orders, fills


class TestLevelBuy(unittest.TestCase):
    def test_buy_at_bar_zero_when_below_level(self):
        h = Harness(guards=False)
        orders, _ = h.step(6_446_103_527)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "buy")
        # to_orders() renders amounts as u64 decimal strings on the wire.
        self.assertEqual(int(orders[0]["notional_quote_units"]),
                         replay.START_CAPITAL)

    def test_no_buy_when_above_level(self):
        h = Harness(guards=False)
        orders, _ = h.step(6_550_000_000)
        self.assertEqual(orders, [])

    def test_no_buy_during_warmup(self):
        h = Harness(guards=False, warmup=5)
        orders, _ = h.step(6_400_000_000)
        self.assertEqual(orders, [])

    def test_hold_only_never_sells(self):
        h = Harness(guards=False)
        orders, _ = h.step(6_446_103_527)
        self.assertEqual(len(orders), 1)
        # Fill the buy at the next open, then run the price up 10%: still no sell.
        h.step(6_500_000_000)
        for close in (6_600_000_000, 6_800_000_000, 7_100_000_000):
            orders, _ = h.step(close)
            self.assertEqual(orders, [])

    def test_determinism(self):
        def run():
            h = Harness(guards=True)
            out = []
            for close in (6_446_103_527, 6_500_000_000, 6_600_000_000,
                          6_700_000_000, 6_400_000_000, 6_300_000_000):
                orders, _ = h.step(close)
                out.append([(o["side"], o.get("notional_quote_units")
                             or o.get("qty")) for o in orders])
            return out
        self.assertEqual(run(), run())


class TestGuards(unittest.TestCase):
    def _bought(self, guards=True):
        h = Harness(guards=guards)
        orders, _ = h.step(6_400_000_000)  # below level: buy
        self.assertEqual(len(orders), 1)
        h.step(6_400_000_000)  # buy fills at this bar's open
        return h

    def test_take_profit_at_plus_three_percent(self):
        h = self._bought()
        entry = h.strategy.entry_price
        self.assertIsNotNone(entry)
        # Just under +3%: no exit.
        orders, _ = h.step(entry * 10_299 // 10_000)
        self.assertEqual(orders, [])
        # Just over +3%: sell all. (Exactly entry*10300//10000 can truncate
        # to a hair under +3% in integer math, so the test steps past it.)
        orders, _ = h.step(entry * 10_301 // 10_000)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")

    def test_stop_loss_at_minus_three_percent(self):
        h = self._bought()
        entry = h.strategy.entry_price
        orders, _ = h.step(entry * 9_701 // 10_000)
        self.assertEqual(orders, [])
        orders, _ = h.step(entry * 9_700 // 10_000)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")

    def test_trailing_stop_arms_at_plus_two_gives_back_1_2(self):
        h = self._bought()
        entry = h.strategy.entry_price
        # Rise just past +2%: arms, but no sale yet.
        peak = entry * 10_201 // 10_000
        orders, _ = h.step(peak)
        self.assertEqual(orders, [])
        # Give back just over 1.2% from the peak: sell.
        orders, _ = h.step(peak * 9_879 // 10_000)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")

    def test_trailing_does_not_arm_below_plus_two(self):
        h = self._bought()
        entry = h.strategy.entry_price
        h.step(entry * 10_100 // 10_000)  # +1%
        # A 1.2% giveback from a +1% peak must NOT sell (never armed).
        orders, _ = h.step(entry * 10_100 * 9_880 // 10_000 // 10_000)
        self.assertEqual(orders, [])

    def test_cooldown_after_stop_loss(self):
        h = self._bought()
        entry = h.strategy.entry_price
        orders, _ = h.step(entry * 9_700 // 10_000)  # SL sell decided
        self.assertEqual(len(orders), 1)
        h.step(entry * 9_600 // 10_000)  # SL sell fills; cooldown starts
        self.assertGreaterEqual(h.strategy.cooldown_until, h.seq + 1)
        # Price still below the level, but inside the cooldown: no buy.
        for _ in range(10):
            orders, _ = h.step(6_300_000_000)
            self.assertEqual(orders, [])
        # After the cooldown ends: buy again below the level.
        bought = False
        for _ in range(40):
            orders, _ = h.step(6_300_000_000)
            if orders:
                bought = True
                self.assertEqual(orders[0]["side"], "buy")
                break
        self.assertTrue(bought)


class TestFillMath(unittest.TestCase):
    def test_buy_fill_matches_exact_rational_math(self):
        notional = 1_000_000_000
        open_price = 6_444_000_000
        depth = [800_000_000_000]
        f = replay.bars_buy_fill(notional, open_price, depth)
        spend = notional * (BPS - 4) // BPS
        # Single hop: M = (D + spend) / D exactly.
        m = Fraction(depth[0] + spend, depth[0])
        price = int(open_price * Fraction(BPS + 2, BPS) * m)
        qty = spend * 10 ** 13 // (price * 10 ** 6)
        self.assertEqual(f["spend"], spend)
        self.assertEqual(f["fee"], notional - spend)
        self.assertEqual(f["price"], price)
        self.assertEqual(f["qty"], qty)

    def test_sell_fill_matches_exact_rational_math(self):
        qty = 1_548_900
        open_price = 6_477_000_000
        depth = [900_000_000_000]
        f = replay.bars_sell_fill(qty, open_price, depth)
        value = qty * open_price * 10 ** 6 // 10 ** 13
        m = Fraction(depth[0] + value, depth[0])
        slipped = int(open_price * Fraction(BPS - 2, BPS) / m)
        price = max(1, slipped)
        gross = qty * price * 10 ** 6 // 10 ** 13
        proceeds = gross * (BPS - 4) // BPS
        self.assertEqual(f["price"], price)
        self.assertEqual(f["gross"], gross)
        self.assertEqual(f["proceeds"], proceeds)
        self.assertEqual(f["fee"], gross - proceeds)

    def test_buy_then_sell_round_trip_is_loss_on_flat_price(self):
        # A flat price still loses the fee + slippage + impact both ways.
        depth = [880_626_445_729]
        f = replay.bars_buy_fill(1_000_000_000, 6_446_103_527, depth)
        g = replay.bars_sell_fill(f["qty"], 6_446_103_527, depth)
        self.assertLess(g["proceeds"], 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
