# STRATEGY.md — btc-race-24h-20261117-0603

## Idea

A level-buy hold bot (v2 of the previous race's winning shape: buy the dip
below a price level, hold to the end of the race). Bitcoin had just crashed to
~$64,500 when this competition opened, so the level is set where the market
actually is: **$65,000**.

## Rules (shipped entry: hold-only, `GUARDS = False` in `bot.py`)

- **Buy** with ALL cash the first time CBBTC's last price is below $65,000.
  If the price is already below $65,000 at race start, buy at bar 0.
- **Hold** to the end of the 24h race. No other exits in the shipped entry.
- Orders fill at the next bar's open (4 bps fee + 2 bps slippage + pool
  impact, ~11 bps on $1,000 at the dev window's median depth). Buy
  `max_slippage_bps` is 100 (dev-window 1-minute moves never exceeded 45 bps).
- At most 1 open position; long only; no leverage; no clock, no randomness,
  no environment — deterministic.

## The guard-on variant (tested, NOT shipped)

The same bot with `GUARDS = True`: take-profit +3%, stop-loss -3%, trailing
stop that arms at +2% and sells on a 1.2% giveback from the peak, and a
30-bar cooldown after a stop-loss sell (re-buy only if still below $65,000).
The guard code stays in `bot.py` behind the `GUARDS` flag so the comparison
is reproducible — flip it to `True` and re-run `replay.py`.

## Measured results

### Dev window (1,440 bars, first close $64,461.04, last $64,775.95)

| variant | net PnL | trades (fills) | fees | max drawdown | rejected |
|---|---|---|---|---|---|
| guard-on | **+$1.07** (1,067,187 micro-USDC) | 2 | $0.80 | 2.12% | 0 |
| hold-only | **+$1.07** (1,067,187 micro-USDC) | 2 | $0.80 | 2.12% | 0 |

Identical: on the dev window the guards never trigger (the price never rises
3% above entry — max +1.56% — and never falls 3% — min -0.91%), so both
variants are the same single buy-and-hold. The buy filled at $64,570.84
(bar 1 open $64,477.09 + 2 bps slippage + ~11 bps impact) for 0.01548067
CBBTC; the flatten sold at $64,691.50 against the last close $64,775.95.
Round-trip friction is ~$3.56 (fees $0.80 + slippage/impact ~$2.76) against
a +0.46% price drift (~+$4.63). Determinism: the action log is byte-identical
across two runs of each variant.

Cross-check: the kit's `grade-local --driver docker` cannot run here (no
Docker on the VM), so the replay was verified against the real harness
instead — a throwaway Node bot mirroring the hold-only logic exactly was
graded with `grade-local --driver process` (the process driver runs Node
bots): **net PnL 1,067,187 micro-USDC, max drawdown 0.021189434 — an exact
match** with the Python replay. The replay's fill math is a faithful integer
port of the harness's `barsBuyFill`/`barsSellFill`/`BarsPool`, and the unit
tests check it against independently computed exact rational arithmetic.

### Out-of-sample: ~90 days of BTCUSDT 1-minute closes (19 Aug – 16 Nov 2026)

One 24h window per calendar day (90 windows), plus a rolling check (357
windows, a new window every 6h). Fills use the same math with a fixed pool
depth (the dev window's median CBBTC depth, 880,626,445,729 raw units);
prices are Binance BTCUSDT closes, open = previous close.

Calendar-day windows:

| variant | mean PnL / window | traded windows | mean PnL on traded | best | worst |
|---|---|---|---|---|---|
| guard-on | -$0.06 | 4 | -$1.35 | +$28.84 | -$15.92 |
| hold-only | **+$0.72** | 4 | **+$16.18** | +$67.24 | -$13.35 |

Traded days (both variants): 19 Aug (+$28.84 guard / +$67.24 hold),
14 Nov (-$13.35 / -$13.35), 15 Nov (-$15.92 / +$15.80),
16 Nov (-$4.98 / -$4.98).

Rolling 6h windows: guard-on mean +$0.11/window (traded mean +$3.33, best
+$29.73, worst -$15.92); hold-only mean **+$0.65**/window (traded mean
**+$19.35**, best +$111.29, worst -$13.35). 345 of 357 windows are no-trade
for both.

**Choice: hold-only.** It beats guard-on on every aggregate in both OOS
cuts. The guards cost money in the observed regimes: the +3% take-profit
caps the strong up-days (19 Aug: +$28.84 vs +$67.24), and the -3%
stop-loss whipsaws in a falling market (15 Nov: 6 trades, -$15.92 vs
+$15.80 for holding through the same day). The guards would only pay in a
sustained >3% crash with no recovery inside the window — a regime that did
not occur in the 90-day sample.

## What the edge is and is not

- **Is:** a regime filter, not a predictor. The $65,000 level only lets the
  bot trade after a crash to that level; 86 of 90 OOS days it sits in cash
  (PnL $0). When it does trade, it is simply long BTC for the rest of the
  day, so it wins when the day drifts up from the level and loses when it
  keeps falling. The dev-window profit (+$1.07, +0.11%) is a thin +0.46%
  drift minus ~$3.56 of round-trip friction — it passes the dev gate
  (net PnL > $0) but only just.
- **Is not:** a validated strategy. There is no backtested edge over
  buy-and-hold, no signal beyond the level, and no protection against a
  further crash in the shipped entry (the stop-loss was tested and dropped
  because it lost money out-of-sample). The 90-day OOS sample has only 4
  traded days, so the variant choice rests on a small sample — it is
  consistent across both OOS cuts (calendar and rolling), but it is still
  4 days. The live race window is a new 24h that nobody has seen; the
  result is genuinely uncertain.

## Tried and dropped

- **Guard-on (TP +3% / SL -3% / trailing +2%→-1.2%, 30-bar cooldown):**
  dropped — loses to hold-only out-of-sample (see above).
- **Tighter buy slippage (50 bps):** dropped — the dev window's largest
  1-minute move was 45 bps, so 50 bps would risk rejected buys on a gap;
  100 bps leaves room while still capping a bad fill.
- **Re-buying after a take-profit exit while still below the level:** the
  spec allows it (cooldown applies only after a stop-loss), but with the
  guards off there is no exit to re-buy from; the shipped entry buys once.
