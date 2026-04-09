"""
FastAPI 앱 진입점.

실행:
  uvicorn api.main:app --reload --port 8000

Swagger UI: http://localhost:8000/docs
ReDoc:       http://localhost:8000/redoc
"""

import io
import os
import sys

# 윈도우 한글 출력 처리
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# uvicorn을 trading-bot/ 루트에서 실행하므로 루트가 이미 sys.path에 있음.
# 혹시 누락된 경우를 대비해 명시적으로 추가.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config
from api.routers import bot, positions, trades, stats
from api.routers.bot import bot_runner
from api.models import BotStatusOut, LogsOut

app = FastAPI(
    title="Trading Bot API",
    description="모의투자 봇 — 변동성 돌파 + 추세 추종 전략",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CORS — React 개발 서버 및 로컬 파일 접근 허용
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 프로덕션에서는 특정 도메인으로 제한할 것
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------
app.include_router(bot.router)
app.include_router(positions.router)
app.include_router(trades.router)
app.include_router(stats.router)

# ---------------------------------------------------------------------------
# 공통 엔드포인트
# ---------------------------------------------------------------------------

@app.get("/api/status", response_model=BotStatusOut, tags=["status"], summary="봇 실행 상태")
def get_status():
    """봇의 현재 실행 상태(on/off), 마지막 실행 시각, 다음 예정 시각을 반환합니다."""
    return bot_runner.to_status()


@app.get("/api/logs", response_model=LogsOut, tags=["logs"], summary="최근 로그 조회")
def get_logs(lines: int = 50):
    """
    logs/trading.log 파일의 최근 N줄을 반환합니다.
    lines 파라미터로 줄 수를 조정할 수 있습니다 (기본값 50, 최대 500).
    """
    lines = min(lines, 500)
    log_path = os.path.join(config.LOG_DIR, "trading.log")

    if not os.path.exists(log_path):
        return LogsOut(lines=[], total_lines=0)

    with open(log_path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    stripped = [l.rstrip("\n") for l in all_lines]
    return LogsOut(
        lines=stripped[-lines:],
        total_lines=len(stripped),
    )


# ---------------------------------------------------------------------------
# 정적 파일 서빙 — React 빌드 결과물 (frontend/dist)
# /api/* 는 위 라우터가 먼저 처리하므로 충돌 없음
# ---------------------------------------------------------------------------
DIST_DIR = os.path.join(ROOT, "frontend", "dist")

if os.path.isdir(DIST_DIR):
    # JS/CSS/assets 서빙
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str = ""):
        """API 경로가 아닌 모든 요청을 index.html로 라우팅 (SPA fallback)."""
        # /api/* 는 위 라우터들이 먼저 처리하므로 여기 도달하지 않음
        index = os.path.join(DIST_DIR, "index.html")
        return FileResponse(index)
else:
    @app.get("/", tags=["root"], summary="루트")
    def root():
        return {
            "service": "Trading Bot API",
            "docs": "/docs",
            "status": "/api/status",
            "note": "frontend/dist not found — run: cd frontend && npm run build",
        }
