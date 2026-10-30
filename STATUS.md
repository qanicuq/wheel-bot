# Wheel bot paper test

Updated 2026-10-30 · started 2026-10-12 · trading stops 2026-12-11 · simulated fills on real delayed quotes, no real orders

| Book | Value | Return | Worst drawdown | Expectancy per option trade |
|---|---|---|---|---|
| wheel_only | $199,575 | -0.21% | 0.4% | n/a (none closed yet) |
| core_wheel | $196,957 | -1.52% | 1.5% | n/a (none closed yet) |
| hold SPY (benchmark) | $195,062 | -2.47% | | |

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
- wheel_only/AAPL: skipped, earnings 2026-11-02 (Yahoo) is before expiration 2026-12-04
- wheel_only/MSFT: skipped, IV rank 12%
- core_wheel/AAPL: skipped, earnings 2026-11-02 (Yahoo) is before expiration 2026-12-04
- core_wheel/MSFT: skipped, IV rank 12%
