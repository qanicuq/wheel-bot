"""Wheel bot - PAPER trading runner (no real orders, no Claude calls).

Runs once per weekday on GitHub Actions. Uses real delayed option quotes from
Yahoo Finance (via yfinance) and simulates fills between bid and ask.
State lives in state.json; every simulated trade is appended to trades.csv;
STATUS.md is rewritten each run.

Two paper books run side by side, both starting with $200,000:
  wheel_only   - wheel on SPY, QQQ, AAPL, MSFT
  core_wheel   - 50% buy-and-hold SPY + wheel on QQQ, AAPL, MSFT
"""
import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import wheel
from wheel import SETTINGS, bs_delta, iv_rank, should_take_profit

START_CASH = 200_000
END_DATE = date(2026, 12, 11)      # paper test runs ~2 months, then stops trading
CASH_YIELD = 0.037                 # money-market yield on uncommitted cash
FILL_FRACTION = 0.25               # sell at bid + 25% of spread, buy at ask - 25%
MAX_SPREAD_PCT = 0.25              # skip contracts whose spread > 25% of mid
DTE_TARGET, DTE_MIN, DTE_MAX = 35, 21, 49
VOL_LOOKBACK, IV_PREMIUM, IV_FLOOR = 12, 1.15, 0.12
BOOKS = {
    "wheel_only": {"tickers": ["SPY", "QQQ", "AAPL", "MSFT"], "core_spy": 0.0},
    "core_wheel": {"tickers": ["QQQ", "AAPL", "MSFT"], "core_spy": 0.5},
}
HERE = os.path.dirname(os.path.abspath(__file__))
STATE, TRADES, STATUS = (os.path.join(HERE, f) for f in ("state.json", "trades.csv", "STATUS.md"))


# ---------------------------------------------------------------- market data
class YahooData:
    def __init__(self):
        import yfinance as yf
        self.yf = yf
        self._t, self._hist = {}, {}

    def ticker(self, t):
        if t not in self._t:
            self._t[t] = self.yf.Ticker(t)
        return self._t[t]

    def history(self, t):
        """Daily closes as list[(date, close)], about 2 years."""
        if t not in self._hist:
            df = self.ticker(t).history(period="2y", interval="1d", auto_adjust=False)
            self._hist[t] = [(ix.date(), float(c)) for ix, c in df["Close"].items()]
        return self._hist[t]

    def expirations(self, t):
        return [date.fromisoformat(e) for e in self.ticker(t).options]

    def chain(self, t, exp, kind):
        ch = self.ticker(t).option_chain(exp.isoformat())
        df = ch.puts if kind == "put" else ch.calls
        return [{"symbol": r.contractSymbol, "strike": float(r.strike), "bid": float(r.bid or 0),
                 "ask": float(r.ask or 0), "iv": float(r.impliedVolatility or 0)}
                for r in df.itertuples()]

    def next_earnings(self, t):
        try:
            cal = self.ticker(t).calendar
            d = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if d:
                d = d[0] if isinstance(d, (list, tuple)) else d
                return d if isinstance(d, date) else d.date()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------- helpers
def weekly_vol_series(hist):
    """Same volatility estimate the backtest used: 12-week realized vol x1.15."""
    weekly, last_week = [], None
    for d, c in hist:
        wk = d.isocalendar()[:2]
        if wk != last_week:
            weekly.append(c)
            last_week = wk
        else:
            weekly[-1] = c
    out = []
    for i in range(4, len(weekly)):
        p = weekly[max(0, i - VOL_LOOKBACK):i + 1]
        r = [math.log(b / a) for a, b in zip(p, p[1:])]
        m = sum(r) / len(r)
        sd = math.sqrt(sum((x - m) ** 2 for x in r) / max(1, len(r) - 1))
        out.append(max(IV_FLOOR, sd * math.sqrt(52) * IV_PREMIUM))
    return out


def fill_price(q, side):
    spread = q["ask"] - q["bid"]
    return q["bid"] + FILL_FRACTION * spread if side == "sell" else q["ask"] - FILL_FRACTION * spread


def liquid(q):
    mid = (q["bid"] + q["ask"]) / 2
    return q["bid"] > 0 and q["ask"] > 0 and mid > 0 and (q["ask"] - q["bid"]) / mid <= MAX_SPREAD_PCT


def pick_expiration(data, t, today):
    exps = [e for e in data.expirations(t) if DTE_MIN <= (e - today).days <= DTE_MAX]
    return min(exps, key=lambda e: abs((e - today).days - DTE_TARGET)) if exps else None


def choose_contract(data, t, today, spot, kind, target_delta, min_strike=0.0):
    """Highest-premium contract at or below the target |delta| (further OTM side)."""
    exp = pick_expiration(data, t, today)
    if not exp:
        return None, "no expiration 21-49 days out"
    years = (exp - today).days / 365
    best = None
    for q in data.chain(t, exp, kind):
        if not liquid(q) or not (0.03 < q["iv"] < 3):
            continue
        if kind == "put" and q["strike"] >= spot:
            continue
        if kind == "call" and (q["strike"] <= spot or q["strike"] < min_strike):
            continue
        d = abs(bs_delta(spot, q["strike"], years, q["iv"], kind))
        if d <= target_delta and (best is None or d > best[1]):
            best = (q, d)
    if not best:
        return None, "no liquid contract at target delta"
    q, d = best
    return {"symbol": q["symbol"], "kind": kind, "strike": q["strike"], "exp": exp.isoformat(),
            "prem": round(fill_price(q, "sell"), 2), "delta": round(d, 3)}, "ok"


def quote_for(data, t, opt):
    for q in data.chain(t, date.fromisoformat(opt["exp"]), opt["kind"]):
        if q["symbol"] == opt["symbol"]:
            return q
    return None


def close_on_or_before(hist, d):
    vals = [c for dd, c in hist if dd <= d]
    return vals[-1] if vals else hist[-1][1]


# ---------------------------------------------------------------- one run
def new_state(today):
    return {"start": today.isoformat(), "last_run": None, "books": {
        name: {"cash": START_CASH, "core_shares": 0.0, "core_cost": 0.0,
               "positions": {t: {"shares": 0, "basis": 0.0, "opt": None} for t in cfg["tickers"]}}
        for name, cfg in BOOKS.items()}}


def run(data, today, state, log):
    fee = SETTINGS["fee_per_contract"]
    spot = {t: data.history(t)[-1][1] for t in ["SPY", "QQQ", "AAPL", "MSFT"]}
    last_run = date.fromisoformat(state["last_run"]) if state["last_run"] else None
    notes = []

    for name, cfg in BOOKS.items():
        b = state["books"][name]
        pos = b["positions"]

        # money-market interest on cash that isn't securing a put
        reserved = sum(p["opt"]["strike"] * 100 for p in pos.values() if p["opt"] and p["opt"]["kind"] == "put")
        if last_run:
            days = (today - last_run).days
            b["cash"] += max(0.0, b["cash"] - reserved) * CASH_YIELD * days / 365

        # first run: buy the SPY core
        if cfg["core_spy"] and b["core_shares"] == 0:
            amt = START_CASH * cfg["core_spy"]
            b["core_shares"] = round(amt / spot["SPY"], 4)
            b["core_cost"] = amt
            b["cash"] -= amt
            log(name, "SPY", "BUY CORE SHARES", b["core_shares"], spot["SPY"], "", -amt)

        # 1) expirations and take-profits
        for t, p in pos.items():
            o = p["opt"]
            if not o:
                continue
            exp = date.fromisoformat(o["exp"])
            if today >= exp:
                px = close_on_or_before(data.history(t), exp)
                p["opt"] = None
                intrinsic = max(0.0, o["strike"] - px) if o["kind"] == "put" else max(0.0, px - o["strike"])
                b.setdefault("closed_pl", []).append(round((o["prem"] - intrinsic) * 100 - fee, 2))
                if o["kind"] == "put" and px < o["strike"]:
                    b["cash"] -= o["strike"] * 100
                    p["shares"], p["basis"] = 100, o["strike"] - o["prem"]
                    log(name, t, "PUT ASSIGNED", o["strike"], px, o["exp"], -o["strike"] * 100)
                elif o["kind"] == "call" and px > o["strike"]:
                    b["cash"] += o["strike"] * 100
                    p["shares"], p["basis"] = 0, 0.0
                    log(name, t, "CALLED AWAY", o["strike"], px, o["exp"], o["strike"] * 100)
                else:
                    log(name, t, f"{o['kind'].upper()} EXPIRED WORTHLESS", o["strike"], px, o["exp"], 0)
                continue
            q = quote_for(data, t, o)
            if q and q["ask"] > 0:
                cost = fill_price(q, "buy")
                if should_take_profit(o["prem"], cost):
                    b["cash"] -= cost * 100 + fee
                    if o["kind"] == "call":
                        p["basis"] += cost
                    p["opt"] = None
                    b.setdefault("closed_pl", []).append(round((o["prem"] - cost) * 100 - 2 * fee, 2))
                    log(name, t, f"BUY BACK {o['kind'].upper()} (50% profit)", o["strike"], spot[t], o["exp"],
                        -(cost * 100 + fee))

        acct = b["cash"] + b["core_shares"] * spot["SPY"] + sum(p["shares"] * spot[t] for t, p in pos.items())
        reserved = sum(p["opt"]["strike"] * 100 for p in pos.values() if p["opt"] and p["opt"]["kind"] == "put")

        # 2) open new options
        for t, p in pos.items():
            if p["opt"] or today > END_DATE:
                continue
            hist = data.history(t)
            exp = pick_expiration(data, t, today)
            if t in SETTINGS["has_earnings"] and exp:
                er = data.next_earnings(t)
                blocked = (today <= er <= exp) if er else \
                    wheel.spans_earnings(t, [today + timedelta(days=k) for k in range((exp - today).days + 1)])
                if blocked:
                    src = f"earnings {er} (Yahoo)" if er else "earnings date unknown, used approximate window"
                    notes.append(f"{name}/{t}: skipped, {src} is before expiration {exp}")
                    continue
            if p["shares"] == 0:
                vols = weekly_vol_series(hist)
                ivr = iv_rank(vols[-1], vols[-52:])
                five_day = hist[-1][1] / hist[-6][1] - 1
                if five_day <= SETTINGS["crash_filter_pct"]:
                    notes.append(f"{name}/{t}: skipped, crash filter"); continue
                if ivr < SETTINGS["min_iv_rank"]:
                    notes.append(f"{name}/{t}: skipped, IV rank {ivr:.0%}"); continue
                c, why = choose_contract(data, t, today, spot[t], "put", SETTINGS["put_delta"])
                if not c:
                    notes.append(f"{name}/{t}: {why}"); continue
                need = c["strike"] * 100
                if need > b["cash"] - reserved or need > SETTINGS["max_ticker_pct"] * acct:
                    notes.append(f"{name}/{t}: not enough free cash for ${need:,.0f}"); continue
                if c["prem"] < SETTINGS["min_put_premium_pct"] * c["strike"]:
                    notes.append(f"{name}/{t}: premium too low"); continue
                reserved += need
            else:
                sma50 = sum(c for _, c in hist[-50:]) / 50
                step = SETTINGS["strike_step"][t]
                min_k = math.ceil(p["basis"] / step) * step
                c, why = choose_contract(data, t, today, spot[t], "call",
                                         wheel.call_delta(spot[t], sma50), min_strike=min_k)
                if not c or c["prem"] < SETTINGS["min_call_premium"]:
                    notes.append(f"{name}/{t}: no covered call ({why})"); continue
                p["basis"] -= c["prem"]
            b["cash"] += c["prem"] * 100 - fee
            p["opt"] = c
            log(name, t, f"SELL {c['kind'].upper()} (delta {c['delta']})", c["strike"], spot[t], c["exp"],
                c["prem"] * 100 - fee)

    state["last_run"] = today.isoformat()
    return notes, spot


# ---------------------------------------------------------------- reporting
def book_value(data, b, spot, today):
    liab = 0.0
    for t, p in b["positions"].items():
        o = p["opt"]
        if o and date.fromisoformat(o["exp"]) > today:
            q = quote_for(data, t, o)
            if q and q["ask"] > 0:
                liab += (q["bid"] + q["ask"]) / 2 * 100
    stock = b["core_shares"] * spot["SPY"] + sum(p["shares"] * spot[t] for t, p in b["positions"].items())
    return b["cash"] + stock - liab


def write_status(data, state, spot, today, notes, start_spy):
    lines = [f"# Wheel bot paper test", "",
             f"Updated {today.isoformat()} · started {state['start']} · trading stops {END_DATE.isoformat()} · "
             "simulated fills on real delayed quotes, no real orders", "",
             "| Book | Value | Return | Worst drawdown | Expectancy per option trade |", "|---|---|---|---|---|"]
    for name, b in state["books"].items():
        v = book_value(data, b, spot, today)
        b["peak"] = max(b.get("peak", START_CASH), v)
        b["max_dd_pct"] = round(max(b.get("max_dd_pct", 0.0), (b["peak"] - v) / b["peak"] * 100), 2)
        pls = b.get("closed_pl", [])
        exp_txt = f"${sum(pls) / len(pls):,.2f} ({len(pls)} closed)" if pls else "n/a (none closed yet)"
        lines.append(f"| {name} | ${v:,.0f} | {v / START_CASH - 1:+.2%} | {b['max_dd_pct']:.1f}% | {exp_txt} |")
    if start_spy:
        lines.append(f"| hold SPY (benchmark) | ${START_CASH * spot['SPY'] / start_spy:,.0f} | "
                     f"{spot['SPY'] / start_spy - 1:+.2%} | | |")
    lines += ["", "## Open positions", ""]
    for name, b in state["books"].items():
        for t, p in b["positions"].items():
            o = p["opt"]
            desc = f"{p['shares']} sh" if p["shares"] else "cash"
            if o:
                desc += f", short {o['kind']} {o['strike']:g} exp {o['exp']} (sold ${o['prem']:.2f})"
            lines.append(f"- {name} / {t}: {desc}")
    if notes:
        lines += ["", "## Today's skips", ""] + [f"- {n}" for n in notes]
    open(STATUS, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main(data=None, today=None):
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    data = data or YahooData()
    if data.history("SPY")[-1][0] != today:
        print(f"Market closed on {today}; nothing to do.")
        return
    state = json.load(open(STATE)) if os.path.exists(STATE) else new_state(today)
    if state["last_run"] == today.isoformat():
        print("Already ran today."); return
    state.setdefault("start_spy", data.history("SPY")[-1][1])

    new_file = not os.path.exists(TRADES)
    with open(TRADES, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "book", "ticker", "action", "strike_or_shares", "stock_price", "expiration", "cash_flow"])

        def log(book, t, action, k, px, exp, flow):
            w.writerow([today.isoformat(), book, t, action, k, round(px, 2), exp, round(flow, 2)])
            print(f"{book} {t} {action} {k} @ stock {px:.2f}")

        notes, spot = run(data, today, state, log)
    json.dump(state, open(STATE, "w"), indent=1)
    write_status(data, state, spot, today, notes, state.get("start_spy"))
    json.dump(state, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
