"""Breakout-recovery bot for the make.fun BTC race (bars-replay/v1).

Long only, one position at a time, full cash per trade. A single entry:
  Breakout recovery: the close crosses to at least BRK_BPS above the rolling
  BRK_BARS low while EMA(FAST) is above EMA(SLOW), RSI(14) is between
  BRK_RSI_LO and BRK_RSI_HI, and the close still sits at least
  BRK_MIN_DIP_BPS below the rolling BRK_DIP_BARS high. In words: buy a fresh
  momentum leg that is recovering from a real dip, not a breakout printed at
  the highs in chop. (A dip-buy "capitulation" entry was screened during
  development and dropped: it added $0.72 on the dev window while catching
  falling knives in the down-trending half.)
After any exit, no re-entry for COOLDOWN_BARS bars.
  Exit (whole position): take profit at TP_BPS over cost, a trailing stop
  TRAIL_BPS below the highest close since entry, a hard stop STOP_BPS under
  cost, or a time stop: after TIME_STOP_BARS bars, exit unless the position
  is at least TIME_STOP_MIN_BPS over cost (cut dead trades, let winners run).
  No new entries after NO_ENTRY_AFTER_SEQ (both windows are 1,440 bars).

Deterministic: indicators run in float64 on + - * / alone (the same
operations the kit baselines use), exits compare integers, no clock, no
network, no environment, no randomness.

Dev-window result (frozen 24h dev dataset, local replay of the kit harness):
  net PnL +$18.02 USDC, 2 trades (1 round trip), max drawdown 0.64%,
  fees $0.81, 0 rejected orders. At 2x fees the same run nets +$9.29.
  Dev gate (net PnL > $0): PASSED.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- PARAMS (final: screened on the frozen dev window) ---
PARAMS = {
    "mode_a": 0,  # capitulation entry disabled (screened out; see docstring)
    "mode_b": 1,
    "cap_bars": 240,
    "cap_dip_bps": 150,
    "cap_rsi": 35,
    "recover_bps": 30,
    "brk_bars": 60,
    "brk_bps": 40,
    "brk_dip_bars": 120,
    "brk_min_dip_bps": 50,
    "brk_rsi_lo": 40,
    "brk_rsi_hi": 70,
    "ema_fast": 12,
    "ema_slow": 48,
    "rsi_period": 14,
    "cooldown_bars": 30,
    "tp_bps": 200,
    "trail_bps": 80,
    "stop_bps": 100,
    "time_stop_bars": 240,
    "time_stop_min_bps": 30,
    "no_entry_after_seq": 1380,
    "max_slippage_bps": 100,
}
# --- /PARAMS ---

P = PARAMS


class Ema:
    """EMA seeded as the mean of its first span of closes."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1.0)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Series:
    """Per-symbol indicator state over closes."""

    def __init__(self):
        self.closes = []
        self.prev_close = None
        self.rsi_first = []
        self.rsi_gain = None
        self.rsi_loss = None
        self.ema_fast = Ema(P["ema_fast"])
        self.ema_slow = Ema(P["ema_slow"])
        self.brk_prev = False

    def push(self, close):
        if self.prev_close is not None:
            change = close - self.prev_close
            up = change if change > 0 else 0.0
            down = -change if change < 0 else 0.0
            if self.rsi_gain is None:
                self.rsi_first.append(change)
                if len(self.rsi_first) == P["rsi_period"]:
                    self.rsi_gain = sum(x for x in self.rsi_first if x > 0) / P["rsi_period"]
                    self.rsi_loss = sum(-x for x in self.rsi_first if x < 0) / P["rsi_period"]
            else:
                n = P["rsi_period"]
                self.rsi_gain = (self.rsi_gain * (n - 1) + up) / n
                self.rsi_loss = (self.rsi_loss * (n - 1) + down) / n
        self.prev_close = close
        self.closes.append(close)
        self.ema_fast.step(close)
        self.ema_slow.step(close)

    def rsi(self):
        if self.rsi_gain is None:
            return None
        moved = self.rsi_gain + self.rsi_loss
        return 50.0 if moved == 0 else 100.0 * self.rsi_gain / moved

    def _window(self, n):
        w = self.closes[-n:]
        return w if len(w) >= n else None

    def dip_bps(self, bars):
        """Close vs the rolling high, in bps (0.0 until enough bars)."""
        w = self._window(bars)
        if w is None:
            return 0.0
        high = max(w)
        return (high - w[-1]) * 10000.0 / high if high > 0 else 0.0

    def above_low_bps(self, bars):
        """Close vs the rolling low, in bps (0.0 until enough bars)."""
        w = self._window(bars)
        if w is None:
            return 0.0
        low = min(w)
        return (w[-1] - low) * 10000.0 / low if low > 0 else 0.0

    def trend_up(self):
        f = self.ema_fast.value
        s = self.ema_slow.value
        return f is not None and s is not None and f > s


series = Series()
in_position = False
entry_cost = 0
high_since_entry = 0
held_bars = 0
cooldown_until = -1


def on_batch(state, batch):
    global in_position, entry_cost, high_since_entry, held_bars, cooldown_until
    fresh = False
    for bar in batch["bars"]:
        if bar["symbol"] == SYMBOL:
            series.push(float(bar["c"]))
            fresh = True
    if state.warming_up:
        return []
    if not fresh:
        return []

    held = state.positions.get(SYMBOL)
    close = state.close(SYMBOL)
    if close is None:
        return []
    # Track the breakout condition every bar (not only while flat), so a
    # "fresh" breakout is a real crossing rather than stale state.
    brk_now = series.above_low_bps(P["brk_bars"]) >= P["brk_bps"]
    brk_fresh = brk_now and not series.brk_prev
    series.brk_prev = brk_now

    if held is not None:
        if not in_position:
            in_position = True
            entry_cost = held.cost
            high_since_entry = close
            held_bars = 0
        else:
            held_bars += 1
            if close > high_since_entry:
                high_since_entry = close
        value = state.value(SYMBOL, held.qty)
        cost = entry_cost
        exits = False
        if value * 10000 >= cost * (10000 + P["tp_bps"]):
            exits = True  # take profit
        elif close * 10000 < high_since_entry * (10000 - P["trail_bps"]):
            exits = True  # trailing stop
        elif value * 10000 <= cost * (10000 - P["stop_bps"]):
            exits = True  # hard stop
        elif (held_bars >= P["time_stop_bars"]
                and value * 10000 < cost * (10000 + P["time_stop_min_bps"])):
            exits = True  # time stop: dead trade
        if exits:
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": P["max_slippage_bps"]}]
        return []

    # Flat: reset tracking, note an exit for the cooldown, look for an entry.
    if in_position:
        in_position = False
        cooldown_until = state.seq + P["cooldown_bars"]
    entry_cost = 0
    high_since_entry = 0
    held_bars = 0
    if state.seq >= P["no_entry_after_seq"]:
        return []
    if state.seq < cooldown_until:
        return []
    if state.cash < 1_000_000:
        return []
    rsi = series.rsi()
    if rsi is None:
        return []

    enter = False
    if P["mode_a"]:
        if (series.dip_bps(P["cap_bars"]) >= P["cap_dip_bps"]
                and rsi < P["cap_rsi"]
                and series.above_low_bps(P["brk_bars"]) >= P["recover_bps"]):
            enter = True
    if not enter and P["mode_b"]:
        if (brk_fresh and series.trend_up()
                and P["brk_rsi_lo"] <= rsi <= P["brk_rsi_hi"]
                and series.dip_bps(P["brk_dip_bars"]) >= P["brk_min_dip_bps"]):
            enter = True
    if not enter:
        return []
    return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": state.cash,
             "max_slippage_bps": P["max_slippage_bps"]}]


makefun_bars.run(on_batch)
