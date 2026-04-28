from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = BASE_DIR / ".env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


load_env_file(ENV_PATH)


@dataclass(slots=True)
class Settings:
    company_name: str = os.environ.get("COMPANY_NAME", "Solo Trading Company")
    app_env: str = os.environ.get("APP_ENV", "local")
    execution_mode: str = os.environ.get("EXECUTION_MODE", "paper")
    active_desks: str = os.environ.get("ACTIVE_DESKS", "crypto")
    paper_fee_bps: float = float(os.environ.get("PAPER_FEE_BPS", "5"))
    paper_slippage_min_bps: float = float(os.environ.get("PAPER_SLIPPAGE_MIN_BPS", "5"))
    paper_slippage_max_bps: float = float(os.environ.get("PAPER_SLIPPAGE_MAX_BPS", "15"))
    crypto_high_corr_threshold: float = float(os.environ.get("CRYPTO_HIGH_CORR_THRESHOLD", "0.85"))
    crypto_high_corr_max_positions: int = int(os.environ.get("CRYPTO_HIGH_CORR_MAX_POSITIONS", "2"))
    crypto_signal_stale_minutes: float = float(os.environ.get("CRYPTO_SIGNAL_STALE_MINUTES", "6"))
    live_capital_krw: int = int(os.environ.get("LIVE_CAPITAL_KRW", "0"))
    upbit_pilot_max_krw: int = int(os.environ.get("UPBIT_PILOT_MAX_KRW", "2500000"))
    upbit_pilot_single_order_only: bool = os.environ.get("UPBIT_PILOT_SINGLE_ORDER_ONLY", "false").lower() == "true"
    host: str = os.environ.get("APP_HOST", "127.0.0.1")
    port: int = int(os.environ.get("APP_PORT", "8080"))
    public_base_url: str = os.environ.get("PUBLIC_BASE_URL", "")
    public_base_label: str = os.environ.get("PUBLIC_BASE_LABEL", "Public URL")
    cycle_interval_minutes: int = int(os.environ.get("CYCLE_INTERVAL_MINUTES", "15"))
    realtime_active_interval_seconds: int = int(os.environ.get("REALTIME_ACTIVE_INTERVAL_SECONDS", "20"))
    realtime_watch_interval_seconds: int = int(os.environ.get("REALTIME_WATCH_INTERVAL_SECONDS", "45"))
    realtime_idle_interval_seconds: int = int(os.environ.get("REALTIME_IDLE_INTERVAL_SECONDS", "120"))
    crypto_fast_cycle_seconds: int = int(os.environ.get("CRYPTO_FAST_CYCLE_SECONDS", "8"))
    crypto_rapid_guard_seconds: int = int(os.environ.get("CRYPTO_RAPID_GUARD_SECONDS", "3"))
    timezone: str = os.environ.get("APP_TIMEZONE", "Asia/Seoul")
    paper_capital_krw: int = int(os.environ.get("PAPER_CAPITAL_KRW", "10000000"))
    db_path: Path = Path(os.environ.get("APP_DB_PATH", str(DATA_DIR / "trading_company_v2.db")))
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    huggingface_api_key: str = os.environ.get("HUGGINGFACE_API_KEY", "")
    alphavantage_api_key: str = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    upbit_access_key: str = os.environ.get("UPBIT_ACCESS_KEY", "")
    upbit_secret_key: str = os.environ.get("UPBIT_SECRET_KEY", "")
    upbit_allow_live: bool = os.environ.get("UPBIT_ALLOW_LIVE", "false").lower() == "true"
    kis_app_key: str = os.environ.get("KIS_APP_KEY", "")
    kis_app_secret: str = os.environ.get("KIS_APP_SECRET", "")
    kis_account_no: str = os.environ.get("KIS_ACCOUNT_NO", "")
    kis_product_code: str = os.environ.get("KIS_PRODUCT_CODE", "")
    kis_allow_live: bool = os.environ.get("KIS_ALLOW_LIVE", "false").lower() == "true"
    kis_mock: bool = os.environ.get("KIS_MOCK", "false").lower() == "true"
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")
    telegram_notify_every_cycle: bool = os.environ.get("TELEGRAM_NOTIFY_EVERY_CYCLE", "false").lower() == "true"
    telegram_summary_enabled: bool = os.environ.get("TELEGRAM_SUMMARY_ENABLED", "false").lower() == "true"
    telegram_ops_enabled: bool = os.environ.get("TELEGRAM_OPS_ENABLED", "false").lower() == "true"
    telegram_realtime_enabled: bool = os.environ.get("TELEGRAM_REALTIME_ENABLED", "false").lower() == "true"
    telegram_risk_enabled: bool = os.environ.get("TELEGRAM_RISK_ENABLED", "true").lower() == "true"
    telegram_stale_enabled: bool = os.environ.get("TELEGRAM_STALE_ENABLED", "true").lower() == "true"
    telegram_error_enabled: bool = os.environ.get("TELEGRAM_ERROR_ENABLED", "true").lower() == "true"
    operator_name: str = os.environ.get("OPERATOR_NAME", "Owner")
    app_username: str = os.environ.get("APP_USERNAME", "")
    app_password: str = os.environ.get("APP_PASSWORD", "")

    @property
    def active_desk_set(self) -> set[str]:
        desks = {item.strip().lower() for item in self.active_desks.split(",") if item.strip()}
        return desks or {"crypto"}


settings = Settings()
