# app.py
# ECU Emulator (DBC) - Dark UI + scenarios + live state publisher (10 Hz)
# Scenarios:
#   - idle / cruise / wot (static presets)
#   - ramp (8s one-shot sweep)
#   - needle_sweep (30s loop sweeping ALL UI params)
#   - dash_test (30s advanced dashboard test: min/max holds, all-blink, LED chase, async ramps)
#
# Requirements:
#   pip install python-can cantools fastapi "uvicorn[standard]" pyyaml
#
# Run:
#   uvicorn app:app --host 0.0.0.0 --port 8000

import asyncio
import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, Set

import can
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse

from dbc_codec import DbcCodec


# -------------------------
# Load configuration
# -------------------------
with open("frames.yaml", "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

BUS_CHANNEL = CFG["bus"]["channel"]
BUS_INTERFACE = CFG["bus"]["interface"]
DBC_PATH = CFG["dbc"]["file"]
MESSAGES = CFG["messages"]


# -------------------------
# ECU state (Throttle only + brake/clutch switches)
# -------------------------
@dataclass
class ECUState:
    # Core
    engine_on: bool = True
    rpm: int = 900
    throttle: int = 0          # %
    load: int = 10             # %
    speed: int = 0             # km/h

    # Temps
    coolant: int = 90          # °C
    iat: int = 25              # °C
    oil_temp: int = 90         # °C

    # Switches
    brake_switch: bool = False
    clutch_switch: bool = False

    # Status / diag
    mil: bool = False
    epc: bool = False
    cruise_active: bool = False
    dtc_count: int = 0
    fan_request: bool = False

    # Electrical / torque / pressures
    battery_voltage: float = 13.8
    lambda_value: float = 1.000  # "lambda" is a Python keyword
    driver_torque_req: float = 0.0
    indicated_torque: float = 0.0
    boost_pressure: float = 100.0
    map_kpa: float = 100.0

    # Cluster counter
    counter: int = 0


state = ECUState()

app = FastAPI()
clients: Set[WebSocket] = set()

bus = can.interface.Bus(channel=BUS_CHANNEL, interface=BUS_INTERFACE)
dbc = DbcCodec(DBC_PATH)


# -------------------------
# Scenario runner
# -------------------------
scenario_task: asyncio.Task | None = None
scenario_cancel = asyncio.Event()
scenario_name: str = "manual"


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _is_extended_from_frame_id(frame_id: int) -> bool:
    return frame_id > 0x7FF


def _pad_to_8(data: bytes) -> bytearray:
    if len(data) > 8:
        raise ValueError(f"DBC produced {len(data)} bytes; expected <= 8")
    return bytearray(list(data) + [0] * (8 - len(data)))


def _get_signal_map(m: Dict[str, Any]) -> Dict[str, str]:
    sigs = m.get("signals", {})
    if not isinstance(sigs, dict):
        raise ValueError("messages[].signals must be a dict mapping DBC_signal -> ECUState_field")
    return sigs


def _apply_update(update: Dict[str, Any]) -> None:
    """
    Apply incoming updates safely to ECUState.
    Minimal coercion + clamping to keep cantools encode happy.
    """
    global scenario_name

    # If no scenario is running, any manual input puts us in manual.
    if scenario_task is None or scenario_task.done():
        scenario_name = "manual"

    for k, v in update.items():
        if not hasattr(state, k):
            continue

        # booleans
        if k in ("engine_on", "mil", "epc", "cruise_active", "fan_request", "brake_switch", "clutch_switch"):
            setattr(state, k, bool(v))
            continue

        # integers
        if k in ("rpm", "throttle", "load", "speed", "coolant", "iat", "oil_temp", "dtc_count", "counter"):
            try:
                iv = int(v)
            except Exception:
                continue

            if k == "rpm":
                iv = max(0, min(7000, iv))
            elif k in ("throttle", "load"):
                iv = max(0, min(100, iv))
            elif k == "speed":
                iv = max(0, min(250, iv))
            elif k in ("coolant", "iat", "oil_temp"):
                iv = max(-40, min(150, iv))
            elif k == "dtc_count":
                iv = max(0, min(15, iv))
            elif k == "counter":
                iv = iv & 0xFF

            setattr(state, k, iv)
            continue

        # floats
        if k in ("battery_voltage", "lambda_value", "driver_torque_req", "indicated_torque", "boost_pressure", "map_kpa"):
            try:
                fv = float(v)
            except Exception:
                continue

            if k == "battery_voltage":
                fv = float(clamp(fv, 8.0, 18.0))
            elif k == "lambda_value":
                fv = float(clamp(fv, 0.5, 1.5))
            elif k in ("boost_pressure", "map_kpa"):
                fv = float(clamp(fv, 0.0, 510.0))
            elif k in ("driver_torque_req", "indicated_torque"):
                fv = float(clamp(fv, -500.0, 500.0))

            setattr(state, k, fv)
            continue


async def broadcast_state() -> None:
    payload = asdict(state) | {"scenario": scenario_name}
    dead = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def state_publisher() -> None:
    """
    Push state continuously so the UI animates during scenarios.
    10 Hz is enough for dashboards and keeps CPU/network low.
    """
    while True:
        if clients:
            await broadcast_state()
        await asyncio.sleep(0.1)


def _set_base():
    # baseline defaults that make UI stable
    state.engine_on = True
    state.mil = False
    state.epc = False
    state.cruise_active = False
    state.fan_request = False
    state.brake_switch = False
    state.clutch_switch = False
    state.dtc_count = 0


async def run_scenario(name: str) -> None:
    """
    Scenarios:
      - idle / cruise / wot: static presets
      - ramp: one-shot sweep (8s)
      - needle_sweep: continuous 30s loop sweeping ALL UI parameters (dashboard debug)
      - dash_test: advanced 30s loop (holds, blink, chase, async ramps)
    """
    global scenario_name
    scenario_name = name
    scenario_cancel.clear()
    _set_base()

    if name == "idle":
        state.rpm = 900
        state.throttle = 2
        state.load = 15
        state.speed = 0
        state.boost_pressure = 100
        state.map_kpa = 100
        state.coolant = 90
        state.iat = 25
        state.oil_temp = 90
        state.battery_voltage = 13.8
        state.lambda_value = 1.000
        state.driver_torque_req = 0.0
        state.indicated_torque = 0.0
        return

    if name == "cruise":
        state.speed = 90
        state.throttle = 18
        state.load = 35
        state.rpm = 2600
        state.boost_pressure = 110
        state.map_kpa = 110
        state.cruise_active = True
        state.lambda_value = 1.000
        state.driver_torque_req = 120.0
        state.indicated_torque = 140.0
        return

    if name == "wot":
        state.speed = 120
        state.throttle = 100
        state.load = 95
        state.rpm = 5200
        state.boost_pressure = 220
        state.map_kpa = 220
        state.lambda_value = 0.88
        state.driver_torque_req = 380.0
        state.indicated_torque = 420.0
        return

    if name == "ramp":
        # One-shot ramp: 900->6000 rpm (8s), throttle 10->100, speed 0->160
        steps = 160
        duration = 8.0
        for i in range(steps + 1):
            if scenario_cancel.is_set():
                return
            t = i / steps
            state.rpm = int(clamp(900 + t * (6000 - 900), 0, 7000))
            state.throttle = int(clamp(10 + t * (100 - 10), 0, 100))
            state.load = int(clamp(20 + t * (95 - 20), 0, 100))
            state.speed = int(clamp(t * 160, 0, 250))
            state.map_kpa = float(clamp(100 + t * 120, 0, 510))
            state.boost_pressure = state.map_kpa
            state.driver_torque_req = float(clamp(-20 + t * 420, -500, 500))
            state.indicated_torque = float(clamp(0 + t * 450, -500, 500))
            state.lambda_value = float(clamp(1.05 - t * 0.20, 0.5, 1.5))
            await asyncio.sleep(duration / steps)
        return

    if name == "needle_sweep":
        # Continuous 30s loop sweeping ALL UI parameters for dashboard debug.
        # Repeats until /api/scenario_stop is called.
        period = 30.0
        steps = 300  # 0.1s resolution
        dt = period / steps

        while not scenario_cancel.is_set():
            for i in range(steps + 1):
                if scenario_cancel.is_set():
                    return

                t = i / steps                    # 0..1
                tri = 1.0 - abs(2.0 * t - 1.0)   # 0..1..0
                s1 = 0.5 + 0.5 * math.sin(2.0 * math.pi * (t + 0.00))
                s2 = 0.5 + 0.5 * math.sin(2.0 * math.pi * (t + 0.33))
                s3 = 0.5 + 0.5 * math.sin(2.0 * math.pi * (t + 0.66))

                # Primary needles
                state.rpm = int(clamp(800 + tri * 6200, 0, 7000))       # 800..7000
                state.speed = int(clamp(tri * 250, 0, 250))             # 0..250
                state.throttle = int(clamp(2 + tri * 98, 0, 100))       # 2..100
                state.load = int(clamp(5 + tri * 95, 0, 100))           # 5..100

                # Temps / voltage (phase-shifted)
                state.coolant = int(clamp(70 + s1 * 55, -40, 150))      # 70..125
                state.iat = int(clamp(-5 + s2 * 70, -40, 150))          # -5..65
                state.oil_temp = int(clamp(65 + s3 * 70, -40, 150))     # 65..135
                state.battery_voltage = float(clamp(11.8 + s2 * 3.0, 8.0, 18.0))  # 11.8..14.8

                # Lambda: small oscillation around 1.0
                state.lambda_value = float(clamp(0.85 + s1 * 0.30, 0.5, 1.5))     # 0.85..1.15

                # Torques / pressures
                state.driver_torque_req = float(clamp(-60 + tri * 480, -500, 500))
                state.indicated_torque = float(clamp(-30 + tri * 520, -500, 500))
                state.map_kpa = float(clamp(100 + tri * 120, 0, 510))            # 100..220
                state.boost_pressure = state.map_kpa

                # DTC count 0..15
                state.dtc_count = int(clamp(round(tri * 15), 0, 15))

                # Switch choreography (distinct windows)
                state.brake_switch = (0.10 <= t < 0.18) or (0.60 <= t < 0.68)
                state.clutch_switch = (0.22 <= t < 0.30) or (0.72 <= t < 0.80)

                state.mil = (0.35 <= t < 0.45)
                state.epc = (0.46 <= t < 0.55)
                state.cruise_active = (0.82 <= t < 0.95)

                # Fan request reacts to coolant
                state.fan_request = (state.coolant >= 105)

                await asyncio.sleep(dt)
            # loop continues
        return

    if name == "dash_test":
        # Advanced dashboard test. Loop length: 30s.
        # Structure per loop:
        #   0-3s   : Hold MIN (idle-like), LEDs off
        #   3-11s  : Asynchronous ramps up (rpm/speed/throttle/load/boost/torque), temps lag
        #   11-14s : Hold MAX, "all LEDs blink"
        #   14-22s : Asynchronous ramps down, sequential LED chase
        #   22-30s : Staggered pulses + sanity checks (fan reacts to coolant), then back to min
        period = 30.0
        dt = 0.05  # 20 Hz internal resolution
        steps = int(period / dt)

        def tri01(x: float) -> float:
            # triangle 0..1..0 for x in [0..1]
            return 1.0 - abs(2.0 * x - 1.0)

        def smoothstep(x: float) -> float:
            x = clamp(x, 0.0, 1.0)
            return x * x * (3.0 - 2.0 * x)

        while not scenario_cancel.is_set():
            for n in range(steps):
                if scenario_cancel.is_set():
                    return

                tsec = n * dt
                phase = tsec / period  # 0..1

                # --- MIN HOLD (0..3s) ---
                if tsec < 3.0:
                    state.engine_on = True
                    state.rpm = 900
                    state.speed = 0
                    state.throttle = 2
                    state.load = 15
                    state.map_kpa = 100.0
                    state.boost_pressure = 100.0
                    state.driver_torque_req = 0.0
                    state.indicated_torque = 0.0
                    state.lambda_value = 1.000
                    state.battery_voltage = 13.8
                    state.coolant = 85
                    state.iat = 20
                    state.oil_temp = 85

                    state.mil = False
                    state.epc = False
                    state.cruise_active = False
                    state.fan_request = False
                    state.brake_switch = False
                    state.clutch_switch = False
                    state.dtc_count = 0

                    await asyncio.sleep(dt)
                    continue

                # --- RAMP UP (3..11s) ---
                if 3.0 <= tsec < 11.0:
                    x = smoothstep((tsec - 3.0) / 8.0)  # 0..1

                    # Asynchronous ramps (phase offsets)
                    x_rpm = smoothstep(clamp(x + 0.00, 0.0, 1.0))
                    x_spd = smoothstep(clamp(x + 0.18, 0.0, 1.0))
                    x_thr = smoothstep(clamp(x + 0.10, 0.0, 1.0))
                    x_load = smoothstep(clamp(x + 0.25, 0.0, 1.0))
                    x_map = smoothstep(clamp(x + 0.30, 0.0, 1.0))
                    x_tq = smoothstep(clamp(x + 0.20, 0.0, 1.0))

                    state.rpm = int(clamp(900 + x_rpm * 6100, 0, 7000))          # 900..7000
                    state.speed = int(clamp(x_spd * 240, 0, 250))                # 0..240
                    state.throttle = int(clamp(3 + x_thr * 97, 0, 100))          # 3..100
                    state.load = int(clamp(10 + x_load * 90, 0, 100))            # 10..100
                    state.map_kpa = float(clamp(100 + x_map * 140, 0, 510))      # 100..240
                    state.boost_pressure = state.map_kpa

                    state.driver_torque_req = float(clamp(-20 + x_tq * 430, -500, 500))
                    state.indicated_torque = float(clamp(0 + x_tq * 460, -500, 500))

                    # Lambda dips with load
                    state.lambda_value = float(clamp(1.05 - x_load * 0.25, 0.5, 1.5))

                    # Voltage ripple
                    state.battery_voltage = float(
                        clamp(13.2 + 0.7 * math.sin(2.0 * math.pi * (phase * 2.0)), 8.0, 18.0)
                    )

                    # Temps lag (slow)
                    state.coolant = int(clamp(85 + x * 25, -40, 150))            # 85..110
                    state.oil_temp = int(clamp(85 + x * 35, -40, 150))           # 85..120
                    state.iat = int(clamp(15 + x * 25, -40, 150))                # 15..40

                    # brake/clutch pulses
                    state.brake_switch = (3.8 <= tsec < 4.3) or (6.2 <= tsec < 6.7)
                    state.clutch_switch = (4.6 <= tsec < 5.1) or (7.0 <= tsec < 7.5)

                    # LEDs mostly off during ramp-up
                    state.mil = False
                    state.epc = False
                    state.cruise_active = False

                    # fan_request reacts to coolant
                    state.fan_request = (state.coolant >= 105)

                    # DTC count rises a bit
                    state.dtc_count = int(clamp(round(x * 6), 0, 15))

                    await asyncio.sleep(dt)
                    continue

                # --- HOLD MAX (11..14s) with ALL BLINK ---
                if 11.0 <= tsec < 14.0:
                    state.rpm = 6900
                    state.speed = 250
                    state.throttle = 100
                    state.load = 100
                    state.map_kpa = 240.0
                    state.boost_pressure = 240.0
                    state.driver_torque_req = 420.0
                    state.indicated_torque = 460.0
                    state.lambda_value = 0.85
                    state.battery_voltage = 14.4
                    state.dtc_count = 15

                    # temps drift slightly up
                    state.coolant = int(clamp(state.coolant + 0.2, -40, 150))
                    state.oil_temp = int(clamp(state.oil_temp + 0.3, -40, 150))
                    state.iat = int(clamp(state.iat + 0.1, -40, 150))

                    blink = (int((tsec - 11.0) / 0.25) % 2) == 0  # 4 Hz
                    state.mil = blink
                    state.epc = blink
                    state.cruise_active = blink
                    state.fan_request = blink
                    state.brake_switch = blink
                    state.clutch_switch = blink

                    await asyncio.sleep(dt)
                    continue

                # --- RAMP DOWN (14..22s) with LED CHASE ---
                if 14.0 <= tsec < 22.0:
                    x = smoothstep((tsec - 14.0) / 8.0)  # 0..1
                    y = 1.0 - x

                    # Asynchronous down ramps (different offsets)
                    y_rpm = smoothstep(clamp(y + 0.10, 0.0, 1.0))
                    y_spd = smoothstep(clamp(y + 0.00, 0.0, 1.0))
                    y_thr = smoothstep(clamp(y + 0.20, 0.0, 1.0))
                    y_load = smoothstep(clamp(y + 0.05, 0.0, 1.0))
                    y_map = smoothstep(clamp(y + 0.12, 0.0, 1.0))
                    y_tq = smoothstep(clamp(y + 0.08, 0.0, 1.0))

                    state.rpm = int(clamp(900 + y_rpm * 6100, 0, 7000))
                    state.speed = int(clamp(y_spd * 240, 0, 250))
                    state.throttle = int(clamp(3 + y_thr * 97, 0, 100))
                    state.load = int(clamp(10 + y_load * 90, 0, 100))
                    state.map_kpa = float(clamp(100 + y_map * 140, 0, 510))
                    state.boost_pressure = state.map_kpa
                    state.driver_torque_req = float(clamp(-20 + y_tq * 430, -500, 500))
                    state.indicated_torque = float(clamp(0 + y_tq * 460, -500, 500))

                    state.lambda_value = float(clamp(0.90 + (1.0 - y_load) * 0.20, 0.5, 1.5))
                    state.battery_voltage = float(
                        clamp(13.8 + 0.4 * math.sin(2.0 * math.pi * (phase * 3.0)), 8.0, 18.0)
                    )

                    # Temps cool down slowly
                    state.coolant = int(clamp(state.coolant - 0.08, -40, 150))
                    state.oil_temp = int(clamp(state.oil_temp - 0.10, -40, 150))
                    state.iat = int(clamp(state.iat - 0.05, -40, 150))

                    # LED chase (each 0.5s a different LED)
                    idx = int((tsec - 14.0) / 0.5) % 6
                    state.mil = (idx == 0)
                    state.epc = (idx == 1)
                    state.cruise_active = (idx == 2)
                    state.fan_request = (idx == 3) or (state.coolant >= 105)
                    state.brake_switch = (idx == 4)
                    state.clutch_switch = (idx == 5)

                    # DTC count decreases
                    state.dtc_count = int(clamp(round(y * 15), 0, 15))

                    await asyncio.sleep(dt)
                    continue

                # --- PULSES / SANITY (22..30s) ---
                if 22.0 <= tsec < 30.0:
                    x = (tsec - 22.0) / 8.0  # 0..1

                    # three desynchronized pulses
                    p1 = tri01((x + 0.00) % 1.0)
                    p2 = tri01((x + 0.33) % 1.0)
                    p3 = tri01((x + 0.66) % 1.0)

                    state.rpm = int(clamp(900 + p1 * 5200, 0, 7000))
                    state.speed = int(clamp(p2 * 200, 0, 250))
                    state.throttle = int(clamp(5 + p3 * 95, 0, 100))
                    state.load = int(clamp(10 + p1 * 90, 0, 100))
                    state.map_kpa = float(clamp(100 + p3 * 140, 0, 510))
                    state.boost_pressure = state.map_kpa
                    state.driver_torque_req = float(clamp(-50 + p2 * 420, -500, 500))
                    state.indicated_torque = float(clamp(-20 + p1 * 460, -500, 500))
                    state.lambda_value = float(clamp(0.95 + 0.15 * math.sin(2.0 * math.pi * (x * 2.0)), 0.5, 1.5))
                    state.battery_voltage = float(clamp(12.4 + 2.0 * p2, 8.0, 18.0))  # 12.4..14.4

                    # temps: oscillation around mid
                    state.coolant = int(clamp(95 + 12 * math.sin(2.0 * math.pi * (x * 1.0)), -40, 150))
                    state.oil_temp = int(clamp(100 + 15 * math.sin(2.0 * math.pi * (x * 0.8 + 0.2)), -40, 150))
                    state.iat = int(clamp(25 + 20 * math.sin(2.0 * math.pi * (x * 1.3 + 0.1)), -40, 150))

                    # fan reacts
                    state.fan_request = (state.coolant >= 105)

                    # toggles: quick pulses
                    state.brake_switch = (int((tsec - 22.0) / 0.7) % 2) == 0
                    state.clutch_switch = (int((tsec - 22.0) / 1.1) % 2) == 0

                    # MIL/EPC alternating
                    alt = (int((tsec - 22.0) / 0.6) % 2) == 0
                    state.mil = alt
                    state.epc = not alt
                    state.cruise_active = (p2 > 0.6)

                    state.dtc_count = int(clamp(round(p3 * 15), 0, 15))

                    await asyncio.sleep(dt)
                    continue

            # loop repeats every 30s
        return

    raise ValueError(f"Unknown scenario '{name}'")


async def stop_scenario() -> None:
    global scenario_task, scenario_name
    scenario_cancel.set()
    if scenario_task and not scenario_task.done():
        try:
            await scenario_task
        except Exception:
            pass
    scenario_task = None
    scenario_name = "manual"


# -------------------------
# CAN scheduler
# -------------------------
async def can_scheduler() -> None:
    tasks = []

    # Create periodic tasks once
    for m in MESSAGES:
        dbc_message_name = m["dbc_message"]
        signal_map = _get_signal_map(m)

        arb_id, data, _dlc = dbc.encode(dbc_message_name, signal_map, state)
        is_ext = bool(m.get("extended")) if "extended" in m else _is_extended_from_frame_id(arb_id)

        msg = can.Message(
            arbitration_id=arb_id,
            data=_pad_to_8(data),
            is_extended_id=is_ext,
        )

        period = float(m["period_ms"]) / 1000.0
        task = bus.send_periodic(msg, period)
        if task is None:
            raise RuntimeError(f"Failed to start periodic task for '{m.get('name', dbc_message_name)}'")
        tasks.append((m, msg, task))

    # Update loop
    try:
        while True:
            state.counter = (state.counter + 1) & 0xFF

            for (m, msg, task) in tasks:
                dbc_message_name = m["dbc_message"]
                signal_map = _get_signal_map(m)

                arb_id, data, _dlc = dbc.encode(dbc_message_name, signal_map, state)
                msg.arbitration_id = arb_id
                msg.is_extended_id = bool(m.get("extended")) if "extended" in m else _is_extended_from_frame_id(arb_id)
                msg.data = _pad_to_8(data)
                task.modify_data(msg)

            await asyncio.sleep(0.02)
    finally:
        for (_, _, task) in tasks:
            task.stop()


# -------------------------
# Web UI (Beautiful dark + scenario buttons)
# -------------------------
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ECU Emulator (DBC)</title>
  <style>
    :root{
      --bg:#0b1020;
      --panel:rgba(255,255,255,.06);
      --stroke:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92);
      --muted:rgba(255,255,255,.68);
      --muted2:rgba(255,255,255,.52);
      --good:#27d17f;
      --bad:#ff5c7a;
      --accent:#8b5cf6;
      --shadow:0 18px 50px rgba(0,0,0,.45);
      --radius:18px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Arial;
      background:
        radial-gradient(1200px 700px at 15% -10%, rgba(139,92,246,0.35), transparent 55%),
        radial-gradient(900px 600px at 90% 0%, rgba(34,211,238,0.25), transparent 55%),
        radial-gradient(900px 800px at 40% 110%, rgba(39,209,127,0.14), transparent 55%),
        var(--bg);
      color:var(--text);
      line-height:1.25;
    }
    .wrap{max-width:1100px;margin:22px auto 40px;padding:0 16px}
    .topbar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
    .brand{display:flex;flex-direction:column;gap:3px}
    .brand h1{font-size:18px;margin:0;letter-spacing:.2px;font-weight:750}
    .brand .sub{font-size:12px;color:var(--muted2)}
    .pillrow{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:flex-end}
    .pill{
      display:inline-flex;align-items:center;gap:8px;padding:8px 12px;
      background:var(--panel);border:1px solid var(--stroke);border-radius:999px;
      box-shadow:var(--shadow);backdrop-filter:blur(10px);font-size:12px;color:var(--muted);
      user-select:none
    }
    .dot{width:10px;height:10px;border-radius:999px;background:var(--muted2);box-shadow:0 0 0 3px rgba(255,255,255,.05)}
    .dot.good{background:var(--good)} .dot.bad{background:var(--bad)}
    .btn{
      appearance:none;border:1px solid var(--stroke);
      background:linear-gradient(180deg, rgba(255,255,255,.09), rgba(255,255,255,.05));
      color:var(--text);padding:10px 14px;border-radius:12px;cursor:pointer;
      box-shadow:var(--shadow);backdrop-filter:blur(10px);
      font-weight:750;letter-spacing:.2px;transition:transform .06s ease;
      white-space:nowrap;
    }
    .btn:active{transform:translateY(1px)}
    .btn.primary{border-color:rgba(139,92,246,.35);background:linear-gradient(180deg, rgba(139,92,246,.35), rgba(139,92,246,.18))}
    .btn.small{padding:8px 10px;border-radius:10px;font-size:12px}
    .btn.danger{border-color:rgba(255,92,122,.35);background:linear-gradient(180deg, rgba(255,92,122,.30), rgba(255,92,122,.12))}
    .grid{display:grid;grid-template-columns:1.15fr .85fr;gap:14px}
    @media (max-width: 920px){.grid{grid-template-columns:1fr}}
    .card{
      background:linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.04));
      border:1px solid var(--stroke);border-radius:var(--radius);
      box-shadow:var(--shadow);backdrop-filter:blur(12px);overflow:hidden;
    }
    .card .head{
      padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:10px;
      border-bottom:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);
    }
    .card .head h2{margin:0;font-size:14px;letter-spacing:.2px;font-weight:850}
    .meta{font-size:12px;color:var(--muted2)}
    .card .body{padding:14px 16px 16px}
    .row{display:grid;grid-template-columns:160px 1fr 90px;gap:12px;align-items:center;margin:12px 0}
    .label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:3px}
    .label strong{font-size:12px;color:var(--text);font-weight:850;letter-spacing:.15px}
    .value{text-align:right;font-variant-numeric:tabular-nums;font-weight:850;color:rgba(255,255,255,.90)}
    .value small{color:var(--muted2);font-weight:700}
    input[type="range"]{
      width:100%;appearance:none;height:10px;border-radius:999px;
      background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.10);outline:none
    }
    input[type="range"]::-webkit-slider-thumb{
      appearance:none;width:18px;height:18px;border-radius:999px;
      background:radial-gradient(circle at 35% 35%, rgba(255,255,255,.95), rgba(255,255,255,.35));
      border:1px solid rgba(0,0,0,.35);box-shadow:0 10px 20px rgba(0,0,0,.35);cursor:pointer
    }
    .toggles{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
    .toggle{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border-radius:14px;
      background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10)}
    .toggle .tlabel{display:flex;flex-direction:column;gap:3px}
    .toggle .tlabel span{font-size:12px;color:var(--muted)}
    .toggle .tlabel strong{font-size:12px;font-weight:850;letter-spacing:.2px}
    .switch{position:relative;width:44px;height:26px;display:inline-block}
    .switch input{display:none}
    .slider{position:absolute;cursor:pointer;inset:0;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.16);
      transition:.15s ease;border-radius:999px}
    .slider:before{position:absolute;content:"";height:20px;width:20px;left:3px;top:2px;background:rgba(255,255,255,.92);
      border-radius:999px;transition:.15s ease;box-shadow:0 10px 18px rgba(0,0,0,.35)}
    .switch input:checked + .slider{background:rgba(139,92,246,.35);border-color:rgba(139,92,246,.45)}
    .switch input:checked + .slider:before{transform:translateX(18px)}
    .scenarioBtns{display:flex;flex-wrap:wrap;gap:8px}
    .tag{
      display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:999px;
      background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10);
      color:var(--muted);font-size:12px
    }
    .tag b{color:rgba(255,255,255,.90)}
    code{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.10);padding:4px 8px;border-radius:10px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">
        <h1>ECU Emulator (DBC)</h1>
        <div class="sub">Dashboard debug • Scenarios • SocketCAN • FastAPI • WebSocket</div>
      </div>
      <div class="pillrow">
        <div class="pill"><span class="dot" id="wsDot"></span><span id="wsState">disconnected</span></div>
        <button class="btn primary" id="toggleEngine">Engine: ON</button>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px;">
      <div class="head"><h2>Scenarios</h2><div class="meta">Presets + sweep + advanced test</div></div>
      <div class="body" style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;">
        <div class="scenarioBtns">
          <button class="btn small" id="scIdle">Idle</button>
          <button class="btn small" id="scCruise">Cruise</button>
          <button class="btn small" id="scWot">WOT</button>
          <button class="btn small" id="scRamp">Ramp</button>
          <button class="btn small" id="scNeedle">Needle sweep</button>
          <button class="btn small" id="scDash">Dash test</button>
          <button class="btn small danger" id="scStop">Stop</button>
        </div>
        <div class="tag">Active: <b id="scenarioName">manual</b></div>
      </div>
      <div class="body" style="padding-top:0;color:rgba(255,255,255,.55);font-size:12px;">
        Dash test adds holds, all-blink, LED chase, and asynchronous gauge patterns.
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="head"><h2>Powertrain</h2><div class="meta">Fast controls</div></div>
        <div class="body">
          <div class="row">
            <div class="label"><strong>RPM</strong><span>Engine speed</span></div>
            <input id="rpm" type="range" min="0" max="7000" step="10" value="900">
            <div class="value"><span id="rpmVal">900</span> <small>rpm</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Throttle</strong><span>DBW request</span></div>
            <input id="throttle" type="range" min="0" max="100" step="1" value="0">
            <div class="value"><span id="throttleVal">0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Load</strong><span>Engine load</span></div>
            <input id="load" type="range" min="0" max="100" step="1" value="10">
            <div class="value"><span id="loadVal">10</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Speed</strong><span>Vehicle speed</span></div>
            <input id="speed" type="range" min="0" max="250" step="1" value="0">
            <div class="value"><span id="speedVal">0</span> <small>km/h</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Coolant</strong><span>Engine temp</span></div>
            <input id="coolant" type="range" min="-40" max="150" step="1" value="90">
            <div class="value"><span id="coolantVal">90</span> <small>°C</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>IAT</strong><span>Intake air temp</span></div>
            <input id="iat" type="range" min="-40" max="150" step="1" value="25">
            <div class="value"><span id="iatVal">25</span> <small>°C</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Oil Temp</strong><span>Oil temperature</span></div>
            <input id="oil_temp" type="range" min="-40" max="150" step="1" value="90">
            <div class="value"><span id="oilTempVal">90</span> <small>°C</small></div>
          </div>

          <div class="toggles">
            <div class="toggle">
              <div class="tlabel"><strong>Brake switch</strong><span>Pedal contact</span></div>
              <label class="switch"><input id="brake_switch" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>Clutch switch</strong><span>Pedal contact</span></div>
              <label class="switch"><input id="clutch_switch" type="checkbox"><span class="slider"></span></label>
            </div>
          </div>

          <div class="toggles" style="margin-top:12px;">
            <div class="toggle">
              <div class="tlabel"><strong>MIL</strong><span>Check engine</span></div>
              <label class="switch"><input id="mil" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>EPC</strong><span>Electronic power control</span></div>
              <label class="switch"><input id="epc" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>Cruise</strong><span>Cruise active</span></div>
              <label class="switch"><input id="cruise_active" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>Fan request</strong><span>Cooling fan</span></div>
              <label class="switch"><input id="fan_request" type="checkbox"><span class="slider"></span></label>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="head"><h2>Electrical & Torque</h2><div class="meta">Status</div></div>
        <div class="body">
          <div class="row">
            <div class="label"><strong>Battery</strong><span>System voltage</span></div>
            <input id="battery_voltage" type="range" min="8" max="18" step="0.1" value="13.8">
            <div class="value"><span id="battVal">13.8</span> <small>V</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Lambda</strong><span>Mixture</span></div>
            <input id="lambda_value" type="range" min="0.5" max="1.5" step="0.001" value="1.000">
            <div class="value"><span id="lambdaVal">1.000</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Driver torque</strong><span>Requested</span></div>
            <input id="driver_torque_req" type="range" min="-500" max="500" step="0.1" value="0.0">
            <div class="value"><span id="tqReqVal">0.0</span> <small>Nm</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Indicated torque</strong><span>Calculated</span></div>
            <input id="indicated_torque" type="range" min="-500" max="500" step="0.1" value="0.0">
            <div class="value"><span id="tqIndVal">0.0</span> <small>Nm</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Boost</strong><span>Charge pressure</span></div>
            <input id="boost_pressure" type="range" min="0" max="510" step="1" value="100">
            <div class="value"><span id="boostVal">100</span> <small>kPa</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>MAP</strong><span>Manifold abs</span></div>
            <input id="map_kpa" type="range" min="0" max="510" step="1" value="100">
            <div class="value"><span id="mapVal">100</span> <small>kPa</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>DTC count</strong><span>Stored faults</span></div>
            <input id="dtc_count" type="range" min="0" max="15" step="1" value="0">
            <div class="value"><span id="dtcVal">0</span></div>
          </div>

          <div style="margin-top:12px;color:rgba(255,255,255,.55);font-size:12px;">
            DBC: <code id="dbcPath">__DBC_PATH__</code>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
  document.getElementById("dbcPath").textContent = "__DBC_PATH__";

  const ws = new WebSocket(`ws://${location.host}/ws`);
  const wsState = document.getElementById("wsState");
  const wsDot = document.getElementById("wsDot");
  const scenarioName = document.getElementById("scenarioName");

  const toggleEngine = document.getElementById("toggleEngine");
  let engine_on = true;

  function setWs(status) {
    wsState.textContent = status;
    wsDot.className = "dot " + (status === "connected" ? "good" : status === "error" ? "bad" : "");
  }
  function setEngine(on) {
    engine_on = !!on;
    toggleEngine.textContent = `Engine: ${engine_on ? "ON" : "OFF"}`;
  }
  function sendUpdate(obj) {
    if (ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  function bindRange(id, valId, key, isFloat=false, digits=3) {
    const el = document.getElementById(id);
    const out = document.getElementById(valId);
    el.addEventListener("input", () => {
      out.textContent = isFloat ? Number(el.value).toFixed(digits) : el.value;
      const obj = {};
      obj[key] = isFloat ? Number(el.value) : parseInt(el.value);
      sendUpdate(obj);
    });
    return el;
  }
  function bindSwitch(id, key) {
    const el = document.getElementById(id);
    el.addEventListener("change", () => {
      const obj = {};
      obj[key] = el.checked;
      sendUpdate(obj);
    });
    return el;
  }

  async function startScenario(name) {
    await fetch(`/api/scenario/${name}`, { method: "POST" });
  }
  async function stopScenario() {
    await fetch(`/api/scenario_stop`, { method: "POST" });
  }

  document.getElementById("scIdle").addEventListener("click", () => startScenario("idle"));
  document.getElementById("scCruise").addEventListener("click", () => startScenario("cruise"));
  document.getElementById("scWot").addEventListener("click", () => startScenario("wot"));
  document.getElementById("scRamp").addEventListener("click", () => startScenario("ramp"));
  document.getElementById("scNeedle").addEventListener("click", () => startScenario("needle_sweep"));
  document.getElementById("scDash").addEventListener("click", () => startScenario("dash_test"));
  document.getElementById("scStop").addEventListener("click", () => stopScenario());

  const rpm = bindRange("rpm", "rpmVal", "rpm", false, 0);
  const throttle = bindRange("throttle", "throttleVal", "throttle", false, 0);
  const load = bindRange("load", "loadVal", "load", false, 0);
  const speed = bindRange("speed", "speedVal", "speed", false, 0);
  const coolant = bindRange("coolant", "coolantVal", "coolant", false, 0);
  const iat = bindRange("iat", "iatVal", "iat", false, 0);
  const oil_temp = bindRange("oil_temp", "oilTempVal", "oil_temp", false, 0);

  const battery_voltage = bindRange("battery_voltage", "battVal", "battery_voltage", true, 1);
  const lambda_value = bindRange("lambda_value", "lambdaVal", "lambda_value", true, 3);
  const driver_torque_req = bindRange("driver_torque_req", "tqReqVal", "driver_torque_req", true, 1);
  const indicated_torque = bindRange("indicated_torque", "tqIndVal", "indicated_torque", true, 1);
  const boost_pressure = bindRange("boost_pressure", "boostVal", "boost_pressure", true, 0);
  const map_kpa = bindRange("map_kpa", "mapVal", "map_kpa", true, 0);
  const dtc_count = bindRange("dtc_count", "dtcVal", "dtc_count", false, 0);

  const brake_switch = bindSwitch("brake_switch", "brake_switch");
  const clutch_switch = bindSwitch("clutch_switch", "clutch_switch");
  const mil = bindSwitch("mil", "mil");
  const epc = bindSwitch("epc", "epc");
  const cruise_active = bindSwitch("cruise_active", "cruise_active");
  const fan_request = bindSwitch("fan_request", "fan_request");

  toggleEngine.addEventListener("click", () => {
    setEngine(!engine_on);
    sendUpdate({engine_on});
  });

  ws.onopen = () => setWs("connected");
  ws.onclose = () => setWs("disconnected");
  ws.onerror = () => setWs("error");

  ws.onmessage = (ev) => {
    const s = JSON.parse(ev.data);

    setEngine(s.engine_on);
    if (typeof s.scenario === "string") scenarioName.textContent = s.scenario;

    rpm.value = s.rpm; document.getElementById("rpmVal").textContent = s.rpm;
    throttle.value = s.throttle; document.getElementById("throttleVal").textContent = s.throttle;
    load.value = s.load; document.getElementById("loadVal").textContent = s.load;
    speed.value = s.speed; document.getElementById("speedVal").textContent = s.speed;

    coolant.value = s.coolant; document.getElementById("coolantVal").textContent = s.coolant;
    iat.value = s.iat; document.getElementById("iatVal").textContent = s.iat;
    oil_temp.value = s.oil_temp; document.getElementById("oilTempVal").textContent = s.oil_temp;

    battery_voltage.value = s.battery_voltage; document.getElementById("battVal").textContent = Number(s.battery_voltage).toFixed(1);
    lambda_value.value = s.lambda_value; document.getElementById("lambdaVal").textContent = Number(s.lambda_value).toFixed(3);
    driver_torque_req.value = s.driver_torque_req; document.getElementById("tqReqVal").textContent = Number(s.driver_torque_req).toFixed(1);
    indicated_torque.value = s.indicated_torque; document.getElementById("tqIndVal").textContent = Number(s.indicated_torque).toFixed(1);
    boost_pressure.value = s.boost_pressure; document.getElementById("boostVal").textContent = Number(s.boost_pressure).toFixed(0);
    map_kpa.value = s.map_kpa; document.getElementById("mapVal").textContent = Number(s.map_kpa).toFixed(0);

    dtc_count.value = s.dtc_count; document.getElementById("dtcVal").textContent = s.dtc_count;

    brake_switch.checked = !!s.brake_switch;
    clutch_switch.checked = !!s.clutch_switch;
    mil.checked = !!s.mil;
    epc.checked = !!s.epc;
    cruise_active.checked = !!s.cruise_active;
    fan_request.checked = !!s.fan_request;
  };
</script>
</body>
</html>
"""

# Inject DBC path without brace templating issues
INDEX_HTML = INDEX_HTML.replace("__DBC_PATH__", DBC_PATH)


# -------------------------
# API routes
# -------------------------
@app.get("/")
def index():
    return HTMLResponse(INDEX_HTML)


@app.get("/api/state")
def get_state():
    return asdict(state) | {"scenario": scenario_name}


@app.post("/api/scenario/{name}")
async def api_start_scenario(name: str):
    global scenario_task, scenario_name

    allowed = {"idle", "cruise", "wot", "ramp", "needle_sweep", "dash_test"}
    if name not in allowed:
        raise HTTPException(status_code=400, detail="Unknown scenario")

    # stop previous
    await stop_scenario()

    # publish scenario name immediately (before task runs)
    scenario_name = name
    await broadcast_state()

    async def _runner():
        try:
            await run_scenario(name)
        finally:
            await broadcast_state()

    scenario_task = asyncio.create_task(_runner())
    return {"ok": True, "scenario": name}


@app.post("/api/scenario_stop")
async def api_stop_scenario():
    await stop_scenario()
    await broadcast_state()
    return {"ok": True}


@app.on_event("startup")
async def startup():
    asyncio.create_task(can_scheduler())
    asyncio.create_task(state_publisher())


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_json(asdict(state) | {"scenario": scenario_name})
    try:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict):
                _apply_update(msg)
                await broadcast_state()
    except WebSocketDisconnect:
        clients.discard(ws)
