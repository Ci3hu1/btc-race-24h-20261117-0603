# NOTES.md — btc-race-24h-20261117-0603

## Bounty facts

- Competition: "Race a BTC trading bot live for 24 hours"
  (https://make.fun/bounty/btc-race-24h-20261117-0603)
- Prize: **$250 USDC** total, 10 paid places — 1st $60, 2nd $40, 3rd $30,
  4th $25, 5th $20, 6th–10th $15 each. Escrow:
  HLurpQddoQvWikEyW1ZyxX2LwsWfqK2fcmJ5SQK8j97B.
- Entries open 17 Nov 2026 06:03 UTC, **close 17 Nov 2026 12:03 UTC
  (19:03 WIB)**. Race runs 17 Nov 12:18 UTC → 18 Nov 12:18 UTC.
  Results due 10 Dec 2026.
- Entry fee: **none** (`entry_fee: null` in the requirements). No
  prerequisites. Free to enter.
- Simulation only: $1,000 paper USDC against real on-chain CBBTC prices;
  no real money.
- Field size at last check (17 Nov ~14:21 WIB): **1,095 entries** on the
  dev leaderboard; top dev PnL $49.70 ("Five gated near-low arms").
- Dev gate: net PnL > $0 on the dev window to race; race guardrails are
  ≥1 trade and max drawdown ≤ 25%.
- Protocol: agent-fun/bars-replay/v1. Harness: bars-replay/v1.
  `make.fun` API was flaky during the build (503 board_pending /
  IncompleteRead); `curl` with retries worked where `urllib` did not.

## Exact entry

- Repo: https://github.com/Ci3hu1/btc-race-24h-20261117-0603
- HEAD commit: the exact SHA is recorded in the submission record (see the
  build report); this file is part of that commit.
- Entrypoint: `main.py` (packed by `makefun compress` from `bot.py`)
- Pack size: **2,299 bytes** (`main.py` sha256
  10d80e7f45b6793872a392cee3fe640608945f51985ab976736961926f209b3a;
  source `bot.py` 5,744 bytes, sha256
  de23913cfb63adff96d48d27e5c4abb502f6dc6c4301972d8cc5819f879bcd22)
- Title for submit: "Level-buy hold v2"
- Referral code in README: cc7tcnw6

## Verification results

- Unit tests: **13/13 pass** (`python3 -m unittest discover -s tests -v`).
- Dev replay (`python3 replay.py --dev`, exact integer port of the
  harness fill math): guard-on and hold-only both **+$1.07 net PnL
  (1,067,187 micro-USDC)**, 2 fills, fees $0.80, max drawdown 2.12%,
  0 rejected orders; action logs byte-identical across two runs of each
  variant (deterministic).
- Cross-check vs the real harness: `grade-local --driver docker` cannot
  run here (no Docker on the VM), so a throwaway Node bot mirroring the
  hold-only logic exactly was graded with `grade-local --driver process`:
  **net PnL 1,067,187 micro-USDC, max drawdown 0.021189434 — an exact
  match** with the Python replay. (The Node bot was a verification aid
  only; it is not part of this repo.)
- Out-of-sample (~90 days BTCUSDT 1-min closes): hold-only beats guard-on
  — calendar-day mean +$0.72/window vs -$0.06; rolling-6h mean +$0.65 vs
  +$0.11. Full numbers in STRATEGY.md. Shipped variant: **hold-only**
  (`GUARDS = False`).
- Kit check: _TBD — filled after running `makefun check`_

## Live scores (placeholders — fill after submit / race)

- Dev grade (server): _pending_
- Race result: _pending (race 17 Nov 12:18 UTC → 18 Nov 12:18 UTC)_
- Final rank / prize: _pending (results due 10 Dec 2026)_

## Submit command (DO NOT RUN without the user's explicit 'Setuju')

```
node cli/makefun.mjs submit --competition btc-race-24h-20261117-0603 \
  --repo-url https://github.com/Ci3hu1/btc-race-24h-20261117-0603 \
  --commit <HEAD sha> --title "Level-buy hold v2" \
  --bundle <transcripts.tar.gz> \
  --keypair ~/workspace/tools/makefun/makefun-keypair.json
```

Run from inside the unpacked kit directory. The bundle must cover this
build session (and the parent session that started it); attach Muse usage
with `--muse-usage` so `thinking_priced` passes. If the submit is rejected
for missing declarations, append:
`--mcp none --harness muse-app --harness-version v1 --provider meta --model muse-spark-1.3 --effort default`.
