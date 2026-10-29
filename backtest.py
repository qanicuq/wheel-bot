"""Weekly backtest of the wheel bot (v1 vs v2) over three periods.

Option prices are MODELED (Black-Scholes on trailing realized volatility x1.15),
not real historical quotes. See README for what that means for the results.
"""
import json
import math
from datetime import date, timedelta

import wheel
from wheel import (SETTINGS, bs_price, pick_call, pick_put, pick_roll,
                   should_take_profit, spans_earnings, iv_rank)

START_CASH = 200_000
VOL_LOOKBACK = 12
IV_PREMIUM = 1.15
IV_FLOOR = 0.12
# Approximate average 3-month T-bill / money-market yields by year
CASH_YIELD = {2021: 0.0005, 2022: 0.02, 2023: 0.05, 2024: 0.05, 2025: 0.041, 2026: 0.037}

# ---- data: Jan 2021 - Oct 2026 weekly closes from Webull --------------------
from data_weekly import CLOSES, END
_old = json.load(open("hist_2021_2024.json"))
TICKERS = list(CLOSES)
PX = {t: [c for _, c in _old[t]] + list(reversed(CLOSES[t])) for t in TICKERS}
DATES = [date.fromisoformat(d) for d, _ in _old["SPY"]]
end = date.fromisoformat(END)
DATES += [end - timedelta(weeks=105 - i) for i in range(106)]
N = len(DATES)
assert all(len(v) == N for v in PX.values())

VOL = {t: [None] * N for t in TICKERS}
for t in TICKERS:
    for i in range(4, N):
        p = PX[t][max(0, i - VOL_LOOKBACK):i + 1]
        r = [math.log(b / a) for a, b in zip(p, p[1:])]
        m = sum(r) / len(r)
        sd = math.sqrt(sum((x - m) ** 2 for x in r) / max(1, len(r) - 1))
        VOL[t][i] = max(IV_FLOOR, sd * math.sqrt(52) * IV_PREMIUM)


def idx_of(d):
    return min(range(N), key=lambda i: abs((DATES[i] - d).days))


def sma(t, i, n=10):          # 10 weeks ~ 50 trading days
    p = PX[t][max(0, i - n + 1):i + 1]
    return sum(p) / len(p)


def option_days(i, weeks):
    return [DATES[i] + timedelta(days=k) for k in range(0, weeks * 7 + 1)]


def run(start, stop, overrides=None, interest=True, core_spy=0.0, wheel_tickers=None):
    saved = dict(SETTINGS)
    SETTINGS.update(overrides or {})
    try:
        return _run(idx_of(start), idx_of(stop), interest, core_spy, wheel_tickers or TICKERS)
    finally:
        SETTINGS.clear(); SETTINGS.update(saved)


def _run(i0, i1, interest, core_spy, tick):
    s, fee = SETTINGS, SETTINGS["fee_per_contract"]
    cash = START_CASH
    core_sh = 0.0
    if core_spy:
        core_sh = START_CASH * core_spy / PX["SPY"][i0]
        cash -= START_CASH * core_spy
    pos = {t: {"shares": 0, "basis": 0.0, "opt": None} for t in tick}
    trades, curve, skips = [], [], {}
    for i in range(i0, i1 + 1):
        d = DATES[i]
        reserved = sum(p["opt"]["k"] * 100 for p in pos.values() if p["opt"] and p["opt"]["kind"] == "put")
        if interest and i > i0:
            cash += max(0.0, cash - reserved) * CASH_YIELD[d.year] / 52

        # 1) manage open options
        for t in tick:
            o, px = pos[t]["opt"], PX[t][i]
            if not o:
                continue
            if i >= o["exp"]:
                pos[t]["opt"] = None
                if o["kind"] == "put" and px < o["k"]:
                    cash -= o["k"] * 100
                    pos[t]["shares"], pos[t]["basis"] = 100, o["k"] - o["prem"]
                    trades.append((str(d), t, "PUT ASSIGNED", o["k"], px))
                elif o["kind"] == "call" and px > o["k"]:
                    roll = None if spans_earnings(t, option_days(i, s["dte_weeks"])) else \
                        pick_roll(t, px, VOL[t][i], o["k"])
                    if roll:
                        k2, prem2, cost = roll
                        cash += (prem2 - cost) * 100 - 2 * fee
                        pos[t]["basis"] -= (prem2 - cost)
                        pos[t]["opt"] = {"kind": "call", "k": k2, "prem": prem2, "exp": i + s["dte_weeks"]}
                        trades.append((str(d), t, "ROLL CALL", k2, px))
                    else:
                        cash += o["k"] * 100
                        pos[t]["shares"], pos[t]["basis"] = 0, 0.0
                        trades.append((str(d), t, "CALLED AWAY", o["k"], px))
                else:
                    trades.append((str(d), t, f"{o['kind'].upper()} EXPIRED", o["k"], px))
            else:
                mark = bs_price(px, o["k"], (o["exp"] - i) * 7 / 365, VOL[t][i], o["kind"])
                if should_take_profit(o["prem"], mark):
                    cost = mark * (1 + s["slippage"])
                    cash -= cost * 100 + fee
                    if o["kind"] == "call":
                        pos[t]["basis"] += cost
                    pos[t]["opt"] = None
                    trades.append((str(d), t, f"BUY BACK {o['kind'].upper()}", o["k"], px))

        acct = cash + core_sh * PX["SPY"][i] + sum(pos[t]["shares"] * PX[t][i] for t in tick)
        reserved = sum(p["opt"]["k"] * 100 for p in pos.values() if p["opt"] and p["opt"]["kind"] == "put")

        # 2) open new options
        for t in tick:
            if pos[t]["opt"]:
                continue
            if spans_earnings(t, option_days(i, s["dte_weeks"])):
                skips["earnings window"] = skips.get("earnings window", 0) + 1
                continue
            px, iv = PX[t][i], VOL[t][i]
            if pos[t]["shares"] == 0:
                hist = [v for v in VOL[t][max(4, i - 51):i + 1] if v]
                pick, why = pick_put(t, px, iv, iv_rank(iv, hist), px / PX[t][i - 1] - 1,
                                     cash - reserved, acct)
                kind = "put"
            else:
                pick, why = pick_call(t, px, iv, pos[t]["basis"], sma(t, i))
                kind = "call"
            if not pick:
                skips[why] = skips.get(why, 0) + 1
                continue
            k, prem = pick
            cash += prem * 100 - fee
            if kind == "call":
                pos[t]["basis"] -= prem
            else:
                reserved += k * 100
            pos[t]["opt"] = {"kind": kind, "k": k, "prem": prem, "exp": i + s["dte_weeks"]}
            trades.append((str(d), t, f"SELL {kind.upper()}", k, px))

        # 3) mark to market
        liab = sum(bs_price(PX[t][i], p["opt"]["k"], max(0, p["opt"]["exp"] - i) * 7 / 365,
                            VOL[t][i], p["opt"]["kind"]) * 100 for t, p in pos.items() if p["opt"])
        eq = cash + core_sh * PX["SPY"][i] + sum(p["shares"] * PX[t][i] for t, p in pos.items()) - liab
        curve.append((str(d), round(eq, 2)))
    return {"equity": curve, "trades": trades, "skips": skips}


def stats(curve):
    vals = [v for _, v in curve]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    yrs = (len(vals) - 1) / 52
    tot = vals[-1] / vals[0] - 1
    return {"ret": round(tot * 100, 1), "cagr": round(((1 + tot) ** (1 / yrs) - 1) * 100, 1),
            "maxdd": round(mdd * 100, 1)}


def hold(tickers, start, stop):
    i0, i1 = idx_of(start), idx_of(stop)
    per = START_CASH / len(tickers)
    return [(str(DATES[i]), sum(per * PX[t][i] / PX[t][i0] for t in tickers)) for i in range(i0, i1 + 1)]


V1 = {"use_trend_calls": False, "use_iv_rank": False, "use_rolling": False,
      "use_earnings_filter": False, "min_put_premium_pct": 0.01, "call_delta_up": 0.25}

PERIODS = {
    "bear_2022": (date(2022, 1, 7), date(2022, 12, 30)),
    "full_2022_2026": (date(2022, 1, 7), date(2026, 10, 9)),
    "recent_2y": (date(2024, 11, 1), date(2026, 10, 9)),
}

if __name__ == "__main__":
    res = {}
    for name, (a, b) in PERIODS.items():
        r = {
            "v1_original": stats(run(a, b, V1, interest=False)["equity"]),
            "v2_all_changes": stats(run(a, b)["equity"]),
            "v2_50pct_SPY_core": stats(run(a, b, core_spy=0.5, wheel_tickers=["QQQ", "AAPL", "MSFT"])["equity"]),
            "hold_4": stats(hold(TICKERS, a, b)),
            "hold_SPY": stats(hold(["SPY"], a, b)),
        }
        # one-change-at-a-time attribution (vs v1 + interest)
        base_i = dict(V1)
        r["v1+interest"] = stats(run(a, b, base_i)["equity"])
        for f, extra in [("trend_calls", {"use_trend_calls": True, "call_delta_up": 0.15}),
                         ("iv_rank", {"use_iv_rank": True, "min_put_premium_pct": 0.004}),
                         ("rolling", {"use_rolling": True}),
                         ("earnings", {"use_earnings_filter": True})]:
            r[f"v1+interest+{f}"] = stats(run(a, b, {**base_i, **extra})["equity"])
        res[name] = r
    full = run(*PERIODS["full_2022_2026"])
    ev = {}
    for tr in full["trades"]:
        ev[tr[2]] = ev.get(tr[2], 0) + 1
    res["v2_full_events"], res["v2_full_skips"] = ev, full["skips"]
    print(json.dumps(res, indent=1))
    json.dump({"results": res, "v2_full_equity": full["equity"],
               "core_full_equity": run(*PERIODS["full_2022_2026"], core_spy=0.5,
                                       wheel_tickers=["QQQ", "AAPL", "MSFT"])["equity"],
               "v1_full_equity": run(*PERIODS["full_2022_2026"], V1, interest=False)["equity"],
               "hold_spy_full": hold(["SPY"], *PERIODS["full_2022_2026"]),
               "trades": full["trades"]}, open("results.json", "w"))
