from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from collections import deque

def now_s() -> float:
    return time.time()

@dataclass
class Signal:
    v: Any
    unit: str = ""
    ts: float = 0.0

    @property
    def age(self) -> float:
        if not self.ts:
            return 1e9
        return max(0.0, now_s() - self.ts)

class CanonicalState:
    """Single source of truth for ALL dashboards (DEV/DEBUG invariant)."""

    def __init__(self, stale_s: float):
        self.stale_s = stale_s
        self.lock = threading.Lock()

        # RX metrics (invariant)
        self.rx_total = 0
        self.rx_decoded = 0
        self.last_rx_ts = 0.0
        self.last_frame: Dict[str, Any] = {"arb_id": None, "name": None}

        # Canonical signals
        self.signals: Dict[str, Signal] = {}

        # Raw decoded signals (debug)
        self.raw: Dict[str, Signal] = {}

        # Flags
        self.flags: Dict[str, int] = {
            "MIL": 0,
            "EPC": 0,
            "Fan": 0,
            "Cruise": 0,
            "Brake": 0,
            "Clutch": 0,
        }

        # DEV history
        self._boost_hist: deque[Tuple[float, float]] = deque()
        self._lambda_hist: deque[Tuple[float, float]] = deque()
        self.dev: Dict[str, Any] = {
            "BoostMax5": None,
            "LambdaMin5": None,
            "LambdaMax5": None,
        }

    # ---------------- RX ----------------

    @property
    def rx_last_age_s(self) -> Optional[float]:
        if not self.last_rx_ts:
            return None
        return max(0.0, now_s() - self.last_rx_ts)

    def bump_rx(self, arb_id: int, ts: float) -> None:
        with self.lock:
            self.rx_total += 1
            if ts:
                self.last_rx_ts = ts
            self.last_frame = {"arb_id": arb_id, "name": self.last_frame.get("name")}

    # ---------------- setters ----------------

    def set_flag(self, name: str, value: int) -> None:
        if name in self.flags:
            self.flags[name] = 1 if int(value) else 0

    def set_signal(self, name: str, value: Any, unit: str, ts: float) -> None:
        self.signals[name] = Signal(v=value, unit=unit, ts=ts)

    def set_raw(self, name: str, value: Any, unit: str, ts: float) -> None:
        self.raw[name] = Signal(v=value, unit=unit, ts=ts)

    # ---------------- decode hooks ----------------

    def update_from_decoded(
        self,
        msg_name: str,
        arb_id: int,
        decoded: Dict[str, Any],
        units: Optional[Dict[str, str]],
        ts: float,
    ) -> None:
        with self.lock:
            self.rx_decoded += 1
            self.last_frame = {"arb_id": arb_id, "name": msg_name}
            if ts:
                self.last_rx_ts = ts

            for k, v in decoded.items():
                self.set_raw(k, v, (units or {}).get(k, ""), ts)

            if "MIL" in decoded: self.set_flag("MIL", decoded["MIL"])
            if "EPC" in decoded: self.set_flag("EPC", decoded["EPC"])
            if "Fan" in decoded: self.set_flag("Fan", decoded["Fan"])
            if "Cruise" in decoded: self.set_flag("Cruise", decoded["Cruise"])
            if "BrakeSwitch" in decoded: self.set_flag("Brake", decoded["BrakeSwitch"])
            if "ClutchSwitch" in decoded: self.set_flag("Clutch", decoded["ClutchSwitch"])

    # ---------------- DEV derived ----------------

    def _prune(self, dq: deque, horizon_s: float) -> None:
        t = now_s()
        while dq and (t - dq[0][0]) > horizon_s:
            dq.popleft()

    def update_derived_dev(self, boost_bar: Optional[float], lam: Optional[float], ts: float) -> None:
        if boost_bar is not None:
            self._boost_hist.append((ts, boost_bar))
            self._prune(self._boost_hist, 5.0)
            self.dev["BoostMax5"] = max(v for _, v in self._boost_hist)

        if lam is not None:
            self._lambda_hist.append((ts, lam))
            self._prune(self._lambda_hist, 5.0)
            vals = [v for _, v in self._lambda_hist]
            self.dev["LambdaMin5"] = min(vals)
            self.dev["LambdaMax5"] = max(vals)

    # ---------------- payload ----------------

    def payload(self, src: str, dbc_name: str, push_hz: float, conv: Dict[str, float]) -> Dict[str, Any]:
        with self.lock:
            stale = self.rx_last_age_s is None or self.rx_last_age_s > self.stale_s

            def pack(tab: Dict[str, Signal]) -> Dict[str, Any]:
                return {k: {"v": s.v, "unit": s.unit, "age": s.age} for k, s in tab.items()}

            return {
                "meta": {
                    "ts": now_s(),
                    "src": src,
                    "dbc": dbc_name,
                    "rx_total": self.rx_total,
                    "rx_decoded": self.rx_decoded,
                    "last_rx_ts": self.last_rx_ts,
                    "last_rx_age_s": self.rx_last_age_s,
                    "stale": stale,
                    "last_frame": dict(self.last_frame),
                    "push_hz": push_hz,
                    "stale_s": self.stale_s,
                    "conversions": dict(conv),
                },
                "signals": pack(self.signals),
                "flags": dict(self.flags),
                "raw": pack(self.raw),
                "dev": dict(self.dev),
            }
