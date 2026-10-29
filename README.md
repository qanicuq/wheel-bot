# Wheel Bot v2 (Level 2 options, cash account)

Sells cash-secured puts; if assigned, sells covered calls until called away. Plain Python, no Claude calls.

- `wheel.py` – rules engine + all settings (feature switches at the bottom of SETTINGS)
- `backtest.py` – weekly backtest, v1 vs v2, three periods, $200k start
- `data_weekly.py`, `hist_2021_2024.json` – weekly closes from Webull, Jan 2021 – Oct 2026

## v2 changes and what the tests showed
| Change | Kept? | Why |
|---|---|---|
| Idle cash earns money-market yield | Yes | Free return on unused cash |
| IV rank gate replaces fixed 1% premium rule | Yes | Helped in full and recent periods, tiny cost in 2022 |
| Skip earnings windows (AAPL, MSFT) | Yes | Helped in all three periods |
| Optional 50% SPY core + wheel on QQQ/AAPL/MSFT | Optional | Best total return; bigger drawdowns |
| Trend-based call delta (0.15 in uptrends) | Off | No measurable effect |
| Rolling ITM calls | Off | Hurt in 2022, neutral elsewhere |
| Daily checks | Not tested | Needs daily option data; backtest is weekly |

## Results (total return / worst drop)
| | 2022 bear | Jan 2022 – Oct 2026 | Last 2 yrs |
|---|---|---|---|
| v1 original | −0.9% / −8.2% | +30.6% / −8.2% | +18.1% / −4.6% |
| v2 wheel only | −0.6% / −7.0% | +63.4% / −8.6% | +38.1% / −10.8% |
| v2 + 50% SPY core | −10.1% / −14.4% | +85.5% / −15.1% | +40.0% / −13.4% |
| Hold SPY | −16.6% / −22.5% | +78.0% / −22.5% | +39.5% / −16.9% |
| Hold the 4 tickers | −23.3% / −24.9% | +89.7% / −24.9% | +45.0% / −19.9% |

## Limits of this backtest
- Option prices are modeled (Black-Scholes, trailing realized vol × 1.15), not real historical quotes; put skew not modeled.
- Weekly checks only. Earnings dates are approximate calendar windows. Cash yields are approximate yearly averages.
- Settings were chosen after seeing these results, so live results will likely be somewhat worse.

## Options screener (`screener.py`)
Port of GitOffMyPorch/Robinhood-options-screener without the Robinhood login. Checks the stocks in `screener_tickers.txt` (seeded from Webull's most-active list) and, for each one under a max price, finds the put or call with delta closest to 0.15 using Yahoo delayed quotes. Run it from the Actions tab ("Options screener" → Run workflow); results land in `SCREENER.md` and the run summary. No Claude calls.
