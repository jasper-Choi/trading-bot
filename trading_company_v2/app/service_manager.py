from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from app.config import settings


DATA_DIR = Path(settings.db_path).resolve().parent
SERVER_PID_PATH = DATA_DIR / "dashboard_server.pid"
LOOP_PID_PATH = DATA_DIR / "company_loop.pid"
SERVER_LOG_PATH = DATA_DIR / "dashboard_server.log"
LOOP_LOG_PATH = DATA_DIR / "company_loop.log"


def _pythonw() -> str:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _clear_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def _spawn(module: str, log_path: Path, pid_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    process = subprocess.Popen(
        [_pythonw(), "-m", module],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=log_handle,
        stderr=log_handle,
        creationflags=creationflags,
        close_fds=False,
    )
    _write_pid(pid_path, process.pid)
    return process.pid


def start_services() -> dict:
    server_pid = _read_pid(SERVER_PID_PATH)
    loop_pid = _read_pid(LOOP_PID_PATH)

    started: dict[str, int | str] = {}
    if not server_pid or not _process_running(server_pid):
        started["server_pid"] = _spawn("app.main", SERVER_LOG_PATH, SERVER_PID_PATH)
    else:
        started["server_pid"] = server_pid
        started["server_status"] = "already_running"

    if not loop_pid or not _process_running(loop_pid):
        started["loop_pid"] = _spawn("app.runtime", LOOP_LOG_PATH, LOOP_PID_PATH)
    else:
        started["loop_pid"] = loop_pid
        started["loop_status"] = "already_running"
    return started


def stop_services() -> dict:
    result: dict[str, str | int | bool] = {}
    for name, path in (("server", SERVER_PID_PATH), ("loop", LOOP_PID_PATH)):
        pid = _read_pid(path)
        if not pid:
            result[name] = "not_running"
            continue
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
            else:
                os.kill(pid, 15)
            result[name] = pid
        finally:
            _clear_pid(path)
    return result


def local_access_urls() -> dict[str, str]:
    host = settings.host
    if host in {"127.0.0.1", "localhost", "0.0.0.0"}:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                lan_ip = sock.getsockname()[0]
        except OSError:
            lan_ip = "127.0.0.1"
    else:
        lan_ip = host
    return {
        "local_url": f"http://127.0.0.1:{settings.port}",
        "lan_url": f"http://{lan_ip}:{settings.port}",
    }


def status() -> dict:
    server_pid = _read_pid(SERVER_PID_PATH)
    loop_pid = _read_pid(LOOP_PID_PATH)
    return {
        "server": {"pid": server_pid, "running": _process_running(server_pid or 0)},
        "loop": {"pid": loop_pid, "running": _process_running(loop_pid or 0)},
        **local_access_urls(),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "status"])
    args = parser.parse_args()

    if args.command == "start":
        print(json.dumps(start_services(), ensure_ascii=False, indent=2))
    elif args.command == "stop":
        print(json.dumps(stop_services(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
