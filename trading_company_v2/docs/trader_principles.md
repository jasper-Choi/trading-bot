# Trader Principles

This project uses stable trader principles, but now under a stricter mission:
profit maximization through real-time, objective response in volatile markets.

## Core Project Rule

The order is:

1. Define the edge
2. Validate the edge
3. Execute it well
4. Control damage when wrong

If risk logic starts blocking the edge itself, the system is misconfigured.

## International

- Paul Tudor Jones
  - Core principle: survive long enough to press the right opportunity
  - Bot translation: risk can veto, but only after a real edge has been defined and validated

- Stan Druckenmiller
  - Core principle: press when conviction is high, stay small when edge is weak
  - Bot translation: conviction-weighted sizing, not flat sizing and not fear-based throttling

- Linda Raschke
  - Core principle: trade what is happening, not what should happen
  - Bot translation: event-driven momentum and opening-drive structure must confirm before execution

- Ed Seykota
  - Core principle: system + position sizing + psychology matter more than prediction
  - Bot translation: orchestration is rules-first, with no emotional override in the code path

## Korea / Local Adaptation

- Domestic short-term trader style influence
  - Strong focus on opening drive, gap behavior, leader rotation, liquidity concentration
  - Bot translation: Korea alpha is in opening-drive continuation, not in generic conservative filtering

- Practical Korean retail/pro desk reality
  - Avoid overtrading low-liquidity names
  - Avoid holding weak positions just because of narrative
  - Respect market session timing and news clustering

## What We Keep

- Alpha-first strategy design
- Regime detection
- Conviction-weighted sizing
- Market-confirmed entries
- Fast risk-off behavior after the edge fails

## What We Avoid

- Blind martingale
- Narrative-only discretionary entries
- Risk logic that cuts winners before the strategy can play out
- Heavy server dependence
- Complex infra that is hard to move to a personal PC
