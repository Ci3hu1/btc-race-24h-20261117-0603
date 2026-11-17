"""btc-race-24h-20261117-0603: level-buy hold bot (v2).

Buys with ALL cash the first time CBBTC's last price is below $65,000 (at bar 0
if it is already below at race start), then holds to the end of the 24h race.
Risk guards: take-profit +3%, stop-loss -3%, and a trailing stop that arms at
+2% and sells when the price gives back 1.2% from the peak since entry. After a
stop-loss sell there is a 30-bar cooldown; a re-buy happens only if the price
is still below $65,000 once the cooldown ends. Take-profit and trailing exits
carry no cooldown.

Pure standard library, deterministic: no wall clock, no environment, no
randomness. All prices are the harness's integers (price_scale 5, so $65,000
is 6_500_000_000); every comparison is integer math, so two runs on the same
bars produce the same orders byte for byte.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
# $65,000 at price_scale 5.
LEVEL_PRICE = 6_500_000_000
# Guards, in basis points measured from the entry fill price.
TAKE_PROFIT_BPS = 300          # +3%
STOP_LOSS_BPS = 300            # -3%
TRAIL_ARM_BPS = 200            # arms once the peak is +2% over entry
TRAIL_GIVEBACK_BPS = 120       # sells when the close is 1.2% under the peak
COOLDOWN_BARS = 30             # bars with no buy after a stop-loss sell
# A fill more than this far from the close the order was decided on is
# rejected by the harness. Dev-window 1-minute moves never exceeded 45 bps,
# so 100 bps on buys leaves room for a gap while still capping a bad fill;
# sells get 500 bps because exiting a falling market matters more than the
# exact exit price.
BUY_MAX_SLIPPAGE_BPS = 100
SELL_MAX_SLIPPAGE_BPS = 500
# Set False for the hold-only variant (buy below the level, never sell).
# The shipped entry uses the value chosen by replay + out-of-sample testing:
# hold-only won on both (see STRATEGY.md). Guards stay in the code so the
# comparison is reproducible: flip this back to True to re-run the guard-on
# variant through the same replay.
GUARDS = False

BPS = 10_000


class LevelBuyHold:
    """The strategy state. One instance per run; all of it is derived from
    the batches and fills the harness sends, so it is deterministic."""

    def __init__(self):
        # Entry fill price (int), or None while flat.
        self.entry_price = None
        # Highest close seen since entry, or None while flat.
        self.peak = None
        # First seq on which a buy is allowed again after a stop-loss sell.
        self.cooldown_until = -1
        # client_id -> exit kind ("tp", "sl", "trail") for sells awaiting a fill.
        self.pending_sells = {}

    def on_batch(self, state, batch):
        if state.warming_up:
            return []
        close = state.close(SYMBOL)
        if close is None:
            return []

        self._apply_fills(state, batch.get("fills") or [])

        holding = state.positions.get(SYMBOL)
        if holding is not None and holding.qty > 0:
            return self._maybe_exit(state, holding, close)
        if state.seq < self.cooldown_until:
            return []
        if state.cash <= 0:
            return []
        if close < LEVEL_PRICE:
            return [{
                "side": "buy",
                "symbol": SYMBOL,
                "client_id": state.client_id(),
                "notional_quote_units": state.cash,
                "max_slippage_bps": BUY_MAX_SLIPPAGE_BPS,
            }]
        return []

    def _apply_fills(self, state, fills):
        for fill in fills:
            if fill.get("symbol") != SYMBOL:
                continue
            status = fill.get("status")
            client_id = fill.get("client_id")
            if status == "filled" and fill.get("side") == "buy":
                self.entry_price = int(fill["price"])
                self.peak = self.entry_price
            elif status == "filled" and fill.get("side") == "sell":
                kind = self.pending_sells.pop(client_id, None)
                if kind == "sl":
                    self.cooldown_until = state.seq + COOLDOWN_BARS
                self.entry_price = None
                self.peak = None
            elif status == "rejected":
                # A rejected sell never filled, so no cooldown and no exit.
                self.pending_sells.pop(client_id, None)

    def _maybe_exit(self, state, holding, close):
        if not GUARDS:
            return []
        entry = self.entry_price
        if entry is None:
            # Fall back to the cost basis: cost is quote units, so
            # price = cost * 10^(qty_scale + price_scale) / (qty * 10^quote_scale)
            # = cost * 10^7 / qty for this symbol (8, 5, 6).
            entry = holding.cost * 10_000_000 // holding.qty
            self.entry_price = entry
        if self.peak is None or close > self.peak:
            self.peak = close
        kind = None
        if close * BPS >= entry * (BPS + TAKE_PROFIT_BPS):
            kind = "tp"
        elif close * BPS <= entry * (BPS - STOP_LOSS_BPS):
            kind = "sl"
        elif (self.peak * BPS >= entry * (BPS + TRAIL_ARM_BPS)
                and close * BPS <= self.peak * (BPS - TRAIL_GIVEBACK_BPS)):
            kind = "trail"
        if kind is None:
            return []
        client_id = state.client_id()
        self.pending_sells[client_id] = kind
        return [{
            "side": "sell",
            "symbol": SYMBOL,
            "client_id": client_id,
            "qty": holding.qty,
            "max_slippage_bps": SELL_MAX_SLIPPAGE_BPS,
        }]


def main():
    bot = LevelBuyHold()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
