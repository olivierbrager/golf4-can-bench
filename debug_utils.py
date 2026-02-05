from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

DEBUG = os.getenv("DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def _now_s() -> float:
    return time.time()


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))


def _log(level: str, component: str, event: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {
        "ts": _now_s(),
        "level": level,
        "component": component,
        "event": event,
    }
    for k, v in fields.items():
        if v is not None:
            payload[k] = v
    _emit(payload)


def log_debug(component: str, event: str, **fields: Any) -> None:
    if DEBUG:
        _log("debug", component, event, **fields)


def log_info(component: str, event: str, **fields: Any) -> None:
    if DEBUG:
        _log("info", component, event, **fields)


def log_warn(component: str, event: str, **fields: Any) -> None:
    _log("warn", component, event, **fields)


def log_error(component: str, event: str, **fields: Any) -> None:
    _log("error", component, event, **fields)


class DebugMetrics:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self.rx_frames_total = 0
        self.rx_frames_per_sec = 0.0
        self.ws_clients_connected = 0
        self.ws_updates_sent_total = 0
        self.ws_updates_dropped_total = 0
        self.decode_unknown_id_total = 0
        self._rx_window_s = 5.0
        self._rx_times: deque[float] = deque()

    def _no_op(self) -> bool:
        return not self.enabled

    def on_rx_frame(self, ts: float) -> None:
        if self._no_op():
            return
        with self._lock:
            self.rx_frames_total += 1
            self._rx_times.append(ts)
            cutoff = ts - self._rx_window_s
            while self._rx_times and self._rx_times[0] < cutoff:
                self._rx_times.popleft()
            span = max(0.001, ts - self._rx_times[0])
            self.rx_frames_per_sec = len(self._rx_times) / span

    def on_decode_unknown(self) -> None:
        if self._no_op():
            return
        with self._lock:
            self.decode_unknown_id_total += 1

    def on_ws_connected(self) -> None:
        if self._no_op():
            return
        with self._lock:
            self.ws_clients_connected += 1

    def on_ws_disconnected(self) -> None:
        if self._no_op():
            return
        with self._lock:
            if self.ws_clients_connected > 0:
                self.ws_clients_connected -= 1

    def on_ws_sent(self) -> None:
        if self._no_op():
            return
        with self._lock:
            self.ws_updates_sent_total += 1

    def on_ws_dropped(self) -> None:
        if self._no_op():
            return
        with self._lock:
            self.ws_updates_dropped_total += 1

    def snapshot(self) -> Optional[Dict[str, Any]]:
        if self._no_op():
            return None
        with self._lock:
            return {
                "rx_frames_total": self.rx_frames_total,
                "rx_frames_per_sec": self.rx_frames_per_sec,
                "ws_clients_connected": self.ws_clients_connected,
                "ws_updates_sent_total": self.ws_updates_sent_total,
                "ws_updates_dropped_total": self.ws_updates_dropped_total,
                "decode_unknown_id_total": self.decode_unknown_id_total,
            }


METRICS = DebugMetrics(DEBUG)
