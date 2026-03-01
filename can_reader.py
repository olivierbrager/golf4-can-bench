from __future__ import annotations

import time
import threading
import json
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

import can

from dbc_codec import DbcCodec
from model import CanonicalState, now_s
from debug_instrumentation import METRICS, log

MAP_KEYS = ("MAP_kPa", "MAP", "MAPAbs_kPa", "ManifoldAbs", "ManifoldPressure")


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

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

    def _apply_canonical(self, sigs: Dict[str, Any], ts: float) -> None:
        with self.state.lock:
            if "RPM" in sigs:
                v = _as_float(sigs.get("RPM"))
                if v is not None:
                    self.state.set_signal("RPM", v, "rpm", ts)

            if "Speed" in sigs:
                v = _as_float(sigs.get("Speed"))
                if v is not None:
                    self.state.set_signal("Speed", v * self.speed_factor, "kmh", ts)

            if "Throttle" in sigs:
                v = _as_float(sigs.get("Throttle"))
                if v is not None:
                    self.state.set_signal("Throttle", v, "%", ts)

            if "Load" in sigs:
                v = _as_float(sigs.get("Load"))
                if v is not None:
                    self.state.set_signal("Load", v, "%", ts)

            if "CoolantTemp" in sigs:
                v = _as_float(sigs.get("CoolantTemp"))
                if v is not None:
                    self.state.set_signal("CoolantTemp", v, "°C", ts)

            if "OilTemp" in sigs:
                v = _as_float(sigs.get("OilTemp"))
                if v is not None:
                    self.state.set_signal("OilTemp", v, "°C", ts)

            if "BatteryV" in sigs:
                v = _as_float(sigs.get("BatteryV"))
                if v is not None:
                    self.state.set_signal("BatteryV", v, "V", ts)

            lam = None
            if "Lambda" in sigs:
                lam = _as_float(sigs.get("Lambda"))
                if lam is not None:
                    self.state.set_signal("Lambda", lam, "", ts)

            map_kpa = None
            for k in MAP_KEYS:
                if k in sigs:
                    v = _as_float(sigs.get(k))
                    if v is not None:
                        map_kpa = v * self.map_factor
                    break

            boost = None
            if map_kpa is not None:
                self.state.set_signal("MAP", map_kpa, "kPa", ts)
                boost = max(0.0, (map_kpa - self.atm_kpa) / 100.0)
                self.state.set_signal("Boost", boost, "bar", ts)

            self.state.update_derived_dev(boost, lam, ts)

    def _run(self) -> None:
        while True:
            bus = None
            try:
                bus = can.Bus(interface="socketcan", channel=self.can_ch, receive_own_messages=True)
                log("can_reader", "bus_open", level="info", bus=self.can_ch)
                while True:
                    msg = bus.recv(timeout=1.0)
                    if msg is None:
                        continue
                    ts = float(getattr(msg, "timestamp", 0.0) or 0.0)
                    if ts < 1_000_000_000.0:
                        ts = now_s()
                    self.state.bump_rx(msg.arbitration_id, ts)
                    METRICS.on_rx(ts)

                    decoded = self.codec.decode(msg.arbitration_id, msg.data, ts=ts)
                    if not decoded:
                        METRICS.inc("decode_unknown_id_total", 1.0)
                        log(
                            "can_reader",
                            "decode_unknown_id",
                            level="debug",
                            can_id=msg.arbitration_id,
                            dlc=len(getattr(msg, "data", []) or []),
                            bus=self.can_ch,
                        )
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

                    self._apply_canonical(decoded.signals, ts)

            except Exception as exc:
                log("can_reader", "rx_error", level="error", bus=self.can_ch, err=str(exc))
                try:
                    if bus is not None:
                        bus.shutdown()
                except Exception:
                    pass
                time.sleep(1.0)


class RemoteStateReader(CanReader):
    def __init__(
        self,
        remote_state_url: str,
        poll_hz: float,
        codec: DbcCodec,
        state: CanonicalState,
        speed_factor: float,
        map_factor: float,
        atm_kpa: float,
    ):
        super().__init__(
            can_ch="remote",
            codec=codec,
            state=state,
            speed_factor=speed_factor,
            map_factor=map_factor,
            atm_kpa=atm_kpa,
        )
        self.remote_state_url = remote_state_url
        self.poll_hz = max(1.0, poll_hz)

    def _run(self) -> None:
        period = 1.0 / self.poll_hz
        while True:
            try:
                req = Request(self.remote_state_url, headers={"Accept": "application/json"})
                with urlopen(req, timeout=2.0) as resp:
                    raw = resp.read()
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Remote payload is not an object")

                ts = now_s()
                self.state.bump_rx(0, ts)
                METRICS.on_rx(ts)

                # Map ECU emulator fields to DBC-like names expected by liveview.
                decoded: Dict[str, Any] = {}
                units: Dict[str, str] = {}

                mappings = (
                    ("rpm", "RPM", "rpm"),
                    ("speed", "Speed", "kmh"),
                    ("throttle", "Throttle", "%"),
                    ("load", "Load", "%"),
                    ("coolant", "CoolantTemp", "°C"),
                    ("oil_temp", "OilTemp", "°C"),
                    ("battery_voltage", "BatteryV", "V"),
                    ("lambda_value", "Lambda", ""),
                    ("map_kpa", "MAP", "kPa"),
                    ("mil", "MIL", ""),
                    ("epc", "EPC", ""),
                    ("fan_request", "Fan", ""),
                    ("cruise_active", "Cruise", ""),
                    ("brake_switch", "BrakeSwitch", ""),
                    ("clutch_switch", "ClutchSwitch", ""),
                )
                for src_key, dst_key, unit in mappings:
                    if src_key in payload:
                        decoded[dst_key] = payload[src_key]
                        units[dst_key] = unit

                if not decoded:
                    METRICS.inc("decode_unknown_id_total", 1.0)
                    time.sleep(period)
                    continue

                self.state.update_from_decoded("RemoteState", 0, decoded, units, ts)
                self._apply_canonical(decoded, ts)

            except Exception as exc:
                log("remote_reader", "rx_error", level="error", url=self.remote_state_url, err=str(exc))
                time.sleep(1.0)
                continue

            time.sleep(period)
