"""Wheel strategy rules engine (Level 2 options, cash account) - v2.

Pure functions only, so the backtester and the live paper runner use the exact
same rules. No Claude calls anywhere: this runs as plain Python.

v2 changes:
  1. idle cash earns money-market interest (handled by the runner/backtest)
  2. covered-call delta depends on trend (0.15 above the 50-day avg, 0.25 below)
  3. puts gated by IV rank instead of a fixed 1% premium minimum
  4. in-the-money calls at expiry are rolled up and out for a net credit
  5. no new options whose life spans an earnings report (single stocks)
"""
import math
from datetime import date

# ---- Settings (edit these, not the code) --------------------------------
SETTINGS = {
    "dte_weeks": 5,               # sell options ~35 days out
    "put_delta": 0.25,
    "call_delta_down": 0.25,      # stock below its 50-day average
    "call_delta_up": 0.15,        # stock above its 50-day average: leave room to run
    "take_profit": 0.50,          # buy back once 50% of the premium is captured
    "min_iv_rank": 0.25,          # only sell puts when IV is in the top 75% of its 1-yr range
    "min_put_premium_pct": 0.004, # hard floor: never lock cash for < 0.4%
    "min_call_premium": 0.05,
    "roll_min_credit": 0.10,      # roll an ITM call only for >= $0.10/share net credit
    "roll_max_otm": 1.15,         # new strike no higher than 15% above the stock price
    "crash_filter_pct": -0.08,    # no new puts the week after an 8%+ weekly drop
    "max_ticker_pct": 0.35,       # never lock more than 35% of the account in one ticker
    "fee_per_contract": 0.65,
    "slippage": 0.03,
    "risk_free": 0.04,
    "strike_step": {"SPY": 1, "QQQ": 1, "AAPL": 2.5, "MSFT": 5},
    "has_earnings": {"AAPL", "MSFT"},
    # feature switches (all on = v2; all off = the original v1 rules)
    "use_trend_calls": False,    # tested: no effect, off
    "use_iv_rank": True,
    "use_rolling": False,        # tested: hurt in 2022, off
    "use_earnings_filter": True,
}


# ---- Black-Scholes helpers ----------------------------------------------
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, years, vol, kind, r=None):
    r = SETTINGS["risk_free"] if r is None else r
    if years <= 0:
        return max(0.0, (strike - spot) if kind == "put" else (spot - strike))
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == "call":
        return spot * _ncdf(d1) - strike * math.exp(-r * years) * _ncdf(d2)
    return strike * math.exp(-r * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def bs_delta(spot, strike, years, vol, kind, r=None):
    r = SETTINGS["risk_free"] if r is None else r
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / (vol * math.sqrt(years))
    return _ncdf(d1) if kind == "call" else _ncdf(d1) - 1


def strike_for_delta(spot, years, vol, kind, target, step):
    """Strike nearest the target |delta|, rounded further out of the money."""
    lo, hi = spot * 0.3, spot * 2.0
    for _ in range(60):
        mid = (lo + hi) / 2
        d = abs(bs_delta(spot, mid, years, vol, kind))
        if kind == "put":
            lo, hi = (mid, hi) if d < target else (lo, mid)
        else:
            lo, hi = (lo, mid) if d < target else (mid, hi)
    k = (lo + hi) / 2
    return math.floor(k / step) * step if kind == "put" else math.ceil(k / step) * step


# ---- Filters -------------------------------------------------------------
def in_earnings_window(d: date):
    """Approximate AAPL/MSFT report windows: ~Jan 22-Feb 8, Apr 22-May 8, etc."""
    if d.month in (1, 4, 7, 10) and d.day >= 22:
        return True
    return d.month in (2, 5, 8, 11) and d.day <= 8


def spans_earnings(ticker, days_covered):
    s = SETTINGS
    if not s["use_earnings_filter"] or ticker not in s["has_earnings"]:
        return False
    return any(in_earnings_window(d) for d in days_covered)


def iv_rank(current, history):
    lo, hi = min(history), max(history)
    return 0.5 if hi - lo < 1e-9 else (current - lo) / (hi - lo)


# ---- Wheel decisions ----------------------------------------------------
def pick_put(ticker, spot, vol, ivr, last_week_return, cash_available, account_value):
    """Return ((strike, premium_per_share), reason) or (None, reason)."""
    s = SETTINGS
    if last_week_return is not None and last_week_return <= s["crash_filter_pct"]:
        return None, "crash filter"
    if s["use_iv_rank"] and ivr < s["min_iv_rank"]:
        return None, "IV rank too low"
    years = s["dte_weeks"] * 7 / 365
    k = strike_for_delta(spot, years, vol, "put", s["put_delta"], s["strike_step"][ticker])
    need = k * 100
    if need > cash_available or need > s["max_ticker_pct"] * account_value:
        return None, "not enough cash / over ticker cap"
    prem = bs_price(spot, k, years, vol, "put") * (1 - s["slippage"])
    if prem < s["min_put_premium_pct"] * k:
        return None, "premium too low"
    return (k, prem), "ok"


def call_delta(spot, sma50):
    s = SETTINGS
    if s["use_trend_calls"] and spot > sma50:
        return s["call_delta_up"]
    return s["call_delta_down"]


def pick_call(ticker, spot, vol, cost_basis, sma50):
    """Covered call; strike never below adjusted cost basis."""
    s = SETTINGS
    years = s["dte_weeks"] * 7 / 365
    step = s["strike_step"][ticker]
    k = strike_for_delta(spot, years, vol, "call", call_delta(spot, sma50), step)
    k = max(k, math.ceil(cost_basis / step) * step)
    prem = bs_price(spot, k, years, vol, "call") * (1 - s["slippage"])
    if prem < s["min_call_premium"]:
        return None, "premium too low"
    return (k, prem), "ok"


def pick_roll(ticker, spot, vol, old_strike):
    """ITM call at expiry: buy it back, sell a later & higher strike for a net credit.
    Returns (new_strike, new_premium, buyback_cost) or None."""
    s = SETTINGS
    if not s["use_rolling"]:
        return None
    step = s["strike_step"][ticker]
    years = s["dte_weeks"] * 7 / 365
    buyback = max(0.0, spot - old_strike) + 0.05          # intrinsic + a little slippage
    best = None
    k = old_strike + step
    while k <= spot * s["roll_max_otm"]:
        prem = bs_price(spot, k, years, vol, "call") * (1 - s["slippage"])
        if prem - buyback >= s["roll_min_credit"]:
            best = (k, prem, buyback)                       # keep the highest strike that works
        k += step
    return best


def should_take_profit(sold_premium, current_mark):
    return current_mark <= sold_premium * (1 - SETTINGS["take_profit"])
