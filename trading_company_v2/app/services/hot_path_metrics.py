from __future__ import annotations

import json
import statistics
import tempfile
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

from app.config import settings


_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=300)
_reason_counts: Counter[str] = Counter()
_last_flush_monotonic = 0.0
_metrics_path = Path(settings.db_path).resolve().parent / "hot_path_latency.json"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return round(ordered[idx], 3)


def _build_snapshot_locked() -> dict[str, Any]:
    events = list(_events)
    total_ms = [float(item.get("total_ms", 0.0) or 0.0) for item in events]
    guard_ms = [float(item.get("guard_ms", 0.0) or 0.0) for item in events if item.get("guard_ms") is not None]
    dispatch_ms = [float(item.get("dispatch_ms", 0.0) or 0.0) for item in events]
    return {
        "updated_at_epoch": time.time(),
        "sample_count": len(events),
        "reason_counts": dict(_reason_counts),
        "latency_ms": {
            "dispatch_avg": round(statistics.mean(dispatch_ms), 3) if dispatch_ms else 0.0,
            "dispatch_p95": _percentile(dispatch_ms, 0.95),
            "guard_avg": round(statistics.mean(guard_ms), 3) if guard_ms else 0.0,
            "guard_p95": _percentile(guard_ms, 0.95),
            "total_avg": round(statistics.mean(total_ms), 3) if total_ms else 0.0,
            "total_p95": _percentile(total_ms, 0.95),
            "total_max": round(max(total_ms), 3) if total_ms else 0.0,
        },
        "recent": events[-20:],
    }


def _flush_snapshot_locked(force: bool = False) -> None:
    global _last_flush_monotonic
    now = time.monotonic()
    if not force and now - _last_flush_monotonic < 2.0:
        return
    _last_flush_monotonic = now
    snapshot = _build_snapshot_locked()
    _metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(_metrics_path.parent)) as tmp:
        json.dump(snapshot, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_metrics_path)


def record_hot_path_event(event: dict[str, Any]) -> None:
    reason = str(event.get("reason") or "unknown")
    event = {"recorded_at_epoch": time.time(), **dict(event)}
    with _lock:
        _events.append(event)
        _reason_counts[reason] += 1
        _flush_snapshot_locked()


def reset_hot_path_metrics() -> None:
    global _last_flush_monotonic
    with _lock:
        _events.clear()
        _reason_counts.clear()
        _last_flush_monotonic = 0.0
        _flush_snapshot_locked(force=True)


def read_hot_path_metrics() -> dict[str, Any]:
    try:
        return json.loads(_metrics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        with _lock:
            return _build_snapshot_locked()
