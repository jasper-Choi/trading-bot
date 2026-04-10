"""
FastAPI 앱 진입점 — 봇 스케줄러가 앱 시작 시 자동으로 백그라운드 스레드에서 실행됩니다.

실행:
  uvicorn api.main:app --reload --port 8000

Swagger UI: http://localhost:8000/docs
ReDoc:       http://localhost:8000/redoc
"""

import io
import os
import sys
from contextlib import asynccontextmanager

# 윈도우 한글 출력 처리
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# uvicorn을 trading-bot/ 루트에서 실행하므로 루트가 이미 sys.path에 있음.
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
from src.reporter import get_log_lines


# ---------------------------------------------------------------------------
# 앱 수명주기 — 시작 시 봇 스케줄러 자동 실행
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 앱 시작 시 봇 스케줄러를 자동으로 백그라운드에서 실행합니다."""
    bot_runner.start()
    print("[Startup] 봇 스케줄러 백그라운드 스레드 시작됨")
    yield
    bot_runner.stop()
    print("[Shutdown] 봇 스케줄러 중지됨")


app = FastAPI(
    title="Trading Bot API",
    description="모의투자 봇 — 변동성 돌파 + 추세 추종 전략",
    version="1.0.0",
    lifespan=lifespan,
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
    인메모리 로그 버퍼(최대 200줄)에서 최근 N줄을 반환합니다.
    lines 파라미터로 줄 수를 조정할 수 있습니다 (기본값 50, 최대 200).
    """
    lines = min(lines, 200)
    recent = get_log_lines(lines)
    return LogsOut(lines=recent, total_lines=len(recent))


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
