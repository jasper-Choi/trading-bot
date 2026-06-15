"""데이터 무결성 검증 프레임워크 (2026-06-15).

자동매매 검증 19개 테스트의 1번 "데이터 검증" 구현.
캔들/가격 데이터가 전략 판단에 쓰이기 전에 무결성을 점검 — 쓰레기 데이터로
잘못된 신호/포지션이 생기는 것을 사전 차단.

배경(실제 사고): NAVER 루프버그(동일종목 38건 유령 포지션), 부분체결 좀비,
fchart 깨진 캔들 등 데이터/상태 정합성 문제가 반복됨. 사후 패치 대신
진입 경로에서 데이터를 게이트.

사용처:
  validate_candles(candles)  → 캔들 무결성 (전략 스캔 전)
  validate_price(price, ...)  → 단일 가격 sanity
  검증 실패는 fail-closed(해당 종목 스킵) 권장 — 단, 호출측이 판단.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def validate_candles(
    candles: list[dict],
    min_len: int = 20,
    max_gap_pct: float = 35.0,
    max_dup_ratio: float = 0.10,
) -> ValidationResult:
    """일봉/분봉 캔들 리스트 무결성 검증.

    체크:
      1. 최소 길이 (지표 계산 가능)
      2. OHLC 양수 + high>=low>=0 + high>=open/close>=low (논리 정합성)
      3. 일별 갭 폭주 (max_gap_pct 초과 = 데이터 오류 의심, 액면분할/오스캔)
      4. 연속 중복 종가 비율 (스테일 데이터/거래정지 과다)
      5. 단조 시간순 (날짜 역전 없음)
    """
    issues: list[str] = []
    n = len(candles)
    if n < min_len:
        return ValidationResult(False, [f"길이 부족 {n}<{min_len}"])

    closes: list[float] = []
    bad_ohlc = 0
    dup_run = 0
    max_dup_run = 0
    prev_close: float | None = None
    big_gaps = 0
    prev_date = ""
    out_of_order = 0

    for c in candles:
        o = float(c.get("open") or 0.0)
        h = float(c.get("high") or 0.0)
        low = float(c.get("low") or 0.0)
        cl = float(c.get("close") or 0.0)
        # OHLC 논리 검증
        if cl <= 0 or h <= 0 or low <= 0:
            bad_ohlc += 1
        elif not (h >= low and h >= cl >= low and h >= o >= low):
            bad_ohlc += 1
        if cl > 0:
            closes.append(cl)
            # 갭 폭주 체크
            if prev_close and prev_close > 0:
                gap = abs(cl - prev_close) / prev_close * 100.0
                if gap > max_gap_pct:
                    big_gaps += 1
                if abs(cl - prev_close) < 1e-9:
                    dup_run += 1
                    max_dup_run = max(max_dup_run, dup_run)
                else:
                    dup_run = 0
            prev_close = cl
        # 시간 순서
        d = str(c.get("date") or c.get("time") or "")
        if prev_date and d and d < prev_date:
            out_of_order += 1
        if d:
            prev_date = d

    if bad_ohlc > 0:
        issues.append(f"OHLC 오류 {bad_ohlc}건 (음수/논리위반)")
    if big_gaps > 0:
        issues.append(f"갭 폭주 {big_gaps}건 (>{max_gap_pct}% — 액면분할/오스캔 의심)")
    if n > 0 and max_dup_run / n > max_dup_ratio:
        issues.append(f"중복 종가 연속 {max_dup_run}봉 (스테일 데이터 의심)")
    if out_of_order > 0:
        issues.append(f"시간순 역전 {out_of_order}건")
    if len(closes) < min_len:
        issues.append(f"유효 종가 부족 {len(closes)}<{min_len}")

    return ValidationResult(len(issues) == 0, issues)


def validate_price(price: float, ref_price: float = 0.0, max_dev_pct: float = 30.0) -> ValidationResult:
    """단일 실시간 가격 sanity 체크.

    - 양수
    - 참조가(직전 종가 등) 대비 max_dev_pct 이내 (틱 오류/0원 방어)
    """
    issues: list[str] = []
    if price <= 0:
        return ValidationResult(False, ["가격 <= 0"])
    if ref_price > 0:
        dev = abs(price - ref_price) / ref_price * 100.0
        if dev > max_dev_pct:
            issues.append(f"참조가 대비 {dev:.0f}% 이탈 (>{max_dev_pct}% — 틱 오류 의심)")
    return ValidationResult(len(issues) == 0, issues)


def validate_position_consistency(open_positions: list[dict], max_per_symbol: int = 3) -> ValidationResult:
    """포지션 정합성 — 동일 종목 과다 중복 감지 (루프버그 조기 경보).

    NAVER 루프버그(동일종목 38건) 같은 비정상 반복을 데이터 레벨에서 탐지.
    """
    issues: list[str] = []
    by_symbol: dict[str, int] = {}
    for p in open_positions:
        sym = str(p.get("symbol", "") or "")
        if sym:
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
    for sym, cnt in by_symbol.items():
        if cnt > max_per_symbol:
            issues.append(f"{sym} 중복 포지션 {cnt}건 (>{max_per_symbol} — 루프버그 의심)")
    return ValidationResult(len(issues) == 0, issues)
