from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Any, Dict, Optional

DEBUG = os.getenv("DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
_LOG_THROTTLE_S = 0.25
_LAST_LOG_TS: Dict[tuple[str, str], float] = {}


def _now_s() -> float:
    return time.time()


def log(component: str, event: str, **fields: Any) -> None:
    if not DEBUG:
        return
    try:
        ts = _now_s()
        key = (component, event)
        last_ts = _LAST_LOG_TS.get(key)
        if last_ts is not None and (ts - last_ts) < _LOG_THROTTLE_S:
            return
        _LAST_LOG_TS[key] = ts

        level = fields.pop("level", "info")
        payload: Dict[str, Any] = {
            "ts": ts,
            "level": level,
            "component": component,
            "event": event,
        }
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True, default=str))
    except Exception:
        # Best-effort logging must never crash the app.
        try:
            print(
                json.dumps(
                    {
                        "ts": _now_s(),
                        "level": "error",
                        "component": "debug",
                        "event": "log_error",
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            )
        except Exception:
            pass


class DebugMetrics:
    def __init__(self, enabled: bool, window_s: float = 5.0) -> None:
        self.enabled = enabled
        self._window_s = window_s
        self._rx_times: deque[float] = deque()
        self._counters: Dict[str, float] = {}

    def _no_op(self) -> bool:
        return not self.enabled

    def inc(self, name: str, delta: float = 1.0) -> None:
        try:
            if self._no_op():
                return
            self._counters[name] = self._counters.get(name, 0.0) + delta
        except Exception:
            pass

    def on_rx(self, ts: float) -> None:
        try:
            if self._no_op():
                return
            self.inc("rx_frames_total", 1.0)
            self._rx_times.append(ts)
            cutoff = ts - self._window_s
            while self._rx_times and self._rx_times[0] < cutoff:
                self._rx_times.popleft()
            if self._rx_times:
                span = max(0.001, ts - self._rx_times[0])
                self._counters["rx_frames_per_sec"] = len(self._rx_times) / span
            else:
                self._counters["rx_frames_per_sec"] = 0.0
        except Exception:
            pass

    def get(self, name: str, default: float = 0.0) -> float:
        if self._no_op():
            return default
        return self._counters.get(name, default)

    def snapshot(self) -> Optional[Dict[str, float]]:
        if self._no_op():
            return None
        return dict(self._counters)


class _NoOpMetrics:
    enabled = False

    def inc(self, name: str, delta: float = 1.0) -> None:
        return

    def on_rx(self, ts: float) -> None:
        return

    def get(self, name: str, default: float = 0.0) -> float:
        return default

    def snapshot(self) -> Optional[Dict[str, float]]:
        return None


METRICS = DebugMetrics(True) if DEBUG else _NoOpMetrics()
