from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from app.config import settings


DATA_DIR = Path(settings.db_path).resolve().parent
APP_ROOT = str(Path(__file__).resolve().parent.parent)
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
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        line = (result.stdout or "").strip()
        if not line or line.startswith("INFO:"):
            return False
        return line.split(",")[1].strip('"') == str(pid)
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


def _matching_module_pids(module: str) -> list[int]:
    if os.name != "nt":
        return []
    script = (
        "$root = @'\n"
        f"{APP_ROOT}\n"
        "'@.Trim(); "
        f"$module = '{module}'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like \"*$root*\" -and $_.CommandLine -like \"*-m $module*\" } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            pids.append(int(text))
        except ValueError:
            continue
    return sorted(set(pids))


def _resolve_pid(pid_path: Path, module: str) -> tuple[int | None, int]:
    pid = _read_pid(pid_path)
    if pid and _process_running(pid):
        return pid, 1
    matches = [item for item in _matching_module_pids(module) if _process_running(item)]
    if len(matches) == 1:
        _write_pid(pid_path, matches[0])
        return matches[0], 1
    if not matches:
        return None, 0
    _write_pid(pid_path, matches[0])
    return matches[0], len(matches)


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
    server_pid, server_count = _resolve_pid(SERVER_PID_PATH, "app.main")
    loop_pid, loop_count = _resolve_pid(LOOP_PID_PATH, "app.runtime")

    started: dict[str, int | str] = {}
    if server_count > 1:
        started["server_pid"] = server_pid or 0
        started["server_status"] = f"duplicate_running:{server_count}"
    elif not server_pid or not _process_running(server_pid):
        started["server_pid"] = _spawn("app.main", SERVER_LOG_PATH, SERVER_PID_PATH)
    else:
        started["server_pid"] = server_pid
        started["server_status"] = "already_running"

    if loop_count > 1:
        started["loop_pid"] = loop_pid or 0
        started["loop_status"] = f"duplicate_running:{loop_count}"
    elif not loop_pid or not _process_running(loop_pid):
        started["loop_pid"] = _spawn("app.runtime", LOOP_LOG_PATH, LOOP_PID_PATH)
    else:
        started["loop_pid"] = loop_pid
        started["loop_status"] = "already_running"
    return started


def stop_services() -> dict:
    result: dict[str, str | int | bool] = {}
    for name, path, module in (
        ("server", SERVER_PID_PATH, "app.main"),
        ("loop", LOOP_PID_PATH, "app.runtime"),
    ):
        pid, count = _resolve_pid(path, module)
        pids = [pid] if pid else []
        if count > 1:
            pids = _matching_module_pids(module)
        if not pids:
            result[name] = "not_running"
            continue
        try:
            for target_pid in pids:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(target_pid), "/F"], check=False, capture_output=True)
                else:
                    os.kill(target_pid, 15)
            result[name] = pids[0] if len(pids) == 1 else {"primary_pid": pids[0], "killed": pids}
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
    urls = {
        "local_url": f"http://127.0.0.1:{settings.port}",
        "lan_url": f"http://{lan_ip}:{settings.port}",
    }
    if settings.public_base_url:
        urls["public_url"] = settings.public_base_url
        urls["public_label"] = settings.public_base_label or "Public URL"
    return urls


def status() -> dict:
    server_pid, server_count = _resolve_pid(SERVER_PID_PATH, "app.main")
    loop_pid, loop_count = _resolve_pid(LOOP_PID_PATH, "app.runtime")
    return {
        "server": {"pid": server_pid, "running": _process_running(server_pid or 0), "instances": server_count},
        "loop": {"pid": loop_pid, "running": _process_running(loop_pid or 0), "instances": loop_count},
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
