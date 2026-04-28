# 트레이딩 봇 전략 벤치마크 분석

작성일: 2026-04-28
작성자: Claude (Sonnet 4.6)
용도: Claude + Codex 협업 핸드오프 / 장기 개발 로드맵

---

## 1. 현재 봇의 능력 요약

### 1.1 보유 기술 스택
| 영역 | 구현 내용 |
|---|---|
| **신호 생성** | 15m + 1m 멀티 타임프레임, EMA/RSI/Breakout, Pullback Entry (Ross Cameron Holy Grail), ICT (FVG/OB/SSL Sweep/CHoCH/Kill Zone) |
| **오더북 분석** | bid/ask 비율, 스프레드, 임밸런스 (스냅샷 기반) |
| **스코어링** | combined_score = 0.38 signal + 0.26 micro + 0.18 orderbook + 0.12 direction + 0.06 weight, trend_ignition_score, pullback boost |
| **포지션 관리** | 트레일링 (3단계 tier), peak_pnl 추적, breakeven_trail, signal-based exit (CHoCH 반전 / 모멘텀 붕괴) |
| **멀티 에이전트** | MarketData / MacroSentiment / TrendStructure / StrategyAllocator / 3개 데스크 / Bull/Bear/PM 토론 / RiskCommittee / Execution / Ops |
| **실시간 운영** | Oracle VM + systemd, auto_pull cron, 18 마켓 병렬 스캔, Upbit live + paper 병행 |
| **리스크 관리** | 스탠스/리짐 결정, risk_budget 캡, fast_fail / target / stop / time_exit, 동시 포지션 한도 (4개), 노출 한도 (2.0x) |
| **백테스트** | coin_backtest_v5 (RSI 평균회귀), stock_backtest_v3 (갭 모멘텀) — 검증된 진입 로직 반영 |

### 1.2 운영 지표
- 사이클 주기: ~2분
- 응답 지연: REST API 기반 (수백 ms ~ 초 단위)
- 동시 추적 종목: 18개 (KRW market, discovery + backtest weight 가중)
- 라이브 실행: Upbit 단일 거래소, 현물만

---

## 2. 비교 대상 분류

### Tier S — 세계 최고 (Renaissance / Citadel / Jane Street / Jump / Wintermute)
- 인프라 예산 $100M+, PhD/Quant 수십~수백명
- 마이크로초 레이턴시, 자체 매칭엔진/시장조성
- ML/딥러닝 기반 시그널, 자체 데이터 인프라 (틱 단위 50년치 등)
- 시장 조성 + 스탯 아브 + 옵션 + 다자산 크로스 헤징

### Tier A — 시스테매틱 펀드 (Bridgewater / AQR / Man AHL / Two Sigma)
- 멀티 시그널 통합, 팩터 모델, HMM 리짐 디텍션
- 리스크 패리티, Kelly/CVaR 기반 사이징
- 워크포워드 검증 + 오버핏 필터링 일상화
- 1조원~수십조원 운용

### Tier B — 유명 디스크리션 트레이더 (PTJ / Druckenmiller / Raschke / Minervini / Ross Cameron)
- 휴리스틱 + 직관 (체크리스트 수준)
- 패턴 인식은 인간 두뇌 의존 (수십년 경험)
- 신호 자체는 우리 봇과 비슷하거나 단순
- 리스크 관리: 1R 기반 손절, 비중 1-2% per trade

### Tier C — 리테일 봇 (3Commas / Cryptohopper / Pionex)
- TA 단일 (RSI/MACD/Bollinger) 또는 그리드
- 오더북 분석 X, ICT X, 멀티 에이전트 X
- 사용자가 직접 파라미터 튜닝

---

## 3. 우리 봇의 강점

| # | 강점 | 비교 우위 |
|---|---|---|
| 1 | **멀티 에이전트 토론 구조** | Bull/Bear/PM 디베이트 + 리스크위원회. Tier C 봇에는 없는 구조. Tier B 트레이더의 사고 과정을 코드화. |
| 2 | **ICT + 오더북 + Pullback 통합** | FVG/OB/CHoCH/SSL Sweep/Kill Zone + bid/ask + Holy Grail 패턴이 한 시스템에 통합. Tier C 압도. |
| 3 | **15m + 1m + 오더북 멀티 레이어** | 추세는 15m, 진입 트리거는 1m, 즉시성은 오더북. 3개 시간축 동시 활용. |
| 4 | **신호 기반 즉시 청산 (signal-based exit)** | 트레일링 스톱 발동 전에 CHoCH bearish + 모멘텀 붕괴 감지 시 즉시 청산. 인간 트레이더가 캐치 못하는 순간 포착. |
| 5 | **백테스트 검증 → 실전 반영 파이프라인** | coin_backtest_v5 검증된 RSI 평균회귀 + 모멘텀 브레이크아웃 룰을 코드에 반영. 추측이 아닌 검증된 룰. |
| 6 | **24/7 무피로 가동** | 인간 트레이더의 가장 큰 약점인 피로/감정 제거. Kill Zone 시간대 자동 인식. |
| 7 | **Direct Entry + Pullback + Ignition + ICT 다중 진입 패스** | 한 가지 패턴만 보지 않고 4가지 다른 진입 트리거 동시 감시. |

---

## 4. 우리 봇의 약점 (우선순위별 분류)

### 🔴 P0 — 즉시 개선 가능, 큰 임팩트

| # | 약점 | 현 상태 | 임팩트 | 비교 |
|---|---|---|---|---|
| 1 | **슬리피지/수수료 미모델링** | Paper P&L = 가격 단순 차이. 실전 0.05% 수수료 + 슬리피지 누적 무시 | 실전 P&L이 paper보다 1-2% 낮게 나옴 | Tier A는 Almgren-Chriss 모델, 우리는 0 |
| 2 | **워크포워드 검증 부재** | 단일 기간 백테스트로 파라미터 결정. 오버핏 위험 | 라이브에서 백테스트 성과 미달성의 주원인 | Tier A 표준 |
| 3 | **고정 사이징 (0.5x/0.75x)** | 변동성 무관 동일 사이즈. ATR 기반 리스크 동등화 X | 변동성 큰 코인에서 과도 손실, 작은 코인은 과도 보수 | Tier B 디스크리셔도 1R 기반 사이징 |
| 4 | **포지션 간 상관관계 무시** | 4개 동시 포지션이 모두 BTC와 0.9 상관이면 사실상 1포지션 | 분산 효과 0, 변동성 4배 | Tier A 기본 |
| 5 | **신호 시간 감쇠 X** | 5분 전 신호와 10초 전 신호가 동일 가중 | 늦은 진입 → 추세 끝물 진입 | HFT 기본 |

### 🟡 P1 — 중기, 중요 임팩트

| # | 약점 | 현 상태 | 비교 |
|---|---|---|---|
| 6 | **틱/체결 데이터 미사용** | 캔들 + 오더북 스냅샷만. Time-and-Sales 무시 | Tier S/A는 모든 체결 분석 |
| 7 | **온체인 데이터 미통합** | 거래소 입출금, 고래 지갑 이동 모름 | Glassnode/Nansen 시그널 무시 |
| 8 | **펀딩비/베이시스 시그널 X** | 현물만 보고 perp 시장 신호 무시 | 베이시스 +/-는 강력한 방향 신호 |
| 9 | **이벤트 캘린더 X** | FOMC/CPI/BTC 반감기/거래소 상장 일정 모름 | 이벤트 전 포지션 자동 축소 불가 |
| 10 | **ML/적응형 학습 X** | 모든 룰 하드코딩. 시장 변화 시 수동 튜닝 필요 | Tier S 핵심 차별점 |
| 11 | **단일 거래소** | Upbit만. Binance/Bybit/Coinbase 가격차 활용 X | 차익거래 기회 100% 손실 |
| 12 | **숏/선물 X** | 현물 long-only. 하락장에서 무력 | 양방향 손익 기회 절반 잃음 |
| 13 | **상관 행렬 + Lead-Lag X** | 코인A가 5분 후 코인B를 견인 — 패턴 사용 못함 | Stat arb의 기본 |

### 🟢 P2 — 장기, 인프라 투자 필요

| # | 약점 | 비교 |
|---|---|---|
| 14 | **레이턴시 (분 단위)** | Tier S는 마이크로초 |
| 15 | **시장 조성 X** | 메이커 리베이트 수익 미발생 |
| 16 | **VaR/CVaR 리스크 모델 X** | 단순 스톱만. Tail risk 미정량화 |
| 17 | **HMM 리짐 모델 X** | 단순 macro+trend score. Hidden state 미감지 |
| 18 | **소셜/뉴스 센티먼트 X** | Twitter/Reddit/Telegram 시그널 무시 |
| 19 | **Smart Order Routing X** | 단일 브로커. 분할 호가 미실행 |
| 20 | **TWAP/VWAP/POV 실행 알고리즘 X** | 시장가 단일 주문. 큰 사이즈 진입 시 슬리피지 폭발 |

---

## 5. 종합 점수 (100점 만점, 가상 "이상적 트레이딩 회사" 기준)

| 항목 | 가중 | 현 점수 | 코멘트 |
|---|---|---|---|
| 신호 생성 (시그널의 다양성/품질) | 20% | 50/100 | 멀티 타임프레임 + ICT 우수, ML/order flow 부재 |
| 리스크 관리 | 20% | 30/100 | 기본 스톱/트레일은 있으나 VaR/Kelly/상관 무시 |
| 실행 (Execution) | 15% | 25/100 | REST API 시장가만. SOR/TWAP 없음 |
| 백테스팅 인프라 | 10% | 35/100 | 스크립트는 있으나 워크포워드 부재 |
| 다자산 / 거래소 커버리지 | 10% | 20/100 | 크립토 단일 거래소만 활성화 |
| 레이턴시 / 인프라 | 10% | 15/100 | Python REST, VM 운영 |
| 데이터 인프라 (대체 데이터 포함) | 10% | 25/100 | 거래소 캔들/오더북만 |
| 적응성 / ML | 5% | 10/100 | 룰 기반, 학습 없음 |
| **종합** | 100% | **~32/100** | **Tier C 압도, Tier B 디스크리셔너리 트레이더 시스템화 수준에 근접** |

### Tier별 비교 점수
- vs Tier S (Renaissance/Citadel): **8-12/100** — 다른 스포츠
- vs Tier A (AQR/Bridgewater): **25-30/100** — 같은 접근, 정교함 격차
- vs Tier B (PTJ/Druckenmiller 시스템): **65-75/100** — 시그널 다양성은 우위, 경험 직관 부재
- vs Tier C (3Commas/Cryptohopper): **220-280/100** — 압도

---

## 6. 개선 로드맵 (Realistic)

### Phase 1: P0 정리 (2-4주, 32 → 45점)
- [ ] Paper에 슬리피지 모델 추가 (체결 가격에 0.05~0.15% 노이즈)
- [ ] Paper에 거래 수수료 0.05% 양방향 차감
- [ ] ATR 기반 사이징 도입 (각 코인 변동성 등급화 → 사이즈 자동 조정)
- [ ] 포지션 상관관계 캡 (4개 중 BTC 상관 0.85+ 코인은 최대 2개로 제한)
- [ ] 신호 타임스탬프 + 시간 감쇠 함수 적용 (10초 전 vs 5분 전 가중치 차이)
- [ ] 워크포워드 백테스트 스크립트 작성 (3개월 train → 1주 test → 슬라이딩)

### Phase 2: P1 핵심 (1-2개월, 45 → 55점)
- [ ] Upbit websocket 도입 → 체결틱 (Time-and-Sales) 분석
- [ ] 대량 체결 (whale print) 감지 + 시그널 부스트
- [ ] 펀딩비/베이시스 시그널 (Binance/Bybit perp 데이터 가져오기)
- [ ] 온체인 시그널 (CryptoQuant/Glassnode 무료 API → 거래소 입출금)
- [ ] 이벤트 캘린더 (FOMC/CPI/BTC 반감기 → 자동 사이즈 축소)
- [ ] 코인 간 상관행렬 + Lead-Lag 분석 (BTC 5분 선행 → 알트 진입)

### Phase 3: 양방향 + 다거래소 (2-3개월, 55 → 65점)
- [ ] Binance/Bybit 추가 → 가격차 차익거래
- [ ] Perp/선물 모듈 (숏 진입 가능 → 하락장에서도 수익)
- [ ] 거래소 간 펀딩비 차익 + 베이시스 트레이딩
- [ ] 스마트 오더 라우팅 (가장 유리한 호가 자동 선택)

### Phase 4: ML 오버레이 (3-6개월, 65 → 72점)
- [ ] 과거 청산 포지션 라벨링 (TP / SL / Trail / 시간초과)
- [ ] Gradient Boosting (LightGBM) → "이 신호가 TP까지 갈 확률" 분류기
- [ ] 분류기 출력을 기존 combined_score와 가중 합산
- [ ] 매주 자동 재학습 (rolling window)
- [ ] 온라인 학습 (River 라이브러리) → 적응형 임계값

### Phase 5: 인프라 진화 (6-12개월, 72 → 78점)
- [ ] Rust/Go 실행 레이어 (레이턴시 5-10x 단축)
- [ ] VaR/CVaR 리스크 엔진 (Tail risk 정량화)
- [ ] HMM 리짐 디텍션 (Bull/Bear/Choppy/Crisis 4-state)
- [ ] TWAP/VWAP 실행 알고리즘 (큰 사이즈 분할)
- [ ] 시장 조성 모듈 (스프레드 양쪽에 호가 → 메이커 리베이트)

### Phase 6: 자체 인프라 (1-2년, 78 → 82점)
- [ ] 자체 데이터 웨어하우스 (틱 데이터 영구 저장)
- [ ] 백테스트 클러스터 (Monte Carlo 1000회 시뮬)
- [ ] Custom feature embeddings (오토인코더 기반)
- [ ] Walk-forward + Combinatorial Purged CV (오버핏 완전 차단)

### 한계
- **Tier S 도달 (85점+)**: 팀 + $10M+ 인프라 + 마이크로초 레이턴시 = 1인/소규모로는 불가
- **현실적 천장**: 1인 운영 = ~75점 (Tier A boutique 수준)
- **Phase 1-4 완료 시**: 개인 운용 시스템 중 상위 1% 진입

---

## 7. 우리 봇 vs 유명 트레이더 정성 비교

| 트레이더 | 우리 봇 vs 그들 강점 | 우리 봇이 못하는 것 |
|---|---|---|
| **Paul Tudor Jones** | 24/7 가동, 다중 신호 통합, 무감정 | 거시 직관 (FED 정책 변화 감지) |
| **Stan Druckenmiller** | 멀티 데스크 동시 운영 | 컨빅션 베팅 (한 종목에 30% 베팅) |
| **Linda Raschke** | Holy Grail (Pullback) 패턴 자동 감지 | 시장 컨텍스트 직관 (지금이 sell-off인지 panic인지) |
| **Mark Minervini** | 모멘텀 + 거래량 자동 스캔 | VCP (Volatility Contraction Pattern) 정밀 감지 |
| **Ross Cameron** | "First Pullback" 자동 진입 | 5분 차트 보고 즉시 판단하는 직관 |
| **Renaissance Medallion** | 룰 기반 일관성 | ML, 자체 데이터, 수십개 시그널 통합 |
| **Wintermute (crypto MM)** | 신호 분석 | 시장 조성, 메이커 리베이트, 마이크로초 실행 |

**결론**: 우리는 디스크리셔너리 트레이더의 "체크리스트와 직관"을 코드화하는 데에는 성공. 시스테매틱 펀드의 "리스크 모델 + ML + 인프라"는 한참 부족.

---

## 8. Claude + Codex 협업 가이드

### Claude (Sonnet/Opus) — 전략적/구조적 작업
- 새 시그널 설계 (예: pullback, ICT)
- 진입/청산 의사결정 트리 리팩토링
- 백테스트 결과 해석 + 파라미터 의도 설정
- 멀티 에이전트 디베이트 로직
- 사용자 요구사항 → 시스템 설계 변환
- 핸드오프 문서 + 전략 분석

### Codex — 구현/리팩토링/대량 코드 작업
- 백테스트 스크립트 대량 생성 (Phase 1-2)
- websocket 클라이언트 구현 (Upbit/Binance/Bybit)
- Rust/Go 실행 레이어 마이그레이션 (Phase 5)
- ML 파이프라인 (LightGBM 학습/서빙) 구현
- 단순 반복 리팩토링 (네이밍 일괄 변경, 타입 힌트 추가 등)
- 인프라 코드 (Dockerfile, k8s manifest, terraform)

### 협업 패턴
1. **새 기능**: Claude가 설계 → HANDOFF.md 업데이트 → Codex가 구현 → Claude가 코드 리뷰 + 통합
2. **버그 수정**: 간단 → Codex / 복잡 (여러 파일 영향) → Claude
3. **백테스트**: Codex가 스크립트 작성 + 실행 → Claude가 결과 분석 + 파라미터 조정
4. **ML 작업**: Codex가 데이터 파이프라인 + 학습 → Claude가 피처 설계 + 모델 통합

---

## 9. 즉시 다음 액션 (이 문서 받은 직후)

### Codex에게 줄 작업 (Phase 1, 1-2주)
1. `app/services/upbit_broker.py`에 슬리피지 시뮬레이터 추가 (paper 모드에서 0.05~0.15% 노이즈)
2. `app/core/state_store.py`의 `pnl_pct` 계산에 거래 수수료 0.05% 양방향 차감
3. `backtest/walk_forward.py` 신규 작성 — 3개월 train / 1주 test / 슬라이딩
4. `app/services/atr_sizing.py` 신규 — ATR(14) 기반 코인별 사이즈 자동 조정

### Claude에게 줄 작업 (Phase 1, 동시 진행)
1. 포지션 상관관계 캡 로직 설계 (어디에 삽입? `execution_agent` `_desk_limits`?)
2. 신호 시간 감쇠 함수 설계 (`signal_engine`에 timestamp 추가, 어떤 감쇠 함수?)
3. ATR 사이징 통합 시점 결정 (recommendation_engine vs execution_agent)
4. Phase 2 시작 전 워크포워드 결과 해석 + 룰 조정

---

## 10. 리스크 / 주의사항

- **현재 -6% 누적 손실 상황**: Phase 1을 마치기 전에는 라이브 사이즈를 보수적으로 유지 권장 (`UPBIT_PILOT_SINGLE_ORDER_ONLY=true` 유지)
- **오버핏 경계**: 백테스트가 아무리 좋아도 워크포워드 검증 전까지는 신뢰하지 말 것
- **레짐 변화**: 2025-2026 크립토 사이클이 우리 룰에 맞을지 미지수. 매주 closed_positions 통계 모니터링 필수
- **Codex와 Claude의 컨텍스트 단절**: 매번 HANDOFF.md + 이 문서를 시작 시점에 읽혀야 일관성 유지

---

**문서 버전**: v1.0 (2026-04-28)
**다음 업데이트 시점**: Phase 1 완료 시 (예상 2026-05-15)
