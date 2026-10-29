"""Options screener for selling cash-secured puts / covered calls (no Claude calls).

Port of GitOffMyPorch/Robinhood-options-screener, minus the Robinhood login.
- Stock list: screener_tickers.txt (seeded from Webull's most-active list).
- Prices + option chains: Yahoo Finance via yfinance (delayed), same as paper_bot.py.
- For each stock at or under --max-price, finds the out-of-the-money contract whose
  |delta| is closest to (but not above) --max-delta, like the original's 0.15 rule.

Changes vs the original: premium is the bid (what you'd actually get), puts are
measured against the cash you lock up (strike x 100), every strike is checked,
returns are also annualized, and illiquid contracts are skipped.

Usage: python screener.py --kind put --max-price 50 [--expiration 2026-11-20] [--max-delta 0.15]
"""
import argparse
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from wheel import bs_delta

HERE = os.path.dirname(os.path.abspath(__file__))
TICKERS_FILE = os.path.join(HERE, "screener_tickers.txt")
OUT_FILE = os.path.join(HERE, "SCREENER.md")
DTE_TARGET, DTE_MIN, DTE_MAX = 35, 21, 49
MAX_SPREAD_PCT = 0.40  # skip contracts whose bid/ask spread is over 40% of mid


def load_tickers(path=TICKERS_FILE):
    with open(path) as f:
        return [ln.split("#")[0].strip().upper() for ln in f if ln.split("#")[0].strip()]


def pick_expiration(available, wanted, today):
    """Exact date if listed, else the closest listed date; default ~35 days out."""
    if not available:
        return None
    if wanted:
        return min(available, key=lambda e: abs((e - wanted).days))
    window = [e for e in available if DTE_MIN <= (e - today).days <= DTE_MAX]
    return min(window or available, key=lambda e: abs((e - today).days - DTE_TARGET))


def best_contract(chain, spot, kind, years, max_delta):
    """OTM, liquid contract with the highest |delta| that is still <= max_delta."""
    best = None
    for q in chain:
        bid, ask, iv, k = q["bid"], q["ask"], q["iv"], q["strike"]
        mid = (bid + ask) / 2
        if bid <= 0 or ask <= 0 or (ask - bid) / mid > MAX_SPREAD_PCT or not (0.03 < iv < 5):
            continue
        if (kind == "put" and k >= spot) or (kind == "call" and k <= spot):
            continue
        d = abs(bs_delta(spot, k, years, iv, kind))
        if d <= max_delta and (best is None or d > best[1]):
            best = (q, d)
    return best


def screen(data, tickers, kind, max_price, expiration, max_delta, today):
    rows, skipped = [], []
    for t in tickers:
        try:
            spot = data.price(t)
            if spot is None or spot > max_price:
                continue
            exp = pick_expiration(data.expirations(t), expiration, today)
            if not exp:
                skipped.append(f"{t}: no options")
                continue
            dte = max(1, (exp - today).days)
            hit = best_contract(data.chain(t, exp, kind), spot, kind, dte / 365, max_delta)
            if not hit:
                skipped.append(f"{t}: no liquid contract at delta <= {max_delta}")
                continue
            q, d = hit
            capital = q["strike"] if kind == "put" else spot  # per share
            ret = q["bid"] / capital
            rows.append({"ticker": t, "price": spot, "exp": exp.isoformat(), "dte": dte,
                         "strike": q["strike"], "bid": q["bid"], "ask": q["ask"],
                         "delta": d, "ret": ret, "ann": ret * 365 / dte,
                         "cash": capital * 100})
        except Exception as e:  # one bad ticker shouldn't kill the run
            skipped.append(f"{t}: error ({type(e).__name__})")
    rows.sort(key=lambda r: r["ann"], reverse=True)
    return rows, skipped


def to_markdown(rows, skipped, kind, max_price, max_delta, now):
    side = "cash-secured puts" if kind == "put" else "covered calls"
    lines = [f"# Options screener: {side}", "",
             f"Run {now:%Y-%m-%d %H:%M} ET · stocks ≤ ${max_price:g} · |delta| ≤ {max_delta} · "
             "Yahoo delayed quotes · premium = bid (last trade when the market is closed)", "",
             "| Ticker | Price | Expires | Days | Strike | Bid / Ask | Chance of assignment* | "
             "Return | Annualized | Cash needed |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['ticker']} | ${r['price']:.2f} | {r['exp']} | {r['dte']} | ${r['strike']:g} | "
                     f"${r['bid']:.2f} / ${r['ask']:.2f} | {r['delta']:.0%} | {r['ret']:.2%} | "
                     f"{r['ann']:.1%} | ${r['cash']:,.0f} |")
    if not rows:
        lines.append("| (no matches) | | | | | | | | | |")
    lines += ["", "*Delta used as a rough probability of finishing in the money, same as the original.",
              "Cash needed = strike × 100 for puts; stock price × 100 (shares you must own) for calls."]
    if skipped:
        lines += ["", f"<details><summary>Skipped ({len(skipped)})</summary>", ""]
        lines += [f"- {s}" for s in skipped] + ["", "</details>"]
    return "\n".join(lines) + "\n"


class YahooData:
    def __init__(self):
        import yfinance as yf
        self.yf, self._t = yf, {}

    def _tk(self, t):
        return self._t.setdefault(t, self.yf.Ticker(t))

    def price(self, t):
        p = self._tk(t).fast_info.get("lastPrice")
        return float(p) if p else None

    def expirations(self, t):
        return [date.fromisoformat(e) for e in self._tk(t).options]

    def chain(self, t, exp, kind):
        ch = self._tk(t).option_chain(exp.isoformat())
        df = ch.puts if kind == "put" else ch.calls
        out = []
        for r in df.itertuples():
            bid, ask = float(r.bid or 0), float(r.ask or 0)
            if bid == 0 and ask == 0:  # market closed: Yahoo zeroes quotes, use last trade
                bid = ask = float(r.lastPrice or 0)
            out.append({"strike": float(r.strike), "bid": bid, "ask": ask,
                        "iv": float(r.impliedVolatility or 0)})
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["put", "call"], default="put")
    ap.add_argument("--max-price", type=float, default=50)
    ap.add_argument("--expiration", default="", help="YYYY-MM-DD; blank = about 35 days out")
    ap.add_argument("--max-delta", type=float, default=0.15)
    a = ap.parse_args()
    now = datetime.now(ZoneInfo("America/New_York"))
    exp = date.fromisoformat(a.expiration) if a.expiration.strip() else None
    rows, skipped = screen(YahooData(), load_tickers(), a.kind, a.max_price, exp, a.max_delta, now.date())
    md = to_markdown(rows, skipped, a.kind, a.max_price, a.max_delta, now)
    with open(OUT_FILE, "w") as f:
        f.write(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    print(md)


if __name__ == "__main__":
    main()
