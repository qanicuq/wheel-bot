# Wheel bot paper test

Updated 2026-11-10 · started 2026-10-12 · trading stops 2026-12-11 · simulated fills on real delayed quotes, no real orders

| Book | Value | Return | Worst drawdown | Expectancy per option trade |
|---|---|---|---|---|
| wheel_only | $198,613 | -0.69% | 1.5% | n/a (none closed yet) |
| core_wheel | $194,471 | -2.76% | 3.9% | n/a (none closed yet) |
| hold SPY (benchmark) | $192,152 | -3.92% | | |

## Open positions

- wheel_only / SPY: cash
- wheel_only / QQQ: cash, short put 697 exp 2026-11-20 (sold $9.07)
- wheel_only / AAPL: cash
- wheel_only / MSFT: cash
- core_wheel / QQQ: cash, short put 696 exp 2026-11-20 (sold $8.96)
- core_wheel / AAPL: cash
- core_wheel / MSFT: cash

## Today's skips

- wheel_only/SPY: skipped, IV rank 0%
- wheel_only/AAPL: skipped, IV rank 0%
- wheel_only/MSFT: skipped, IV rank 0%
- core_wheel/AAPL: skipped, IV rank 0%
- core_wheel/MSFT: skipped, IV rank 0%
