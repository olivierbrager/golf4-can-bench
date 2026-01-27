from __future__ import annotations

import time
import threading
from typing import Any, Dict, Optional

import can

from dbc_codec import DbcCodec
from model import CanonicalState, now_s

MAP_KEYS = ("MAP_kPa", "MAP", "MAPAbs_kPa", "ManifoldAbs", "ManifoldPressure")

class CanReader:
    def __init__(
        self,
        can_ch: str,
        codec: DbcCodec,
        state: CanonicalState,
        speed_factor: float,
        map_factor: float,
        atm_kpa: float,
    ):
        self.can_ch = can_ch
        self.codec = codec
        self.state = state
        self.speed_factor = speed_factor
        self.map_factor = map_factor
        self.atm_kpa = atm_kpa

        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            bus = None
            try:
                bus = can.Bus(interface="socketcan", channel=self.can_ch, receive_own_messages=True)
                while True:
                    msg = bus.recv(timeout=1.0)
                    if msg is None:
                        continue
                    ts = float(getattr(msg, "timestamp", 0.0) or now_s())
                    self.state.bump_rx(msg.arbitration_id, ts)

                    decoded = self.codec.decode(msg.arbitration_id, msg.data, ts=ts)
                    if not decoded:
                        continue

                    # Units from DBC if available
                    units: Dict[str, str] = {}
                    try:
                        m = self.codec._by_id.get(decoded.arb_id)
                        if m:
                            for s in m.signals:
                                units[s.name] = s.unit or ""
                    except Exception:
                        units = {}

                    self.state.update_from_decoded(decoded.name, decoded.arb_id, decoded.signals, units, ts)

                    # Canonical mapping (Dev invariance)
                    with self.state.lock:
                        sigs = decoded.signals

                        if "RPM" in sigs:
                            self.state.set_signal("RPM", float(sigs["RPM"]), "rpm", ts)

                        if "Speed" in sigs:
                            self.state.set_signal("Speed", float(sigs["Speed"]) * self.speed_factor, "kmh", ts)

                        if "Throttle" in sigs:
                            self.state.set_signal("Throttle", float(sigs["Throttle"]), "%", ts)

                        if "Load" in sigs:
                            self.state.set_signal("Load", float(sigs["Load"]), "%", ts)

                        if "CoolantTemp" in sigs:
                            self.state.set_signal("CoolantTemp", float(sigs["CoolantTemp"]), "°C", ts)

                        if "OilTemp" in sigs:
                            self.state.set_signal("OilTemp", float(sigs["OilTemp"]), "°C", ts)

                        if "BatteryV" in sigs:
                            self.state.set_signal("BatteryV", float(sigs["BatteryV"]), "V", ts)

                        lam = None
                        if "Lambda" in sigs:
                            lam = float(sigs["Lambda"])
                            self.state.set_signal("Lambda", lam, "", ts)

                        map_kpa = None
                        for k in MAP_KEYS:
                            if k in sigs:
                                map_kpa = float(sigs[k]) * self.map_factor
                                break

                        boost = None
                        if map_kpa is not None:
                            self.state.set_signal("MAP", map_kpa, "kPa", ts)
                            boost = max(0.0, (map_kpa - self.atm_kpa) / 100.0)
                            self.state.set_signal("Boost", boost, "bar", ts)

                        self.state.update_derived_dev(boost, lam, ts)

            except Exception:
                try:
                    if bus is not None:
                        bus.shutdown()
                except Exception:
                    pass
                time.sleep(1.0)
