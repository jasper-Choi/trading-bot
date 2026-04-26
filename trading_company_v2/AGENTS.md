# Trading Company V2 — 에이전트 구조 상세

> 매 사이클(15분)마다 아래 에이전트들이 순서대로 실행됩니다.

---

## 에이전트 실행 순서

```
1. MarketDataAgent         시장 데이터 수집
2. MacroSentimentAgent     매크로 심리 분석
3. TrendStructureAgent     추세 구조 분석
4. StrategyAllocatorAgent  전략 자본배분 결정
5. CryptoDeskAgent         크립토 신호 생성
6. KoreaStockDeskAgent     한국주식 신호 생성
7. USStockDeskAgent        미국주식 신호 생성
8. ChiefMarketOfficerAgent 총괄 판단 (CIO)
9. RiskCommitteeAgent      리스크 위원회 심의
10. ExecutionAgent         주문 실행
11. OpsAgent               운영 모니터링
```

---

## 각 에이전트 상세

### 1. MarketDataAgent
- Upbit 전체 KRW 마켓 시세 수집
- KOSDAQ 스냅샷 (Naver Finance API)
- 미국 주요 ETF/주식 데이터 (Stooq / Yahoo Finance / AlphaVantage)
- 결과: `MarketSnapshot` (crypto_leaders, gap_candidates, us_leaders)

### 2. MacroSentimentAgent
- 뉴스 피드 + 시장 전반 심리 분석
- 출력: `macro_score` (0.0~1.0), `macro_bias` (cautious/neutral/confident)
- 스탠스 결정에 50% 가중치

### 3. TrendStructureAgent
- 리드 종목(DOGE, XRP, BTC) EMA 구조 분석
- 출력: `trend_score` (0.0~1.0), `trend_bias`
- 스탠스 결정에 50% 가중치

### 4. StrategyAllocatorAgent
- macro + trend 점수 합산 → 스탠스(OFFENSE/BALANCED/DEFENSE) 결정
- 레짐(STRESSED/RANGING/TRENDING) 결정
- 데스크별 자본 배분 비율 산출

### 5. CryptoDeskAgent
- KRW-DOGE, KRW-XRP 15분봉 40개 수집
- KRW-BTC 방향 필터 적용
- `summarize_crypto_signal()` → bias, score, breakout 필드 계산
- combined_score = signal×0.72 + direction×0.18 + weight×0.10
- 출력: lead_market, signal_score, breakout_confirmed, breakout_partial 등

### 6. KoreaStockDeskAgent
- **Path A**: KOSDAQ 상위 30종목 갭업 스캔 (장중 09:00~15:30)
- **Path B**: 워치리스트 20종목 일봉 브레이크아웃 스캔 (24시간)
- 두 경로 결과 병합 → candidate_score 정렬
- 출력: gap_candidates, breakout_confirmed_count, breakout_partial_count

### 7. USStockDeskAgent
- SPY/QQQ/NVDA/AAPL/MSFT/TSLA 등 미국 리더 스캔
- 정규장(21:30~04:00 KST) 한정 진입
- 출력: us_leaders, quality_score, active_us_count

### 8. ChiefMarketOfficerAgent (CIO)
- 전 데스크 신호 종합 판단
- 복리 모드 결정: drift_up / measured_press / press_advantage / capital_protect
- 글로벌 사이즈 멀티플라이어 조정 (0.5x ~ 1.5x+)

### 9. RiskCommitteeAgent
- `allow_new_entries` 결정 (일일 PnL -1.5% 이하 시 차단)
- 리스크 버짓 최종 확정 (레짐/노출/손실 압력 반영)
- 데스크별 / 종목별 압력 상태 평가
- 스탑 압력: medium(×0.5) / high(차단)

### 10. ExecutionAgent
- 각 데스크 추천 액션을 실제 주문으로 변환
- 데스크 오펜스 스코어 계산
- 종목별 엣지 스코어 계산
- 중복 주문 / 포지션 캡 / 노출 상한 체크
- paper → DB 기록 / live → 브로커 API 전송

### 11. OpsAgent
- 운영 이상 감지 및 알림
- 스탑 3회 이상 / 스테일 라이브 주문 / 데스크 일시중지 감지
- Telegram 알림 발송 (에러/리스크/스탈 주문)

---

## 스탠스 결정 로직

```python
combined = (macro_score + trend_score) / 2

if combined >= 0.66:
    stance = "OFFENSE"   # risk_budget = 0.7
elif combined <= 0.42:
    stance = "DEFENSE"   # risk_budget = 0.3
else:
    stance = "BALANCED"  # risk_budget = 0.5
```

---

## 레짐 결정 로직

```python
if macro_score <= 0.35:
    regime = "STRESSED"   # 전 데스크 진입 차단
elif abs(trend_score - 0.5) <= 0.08:
    regime = "RANGING"    # 브레이크아웃 임계값 표준 적용
else:
    regime = "TRENDING"   # offense 임계값 소폭 완화
```

---

## 복리 모드 (Compounding Mode)

| 모드 | 조건 | 효과 |
|------|------|------|
| `press_advantage` | 양수 PnL + 승률 양호 + 엣지 확인 | 리스크버짓 최대 0.72까지 부스트 |
| `measured_press` | 소폭 이익 + 안정적 | 중간 부스트 |
| `drift_up` | 약한 이익 | 소폭 부스트 |
| `capital_protect` | 손실 또는 엣지 약화 | 리스크버짓 0.18 상한 |

---

## 라이브 오더 보수 모드 (Conservative Mode)

라이브 미체결 주문 존재 시 자동 활성화:

| 상태 | 신규 진입 | 리스크버짓 상한 |
|------|-----------|---------------|
| 부분체결 또는 청산 대기 | 차단 | 0.15 |
| 진입 대기만 | 허용 | 0.25 |
| 정상 | 허용 | 원래 버짓 |

스테일 기준: 15분 이상 pending/partial 상태

---

## 브로커 연동

### Upbit (크립토)
- REST API: `https://api.upbit.com`
- 인증: JWT (access_key + secret_key)
- 기능: 주문 / 잔고 / 주문상태 조회

### KIS (한국투자증권, 한국주식)
- 실전 서버: `https://openapi.koreainvestment.com:9443`
- 모의 서버: `https://openapivts.koreainvestment.com:29443` (`KIS_MOCK=true`)
- 인증: OAuth2 토큰 + 해시키
- 기능: 현금매수/매도 / 잔고 / 체결내역

### 실행 모드
```
paper     → 주문 DB 기록만, 브로커 API 미호출
upbit_live → Upbit API 실제 호출
kis_live   → KIS API 실제 호출
```
