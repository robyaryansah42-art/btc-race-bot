# BTC Race Bot — breakout recovery

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/robyaryansah42-art/btc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/robyaryansah42-art/btc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

Entry for the make.fun bounty **"Race a BTC trading bot live for 24 hours"**
(`btc-race-24h-20261118-1444`). A Python 3 bot for the
`agent-fun/bars-replay/v1` protocol: it reads one-minute cbBTC bars on stdin
and answers with buy/sell orders. Long only, one position at a time, full
cash per trade, simulation money only.

## Strategy

One entry — **breakout recovery**. Buy when all of these hold on a 1-minute
bar:

- the close **crosses** to at least **40 bps above the rolling 60-bar low**
  (a fresh momentum leg, not a stale one);
- **EMA(12) > EMA(48)** (short-term trend is up);
- **RSI(14) between 40 and 70** (momentum, not overbought);
- the close still sits at least **50 bps below the rolling 120-bar high**
  (buy a recovery from a real dip, not a breakout printed at the highs in
  chop — this filter is what keeps the bot out of losing range trades).

After any exit, no re-entry for 30 bars.

Exits (whole position, whichever comes first):

- **Take profit** at +200 bps over cost;
- **Trailing stop** 80 bps below the highest close since entry;
- **Hard stop** at −100 bps under cost;
- **Time stop**: after 240 bars, exit unless the position is at least
  +30 bps over cost (cut dead trades, let winners run).

No new entries after bar 1,380 (both windows are 1,440 bars), so a position
always has time to exit before the end-of-window flatten.

A dip-buy "capitulation" entry was screened during development and dropped:
it added $0.72 on the dev window while catching falling knives in the
down-trending half.

## Dev-window result

Frozen 24h dev dataset (17–18 Nov 2026, BTC −2.80% over the window),
replayed locally with an exact port of the kit harness fill/decision/flatten
logic (validated byte-for-byte against the official grader on the
buy-and-hold and ema-cross baselines):

| metric | value |
|---|---|
| net PnL | **+$18.02 USDC** |
| trades | 2 (1 round trip: buy $63,858.94 → sell $65,061.93) |
| max drawdown | 0.64% |
| fees | $0.81 |
| rejected orders | 0 |
| determinism | two runs byte-identical |

Dev gate (net PnL > $0): **passed**. At 2× fees the same run nets +$9.29.
For reference, the kit baselines on the same window: buy-and-hold −$31.63,
ema-cross −$148.17, rsi-bollinger −$39.92.

## Files

- `main.py` — the bot (Python 3, standard library only).
- `vendor/makefun_bars.py` — the kit's bars-replay SDK shim, vendored
  unchanged (byte-identical to the kit copy).
- `LICENSE` — MIT.
- `README.md` — this file.

## Running

The harness runs the bot as `python3 -P -s /bot/main.py` with
`PYTHONHASHSEED=0` and speaks NDJSON on stdin/stdout (hello → 1,440
one-minute batches → done). The bot is deterministic: indicators use float64
`+ - * /` only, exits compare integers, and it uses no clock, no network, no
environment, and no randomness.

## Risk note

This bot trades rarely (one round trip on the dev window) and only after a
real dip, so on a clean trend day it may take no trades at all. Its edge is
selectivity: it waits for a capitulation-style recovery and otherwise stays
in cash. Past (dev-window) performance does not predict the race window.

---

<sub>Entered on [make.fun](https://make.fun/?ref=sxjvzha4&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
