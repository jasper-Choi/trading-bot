# Trading Company V2 — 신호 엔진 & 알고리즘 상세

---

## 1. 공통 기술지표

### EMA (지수이동평균)
```python
alpha = 2 / (span + 1)
ema[0] = values[0]
ema[i] = value[i] × alpha + ema[i-1] × (1 - alpha)
```

### RSI (상대강도지수, 14기간)
```python
avg_gain = 최근 14봉 상승분 평균
avg_loss = 최근 14봉 하락분 평균
RSI = 100 - (100 / (1 + avg_gain/avg_loss))
```

---

## 2. 브레이크아웃 신호 (summarize_breakout_signal)

전 데스크 공통으로 사용하는 핵심 진입 필터.

### 입력 파라미터
| 파라미터 | 크립토 | 한국주식 |
|---------|--------|---------|
| 봉 단위 | 15분봉 | 일봉 |
| breakout_period | 20봉 (=5시간) | 20봉 (=20거래일) |
| vol_surge_mult | 3.0x | 2.5x |
| rsi_min | 50.0 | 55.0 |
| rsi_max | 72.0 | 78.0 |

### 4가지 조건
```
1. 신고가 돌파:   close > max(직전 N봉 종가)
2. 거래량 서지:   현재 거래량 ≥ N기간 평균거래량 × vol_surge_mult
3. RSI 모멘텀:    rsi_min ≤ RSI(14) ≤ rsi_max
4. EMA 위 위치:   close > EMA(breakout_period)
```

### 출력
```python
{
  "breakout": bool,           # 신고가 돌파 여부
  "vol_surge": bool,          # 거래량 서지 여부
  "rsi_in_zone": bool,        # RSI 모멘텀 구간 여부
  "above_ema20": bool,        # EMA 위 여부
  "all_confirmed": bool,      # 4/4 모두 충족
  "partial_confirmed": bool,  # 3/4 이상 충족
  "confirmed_count": int,     # 충족 조건 수 (0~4)
  "breakout_score": float,    # 0.0/0.20/0.45/0.70/0.90
  "vol_ratio": float,         # 거래량 배율
  "period_high": float,       # N기간 최고가
  "last_rsi": float,          # 현재 RSI
  "reasons": list[str]        # 각 조건별 설명
}
```

---

## 3. 크립토 신호 (summarize_crypto_signal)

**입력**: 15분봉 캔들 40개 이상 필요

### 점수 구성
```
기준 = 0.5

EMA 크로스:
  +0.15  EMA(10) > EMA(30)  → 상승 구조
  -0.15  EMA(10) < EMA(30)  → 하락 구조

RSI 구간:
  +0.10  45 ≤ RSI ≤ 68     → 균형 모멘텀
  (없음) RSI < 35           → 약세
  (없음) RSI > 68           → 과열

5봉 모멘텀 (recent_change):
  +0.10  recent_change > +1.0%
  -0.10  recent_change < -1.0%

컨트롤드 브레이크아웃:
  +0.08  0.5% ≤ recent ≤ 2.4% AND burst ≤ 1.8% AND |pullback| ≤ 1.6%

과열 패널티:
  -0.10  3봉 버스트 > +2.4%
  -0.08  3봉 버스트 < -2.5%
  -0.06  4봉 변동폭 > 4.8%

브레이크아웃 오버레이:
  +0.15  4/4 confirmed (breakout_confirmed)
  +0.08  3/4 confirmed (breakout_partial)
  +0.03  2/4 confirmed
```

### 바이어스 분류
```
score ≥ 0.62 → offense
score ≤ 0.40 → defense
그 외        → balanced
```

### 출력 필드
```python
{
  "bias": str,                  # offense / balanced / defense
  "score": float,               # 0.0~1.0
  "reasons": list[str],
  "recent_change_pct": float,   # 5봉 변화율
  "burst_change_pct": float,    # 3봉 변화율
  "ema_gap_pct": float,         # EMA10 vs EMA30 갭
  "pullback_gap_pct": float,    # 종가 vs EMA10 갭
  "range_4_pct": float,         # 4봉 변동폭
  "rsi": float,
  "breakout_confirmed": bool,   # 4/4
  "breakout_partial": bool,     # ≥3/4
  "breakout_count": int,        # 충족 수
  "vol_ratio": float,           # 거래량 배율
  "breakout_score": float       # 0.0~0.90
}
```

---

## 4. 한국주식 신호 (summarize_equity_signal)

**입력**: 일봉 캔들 30개 이상 필요

### 점수 구성
```
기준 = 0.5

EMA 크로스:
  +0.16  EMA(8) > EMA(21)
  -0.16  EMA(8) < EMA(21)

RSI 구간:
  +0.08  48 ≤ RSI ≤ 67   → 건설적 모멘텀
  -0.06  RSI < 38         → 약세

6일 모멘텀 (recent_change):
  +0.12  recent_change > +2.0%
  -0.12  recent_change < -2.0%

단기 버스트 패널티:
  -0.06  3일 버스트 > +8.0%  (과열)
  -0.06  3일 버스트 < -6.0%  (급락)
```

### 바이어스 분류
```
score ≥ 0.64 → offense
score ≤ 0.40 → defense
그 외        → balanced
```

---

## 5. 갭업 후보 스코어링 (Korea Path A)

```python
# 유동성 점수
liquidity_bonus = 0.0
if volume > 20000:    liquidity_bonus = 0.10
elif volume > 8000:   liquidity_bonus = 0.07
elif volume > 3500:   liquidity_bonus = 0.04
elif volume > 1500:   liquidity_bonus = 0.01

# 과열 패널티
overheat_penalty = 0.0
if gap_pct >= 10.0:          overheat_penalty += 0.08
if rsi >= 78.0:              overheat_penalty += 0.12
if burst_change_pct >= 12.0: overheat_penalty += 0.08
if ema_gap_pct >= 12.0:      overheat_penalty += 0.06

# 최종 후보 스코어
candidate_score = (
    gap_pct × 0.022
    + liquidity_bonus
    + signal_score × 0.68
    - overheat_penalty
)
```

갭 필터 범위: **1.2% ≤ gap_pct < 12.0%**

---

## 6. 크립토 라이브 파일럿 신호 (KIS 준용)

```python
# 트리거 임계값
if lead_weight >= 0.30 and recent_change >= -0.4:
    crypto_trigger = 0.56
else:
    crypto_trigger = 0.58

# 신호 상태
distance = crypto_trigger - signal_score
if distance > 0.08:  → "waiting"
elif distance > 0:   → "arming"  (트리거 근접)
else:                → "ready"   (진입 가능)

# probe_longs 조건 (추가)
korea_signal_ready = plan["action"] in ("probe_longs", "selective_probe")
```

---

## 7. 백테스트 검증 기준 (_passes 함수)

신호 파라미터가 실전 적용 전 만족해야 하는 최소 기준:

| 지표 | 최소값 |
|------|--------|
| 승률 | ≥ 45.0% |
| 리스크/리워드 | ≥ 2.0 |
| 샤프 비율 | ≥ 1.0 |
| 최대 낙폭 | ≥ -20.0% |
| 총 수익률 | > 0% |
| 거래 수 | ≥ 20회 |

### 실제 백테스트 결과
**크립토 (coin_backtest_v5, KRW-XRP 기준):**
- 승률: 52.9%
- R/R: 2.02
- 샤프: 5.47
- 검증 결과: PASS

**한국주식 (stock_backtest_v3, 20종목 포트폴리오):**
- 승률: 59.4%
- 연간 수익률: +33.61%
- 샤프: 12.68
- 검증 결과: PASS
