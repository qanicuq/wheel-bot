# Wheel bot paper test

Updated 2026-11-20 · started 2026-10-12 · trading stops 2026-12-11 · simulated fills on real delayed quotes, no real orders

| Book | Value | Return | Worst drawdown | Expectancy per option trade |
|---|---|---|---|---|
| wheel_only | $193,805 | -3.10% | 3.3% | $-6,033.65 (1 closed) |
| core_wheel | $184,960 | -7.52% | 7.5% | $-5,944.65 (1 closed) |
| hold SPY (benchmark) | $181,529 | -9.24% | | |

## Open positions

- wheel_only / SPY: cash, short put 695 exp 2026-12-24 (sold $9.01)
- wheel_only / QQQ: 100 sh, short call 688 exp 2026-12-24 (sold $4.25)
- wheel_only / AAPL: cash
- wheel_only / MSFT: cash
- core_wheel / QQQ: 100 sh, short call 688 exp 2026-12-24 (sold $4.25)
- core_wheel / AAPL: cash
- core_wheel / MSFT: cash

## Today's skips

- wheel_only/AAPL: skipped, IV rank 12%
- wheel_only/MSFT: skipped, IV rank 2%
- core_wheel/AAPL: skipped, IV rank 12%
- core_wheel/MSFT: skipped, IV rank 2%
