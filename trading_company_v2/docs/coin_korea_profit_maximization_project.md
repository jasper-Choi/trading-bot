# Coin & Korea Profit Maximization Project

## Mission

Maximize profit through real-time, objective trading decisions in:

- Upbit crypto
- Korea equities

The value of the system is not "being careful." The value is using agents to
respond faster and more coldly than a human during volatile, high-opportunity
moments.

## Current alpha direction

### Crypto

- Primary style: short-term swing
- Target condition: volatility breakout with real momentum
- Current validated leaders from backtest:
  - `KRW-DOGE`
  - `KRW-XRP`

### Korea equities

- Primary style: opening-drive continuation
- Target condition: gap + liquidity + follow-through
- Current state:
  - data path works
  - statistical sample is still too small
  - research stage is not complete yet

## Priority order

1. Define the edge
2. Validate it with costs included
3. Execute it accurately
4. Limit damage when wrong

## What has already changed

- early winner-cut logic removed from paper position sync
- swing-style target / stop / hold windows expanded
- execution expected PnL aligned to backtest-scale targets
- crypto recommendation thresholds moved closer to validated breakout logic

## Current blockers

### Crypto

- Too many stop-outs after breakout entry
- Need entry refinement so DOGE/XRP expectancy improves without killing trade count

### Korea equities

- Current backtest rules produce too few trades
- Need broader universe and cleaner opening-drive trigger definition

## Next work

1. Reweight crypto emphasis directly from `coin_result_v5.json`
2. Build a wider Korea research universe
3. Redesign Korea backtest until trade count is statistically useful
4. Only then transplant the validated Korea rules into live recommendation logic
