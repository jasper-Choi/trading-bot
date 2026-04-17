# Trader Principles

This v2 design uses stable principles from well-known traders rather than copying any single strategy.

## International

- Paul Tudor Jones
  - Core principle: defense first, survival first
  - Bot translation: risk agent can always veto entries and cut sizing aggressively

- Stan Druckenmiller
  - Core principle: press when conviction is high, stay small when edge is weak
  - Bot translation: stance-based sizing, not fixed sizing

- Linda Raschke
  - Core principle: trade what is happening, not what should happen
  - Bot translation: trend/structure agent must confirm price behavior before execution

- Ed Seykota
  - Core principle: system + position sizing + psychology matter more than prediction
  - Bot translation: orchestration is rules-first, no discretionary override in code path

## Korea / Local Adaptation

- Domestic short-term trader style influence
  - Strong focus on opening drive, gap behavior, leader rotation, liquidity concentration
  - Bot translation: separate market-structure logic for Korea session and U.S./crypto session

- Practical Korean retail/pro desk reality
  - Avoid overtrading low-liquidity names
  - Avoid holding weak positions just because of narrative
  - Respect market session timing and news clustering

## What We Keep

- Capital preservation
- Regime detection
- Conviction-weighted sizing
- Market-confirmed entries
- Fast risk-off behavior

## What We Avoid

- Blind martingale
- Narrative-only discretionary entries
- Heavy server dependence
- Complex infra that is hard to move to a personal PC
