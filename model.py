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
    """Single source of truth for ALL dashboards."""

    def __init__(self, stale_s: float):
        self.stale_s = stale_s
        self.lock = threading.Lock()

        self.rx_total = 0
        self.rx_decoded = 0
        self.last_rx_ts = 0.0
        self.last_frame: Dict[str, Any] = {"arb_id": None, "name": None}

        # Canonical signals (preferred set)
        self.signals: Dict[str, Signal] = {}

        # Raw decoded signals (full table) — for Debug invariance
        self.raw: Dict[str, Signal] = {}

        # Flags (int 0/1)
        self.flags: Dict[str, int] = {
            "MIL": 0,
            "EPC": 0,
            "Fan": 0,
            "Cruise": 0,
            "Brake": 0,
            "Clutch": 0,
        }

        # Histories for DEV (server-side optional; kept minimal)
        self._boost_hist: deque[Tuple[float, float]] = deque()
        self._lambda_hist: deque[Tuple[float, float]] = deque()
        self.dev: Dict[str, Any] = {
            "BoostMax5": None,
            "LambdaMin5": None,
            "LambdaMax5": None,
        }

    def _prune(self, dq: deque, horizon_s: float = 5.0) -> None:
        t = now_s()
        while dq and (t - dq[0][0]) > horizon_s:
            dq.popleft()

    def bump_rx(self, arb_id: int, ts: float) -> None:
        with self.lock:
            self.rx_total += 1
            if ts:
                self.last_rx_ts = ts
            self.last_frame = {"arb_id": arb_id, "name": self.last_frame.get("name")}

    def set_flag(self, name: str, value: int) -> None:
        if name in self.flags:
            self.flags[name] = 1 if int(value) else 0

    def set_signal(self, name: str, value: Any, unit: str, ts: float) -> None:
        self.signals[name] = Signal(v=value, unit=unit, ts=ts)

    def set_raw(self, name: str, value: Any, unit: str, ts: float) -> None:
        self.raw[name] = Signal(v=value, unit=unit, ts=ts)

    def update_from_decoded(self, msg_name: str, arb_id: int, decoded: Dict[str, Any], units: Optional[Dict[str, str]], ts: float) -> None:
        with self.lock:
            self.rx_decoded += 1
            self.last_frame = {"arb_id": arb_id, "name": msg_name}
            if ts:
                self.last_rx_ts = ts

            # raw table (Debug)
            for k, v in decoded.items():
                self.set_raw(k, v, (units or {}).get(k, ""), ts)

            # flags mapping (if present)
            if "MIL" in decoded: self.set_flag("MIL", int(decoded["MIL"]))
            if "EPC" in decoded: self.set_flag("EPC", int(decoded["EPC"]))
            if "Fan" in decoded: self.set_flag("Fan", int(decoded["Fan"]))
            if "Cruise" in decoded: self.set_flag("Cruise", int(decoded["Cruise"]))
            if "BrakeSwitch" in decoded: self.set_flag("Brake", int(decoded["BrakeSwitch"]))
            if "ClutchSwitch" in decoded: self.set_flag("Clutch", int(decoded["ClutchSwitch"]))

    def update_derived_dev(self, boost_bar: Optional[float], lam: Optional[float], ts: float) -> None:
        # called with lock held by caller
        if boost_bar is not None:
            self._boost_hist.append((ts, float(boost_bar)))
            self._prune(self._boost_hist, 5.0)
            self.dev["BoostMax5"] = max(v for _, v in self._boost_hist) if self._boost_hist else None
        if lam is not None:
            self._lambda_hist.append((ts, float(lam)))
            self._prune(self._lambda_hist, 5.0)
            if self._lambda_hist:
                vals = [v for _, v in self._lambda_hist]
                self.dev["LambdaMin5"] = min(vals)
                self.dev["LambdaMax5"] = max(vals)

    def payload(self, src: str, dbc_name: str, push_hz: float, conv: Dict[str, float]) -> Dict[str, Any]:
        with self.lock:
            stale = (now_s() - self.last_rx_ts) > self.stale_s if self.last_rx_ts else True

            def pack_table(tab: Dict[str, Signal]) -> Dict[str, Any]:
                out: Dict[str, Any] = {}
                for k, s in tab.items():
                    out[k] = {"v": s.v, "unit": s.unit, "age": s.age}
                return out

            return {
                "meta": {
                    "ts": now_s(),
                    "src": src,
                    "dbc": dbc_name,
                    "rx_total": self.rx_total,
                    "rx_decoded": self.rx_decoded,
                    "last_rx_ts": self.last_rx_ts,
                    "stale": stale,
                    "last_frame": dict(self.last_frame),
                    "push_hz": push_hz,
                    "stale_s": self.stale_s,
                    "conversions": dict(conv),
                },
                "signals": pack_table(self.signals),
                "flags": dict(self.flags),
                "raw": pack_table(self.raw),
                "dev": dict(self.dev),
            }
