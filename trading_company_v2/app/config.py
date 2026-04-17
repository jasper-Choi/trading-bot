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
    host: str = os.environ.get("APP_HOST", "127.0.0.1")
    port: int = int(os.environ.get("APP_PORT", "8080"))
    cycle_interval_minutes: int = int(os.environ.get("CYCLE_INTERVAL_MINUTES", "15"))
    timezone: str = os.environ.get("APP_TIMEZONE", "Asia/Seoul")
    db_path: Path = Path(os.environ.get("APP_DB_PATH", str(DATA_DIR / "trading_company_v2.db")))
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    huggingface_api_key: str = os.environ.get("HUGGINGFACE_API_KEY", "")
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")
    telegram_notify_every_cycle: bool = os.environ.get("TELEGRAM_NOTIFY_EVERY_CYCLE", "false").lower() == "true"
    operator_name: str = os.environ.get("OPERATOR_NAME", "Owner")


settings = Settings()
