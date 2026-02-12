# app.py
# ECU Emulator (DBC) - Dark UI + scenarios + live state publisher (10 Hz)
# Scenarios:
#   - idle / cruise / wot (static presets)
#   - ramp (8s one-shot sweep)
#   - needle_sweep (30s loop sweeping ALL UI params)
#   - dash_test (30s advanced dashboard test: min/max holds, all-blink, LED chase, async ramps)
#   - warning_blink (continuous all-warning blinker for cluster validation)
#
# Requirements:
#   pip install python-can cantools fastapi "uvicorn[standard]" pyyaml
#
# Run:
#   uvicorn app:app --host 0.0.0.0 --port 8000

import asyncio
import math
import os
import time
import logging

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

    # --- Extended sensors / turbo / flex / dsg ---
    iat: float = 30.0
    afr: float = 14.7
    fuel_pressure_kpa: float = 400.0
    oil_pressure_kpa: float = 250.0
    egt_c: float = 650.0
    
    boost_target_kpa: float = 180.0
    boost_error_kpa: float = 0.0
    wgdc_pct: float = 35.0
    n75_pct: float = 35.0
    turbo_speed_krpm: float = 80.0
    
    ign_angle_deg: float = 10.0
    dwell_ms: float = 2.5
    inj_pw_ms: float = 3.0
    fuel_trim_st: float = 0.0
    fuel_trim_lt: float = 0.0
    lambda_target: float = 1.00
    fuel_temp_c: float = 25.0
    
    ethanol_pct: float = 0.0
    stoich_afr: float = 14.7
    flex_mode: int = 0
    fuel_density: float = 7.4
    
    knock_retard_deg: float = 0.0
    knock_count: int = 0
    iat_comp_pct: float = 0.0
    egt_alarm: int = 0
    oil_press_alarm: int = 0
    
    dsg_gear: int = 1
    dsg_clutch_slip_rpm: int = 0
    dsg_trans_temp_c: float = 60.0
    dsg_mode: int = 1
    dsg_shift_request: int = 0
    launch_active: int = 0
    tcu_ready: int = 1
    

state = ECUState()

app = FastAPI()
clients: Set[WebSocket] = set()

bus = can.interface.Bus(channel=BUS_CHANNEL, interface=BUS_INTERFACE)
dbc = DbcCodec(DBC_PATH)



# -------------------------
# TX observability
# -------------------------
TX_COUNT = 0
TX_LAST_TS = 0.0

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
        if k in (
            "rpm", "throttle", "load", "speed", "coolant", "iat", "oil_temp", "dtc_count", "counter",
            "flex_mode", "egt_alarm", "oil_press_alarm", "dsg_gear", "dsg_clutch_slip_rpm",
            "dsg_mode", "dsg_shift_request", "launch_active", "tcu_ready", "knock_count",
        ):
            try:
                iv = int(v)
            except Exception:
                continue

            if k == "rpm":
                iv = max(0, min(9000, iv))
            elif k in ("throttle", "load"):
                iv = max(0, min(100, iv))
            elif k == "speed":
                iv = max(0, min(300, iv))
            elif k in ("coolant", "iat", "oil_temp"):
                iv = max(-40, min(215, iv))
            elif k == "dtc_count":
                iv = max(0, min(15, iv))
            elif k == "counter":
                iv = iv & 0xFF
            elif k == "flex_mode":
                iv = max(0, min(3, iv))
            elif k in ("egt_alarm", "oil_press_alarm", "launch_active", "tcu_ready"):
                iv = 1 if iv else 0
            elif k == "dsg_gear":
                iv = max(0, min(10, iv))
            elif k == "dsg_clutch_slip_rpm":
                iv = max(0, min(5000, iv))
            elif k == "dsg_mode":
                iv = max(0, min(7, iv))
            elif k == "dsg_shift_request":
                iv = max(0, min(3, iv))
            elif k == "knock_count":
                iv = max(0, min(65535, iv))

            setattr(state, k, iv)
            continue

        # floats
        if k in (
            "battery_voltage", "lambda_value", "driver_torque_req", "indicated_torque", "boost_pressure", "map_kpa",
            "afr", "fuel_pressure_kpa", "oil_pressure_kpa", "egt_c", "boost_target_kpa", "boost_error_kpa",
            "wgdc_pct", "n75_pct", "turbo_speed_krpm", "ign_angle_deg", "dwell_ms", "inj_pw_ms",
            "fuel_trim_st", "fuel_trim_lt", "lambda_target", "fuel_temp_c", "ethanol_pct", "stoich_afr",
            "fuel_density", "knock_retard_deg", "iat_comp_pct", "dsg_trans_temp_c",
        ):
            try:
                fv = float(v)
            except Exception:
                continue

            if k == "battery_voltage":
                fv = float(clamp(fv, 8.0, 18.0))
            elif k == "lambda_value":
                fv = float(clamp(fv, 0.5, 1.5))
            elif k in ("boost_pressure", "map_kpa"):
                fv = float(clamp(fv, 0.0, 300.0))
            elif k in ("driver_torque_req", "indicated_torque"):
                fv = float(clamp(fv, -500.0, 500.0))
            elif k == "afr":
                fv = float(clamp(fv, 0.0, 25.5))
            elif k in ("fuel_pressure_kpa", "oil_pressure_kpa"):
                fv = float(clamp(fv, 0.0, 2000.0))
            elif k == "egt_c":
                fv = float(clamp(fv, 0.0, 1200.0))
            elif k == "boost_target_kpa":
                fv = float(clamp(fv, 0.0, 300.0))
            elif k == "boost_error_kpa":
                fv = float(clamp(fv, -300.0, 300.0))
            elif k in ("wgdc_pct", "n75_pct"):
                fv = float(clamp(fv, 0.0, 100.0))
            elif k == "turbo_speed_krpm":
                fv = float(clamp(fv, 0.0, 400.0))
            elif k == "ign_angle_deg":
                fv = float(clamp(fv, -30.0, 60.0))
            elif k in ("dwell_ms", "inj_pw_ms"):
                fv = float(clamp(fv, 0.0, 12.75))
            elif k in ("fuel_trim_st", "fuel_trim_lt"):
                fv = float(clamp(fv, -100.0, 100.0))
            elif k == "lambda_target":
                fv = float(clamp(fv, 0.0, 2.55))
            elif k == "fuel_temp_c":
                fv = float(clamp(fv, -40.0, 215.0))
            elif k == "ethanol_pct":
                fv = float(clamp(fv, 0.0, 100.0))
            elif k == "stoich_afr":
                fv = float(clamp(fv, 8.0, 20.0))
            elif k == "fuel_density":
                fv = float(clamp(fv, 0.0, 25.5))
            elif k == "knock_retard_deg":
                fv = float(clamp(fv, 0.0, 20.0))
            elif k == "iat_comp_pct":
                fv = float(clamp(fv, -50.0, 50.0))
            elif k == "dsg_trans_temp_c":
                fv = float(clamp(fv, -40.0, 215.0))

            setattr(state, k, fv)
            continue


def _sync_extended_signals() -> None:
    """Keep extended DBC signals coherent from core engine state."""
    # Boost control
    state.boost_target_kpa = float(clamp(100.0 + state.throttle * 1.6, 100.0, 300.0))
    state.boost_error_kpa = float(clamp(state.boost_target_kpa - state.map_kpa, -300.0, 300.0))
    state.wgdc_pct = float(clamp(20.0 + state.throttle * 0.8, 0.0, 100.0))
    state.n75_pct = state.wgdc_pct
    state.turbo_speed_krpm = float(clamp((state.rpm / 40.0) + max(0.0, state.map_kpa - 100.0) * 0.45, 0.0, 400.0))

    # Flex fuel / stoich model
    state.stoich_afr = float(clamp(14.7 - (state.ethanol_pct / 100.0) * 5.0, 8.0, 20.0))
    state.flex_mode = 2 if state.ethanol_pct >= 70.0 else 1 if state.ethanol_pct > 5.0 else 0
    state.fuel_density = float(clamp(7.4 - (state.ethanol_pct / 100.0) * 1.0, 0.0, 25.5))

    # Combustion / fueling
    state.lambda_target = 1.00 if state.throttle < 20 else 0.85
    state.lambda_value = float(clamp(state.lambda_value, 0.70, 1.30))
    state.afr = float(clamp(state.stoich_afr * state.lambda_value, 0.0, 25.5))
    state.fuel_trim_st = float(clamp((state.lambda_target - state.lambda_value) * 100.0, -100.0, 100.0))
    state.fuel_trim_lt = float(clamp(state.fuel_trim_lt * 0.98 + state.fuel_trim_st * 0.02, -100.0, 100.0))
    state.inj_pw_ms = float(clamp(1.2 + state.load * 0.055, 0.0, 12.75))
    state.dwell_ms = float(clamp(2.0 + state.rpm / 4000.0, 0.0, 12.75))
    state.ign_angle_deg = float(clamp(18.0 - max(0.0, state.map_kpa - 100.0) * 0.06 - state.knock_retard_deg * 0.5, -30.0, 60.0))
    state.fuel_temp_c = float(clamp(25.0 + state.load * 0.45, -40.0, 215.0))

    # Pressures / temps / compensations
    state.fuel_pressure_kpa = float(clamp(300.0 + state.load * 5.0, 0.0, 2000.0))
    state.oil_pressure_kpa = float(clamp(120.0 + state.rpm * 0.08, 0.0, 2000.0))
    state.egt_c = float(clamp(380.0 + state.load * 5.0 + max(0.0, state.map_kpa - 100.0) * 1.2, 0.0, 1200.0))
    state.iat_comp_pct = float(clamp(-(state.iat - 25.0) * 0.6, -50.0, 50.0))
    state.egt_alarm = 1 if state.egt_c > 980.0 else 0
    state.oil_press_alarm = 1 if (state.rpm > 2000 and state.oil_pressure_kpa < 140.0) else 0

    # Knock / DSG
    state.knock_retard_deg = float(clamp(state.knock_retard_deg, 0.0, 20.0))
    state.knock_count = int(clamp(state.knock_count, 0, 65535))
    if state.engine_on:
        if state.speed < 5:
            state.dsg_gear = 1
        elif state.speed < 30:
            state.dsg_gear = 2
        elif state.speed < 55:
            state.dsg_gear = 3
        elif state.speed < 85:
            state.dsg_gear = 4
        elif state.speed < 120:
            state.dsg_gear = 5
        else:
            state.dsg_gear = 6
    else:
        state.dsg_gear = 0
    state.dsg_mode = 4 if state.throttle > 75 else 3
    state.dsg_shift_request = 1 if state.rpm > 6400 else 2 if state.rpm < 1300 and state.speed > 20 else 0
    state.launch_active = 1 if (state.speed < 5 and state.throttle > 85 and not state.brake_switch) else 0
    state.dsg_clutch_slip_rpm = int(clamp(abs(state.rpm - max(800, state.speed * 45)), 0, 5000))
    state.dsg_trans_temp_c = float(clamp(state.dsg_trans_temp_c + (state.load / 100.0) * 0.15, -40.0, 215.0))
    state.tcu_ready = 1


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
    state.ethanol_pct = 0.0
    state.knock_retard_deg = 0.0
    state.knock_count = 0
    state.fuel_trim_st = 0.0
    state.fuel_trim_lt = 0.0
    state.dsg_trans_temp_c = 60.0


async def run_scenario(name: str) -> None:
    """
    Scenarios:
      - idle / cruise / wot: static presets
      - ramp: one-shot sweep (8s)
      - needle_sweep: continuous 30s loop sweeping ALL UI parameters (dashboard debug)
      - dash_test: advanced 30s loop (holds, blink, chase, async ramps)
      - warning_blink: continuous all-warning blinker (cluster validation)
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
        state.ethanol_pct = 0.0
        state.knock_retard_deg = 0.0
        _sync_extended_signals()
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
        state.ethanol_pct = 20.0
        state.knock_retard_deg = 0.5
        _sync_extended_signals()
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
        state.ethanol_pct = 65.0
        state.knock_retard_deg = 2.0
        _sync_extended_signals()
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
            state.ethanol_pct = float(clamp(5.0 + t * 60.0, 0.0, 100.0))
            state.knock_retard_deg = float(clamp(max(0.0, (state.map_kpa - 180.0) * 0.03), 0.0, 20.0))
            _sync_extended_signals()
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
                state.ethanol_pct = float(clamp(10.0 + s3 * 70.0, 0.0, 100.0))
                state.knock_retard_deg = float(clamp((state.load / 100.0) * 6.0, 0.0, 20.0))
                _sync_extended_signals()

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
                    state.ethanol_pct = 0.0
                    state.knock_retard_deg = 0.0
                    _sync_extended_signals()

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
                    state.ethanol_pct = float(clamp(10.0 + x * 55.0, 0.0, 100.0))
                    state.knock_retard_deg = float(clamp((state.map_kpa - 120.0) * 0.035, 0.0, 20.0))
                    _sync_extended_signals()

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
                    state.ethanol_pct = 65.0
                    state.knock_retard_deg = 4.0
                    _sync_extended_signals()

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
                    state.ethanol_pct = float(clamp(65.0 - x * 45.0, 0.0, 100.0))
                    state.knock_retard_deg = float(clamp((state.map_kpa - 120.0) * 0.03, 0.0, 20.0))
                    _sync_extended_signals()

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
                    state.ethanol_pct = float(clamp(20.0 + p2 * 50.0, 0.0, 100.0))
                    state.knock_retard_deg = float(clamp((state.load / 100.0) * 8.0, 0.0, 20.0))
                    _sync_extended_signals()

                    await asyncio.sleep(dt)
                    continue

            # loop repeats every 30s
        return

    if name == "warning_blink":
        # Continuous all-warning blinker for cluster/icon validation.
        # Keeps powertrain near idle while toggling all warning-relevant signals.
        state.engine_on = True
        state.rpm = 950
        state.throttle = 3
        state.load = 12
        state.speed = 0
        state.coolant = 96
        state.iat = 24
        state.oil_temp = 92
        state.battery_voltage = 13.6
        state.lambda_value = 1.000
        state.map_kpa = 100.0
        state.boost_pressure = 100.0
        state.driver_torque_req = 0.0
        state.indicated_torque = 0.0

        tick = 0
        while not scenario_cancel.is_set():
            on = (tick % 2) == 0

            # Street top cluster warnings
            state.mil = on
            state.epc = on
            state.dtc_count = 12 if on else 0
            state.cruise_active = True  # arrows alternate in UI while cruise is active
            state.brake_switch = on
            state.clutch_switch = on
            state.fan_request = on

            # Extended warning-style flags
            state.egt_alarm = 1 if on else 0
            state.oil_press_alarm = 1 if on else 0
            state.launch_active = 1 if on else 0

            _sync_extended_signals()
            await asyncio.sleep(0.35)
            tick += 1
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
    global TX_COUNT, TX_LAST_TS
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
                TX_COUNT += 1
                TX_LAST_TS = time.time()

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
          <button class="btn small" id="scWarn">Warn blink</button>
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

          <div style="margin-top:14px;margin-bottom:8px;font-size:12px;color:var(--muted);font-weight:800;letter-spacing:.2px;">
            Extended DBC
          </div>

          <div class="row">
            <div class="label"><strong>AFR</strong><span>Air fuel ratio</span></div>
            <input id="afr" type="range" min="0" max="25.5" step="0.1" value="14.7">
            <div class="value"><span id="afrVal">14.7</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Fuel Pressure</strong><span>Rail pressure</span></div>
            <input id="fuel_pressure_kpa" type="range" min="0" max="2000" step="1" value="400">
            <div class="value"><span id="fuelPressureVal">400</span> <small>kPa</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Oil Pressure</strong><span>Lub pressure</span></div>
            <input id="oil_pressure_kpa" type="range" min="0" max="2000" step="1" value="250">
            <div class="value"><span id="oilPressureVal">250</span> <small>kPa</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>EGT</strong><span>Exhaust gas temp</span></div>
            <input id="egt_c" type="range" min="0" max="1200" step="1" value="650">
            <div class="value"><span id="egtVal">650</span> <small>°C</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Boost Target</strong><span>Target pressure</span></div>
            <input id="boost_target_kpa" type="range" min="0" max="300" step="0.1" value="180">
            <div class="value"><span id="boostTargetVal">180.0</span> <small>kPa</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Boost Error</strong><span>Target - MAP</span></div>
            <input id="boost_error_kpa" type="range" min="-300" max="300" step="0.1" value="0">
            <div class="value"><span id="boostErrorVal">0.0</span> <small>kPa</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>WGDC</strong><span>Wastegate duty</span></div>
            <input id="wgdc_pct" type="range" min="0" max="100" step="0.5" value="35">
            <div class="value"><span id="wgdcVal">35.0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>N75</strong><span>Valve duty</span></div>
            <input id="n75_pct" type="range" min="0" max="100" step="0.5" value="35">
            <div class="value"><span id="n75Val">35.0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Turbo speed</strong><span>Compressor speed</span></div>
            <input id="turbo_speed_krpm" type="range" min="0" max="400" step="0.1" value="80">
            <div class="value"><span id="turboSpeedVal">80.0</span> <small>krpm</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Ignition angle</strong><span>Timing advance</span></div>
            <input id="ign_angle_deg" type="range" min="-30" max="60" step="0.1" value="10">
            <div class="value"><span id="ignAngleVal">10.0</span> <small>deg</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Dwell</strong><span>Coil charge time</span></div>
            <input id="dwell_ms" type="range" min="0" max="12.75" step="0.05" value="2.5">
            <div class="value"><span id="dwellVal">2.50</span> <small>ms</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Injector PW</strong><span>Pulse width</span></div>
            <input id="inj_pw_ms" type="range" min="0" max="12.75" step="0.05" value="3.0">
            <div class="value"><span id="injPwVal">3.00</span> <small>ms</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Fuel trim ST</strong><span>Short term</span></div>
            <input id="fuel_trim_st" type="range" min="-100" max="100" step="0.5" value="0">
            <div class="value"><span id="fuelTrimStVal">0.0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Fuel trim LT</strong><span>Long term</span></div>
            <input id="fuel_trim_lt" type="range" min="-100" max="100" step="0.5" value="0">
            <div class="value"><span id="fuelTrimLtVal">0.0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Lambda target</strong><span>Requested lambda</span></div>
            <input id="lambda_target" type="range" min="0" max="2.55" step="0.01" value="1.00">
            <div class="value"><span id="lambdaTargetVal">1.00</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Fuel temp</strong><span>Fuel temperature</span></div>
            <input id="fuel_temp_c" type="range" min="-40" max="215" step="1" value="25">
            <div class="value"><span id="fuelTempVal">25</span> <small>°C</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Ethanol</strong><span>Fuel blend content</span></div>
            <input id="ethanol_pct" type="range" min="0" max="100" step="0.5" value="0">
            <div class="value"><span id="ethanolVal">0.0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Stoich AFR</strong><span>Blend stoich value</span></div>
            <input id="stoich_afr" type="range" min="8" max="20" step="0.1" value="14.7">
            <div class="value"><span id="stoichAfrVal">14.7</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Flex mode</strong><span>0=Petrol,1=Blend,2=E85</span></div>
            <input id="flex_mode" type="range" min="0" max="3" step="1" value="0">
            <div class="value"><span id="flexModeVal">0</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Fuel density</strong><span>Relative density</span></div>
            <input id="fuel_density" type="range" min="0" max="25.5" step="0.1" value="7.4">
            <div class="value"><span id="fuelDensityVal">7.4</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Knock retard</strong><span>Timing correction</span></div>
            <input id="knock_retard_deg" type="range" min="0" max="20" step="0.1" value="0">
            <div class="value"><span id="knockRetardVal">0.0</span> <small>deg</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>Knock count</strong><span>Accumulated events</span></div>
            <input id="knock_count" type="range" min="0" max="65535" step="1" value="0">
            <div class="value"><span id="knockCountVal">0</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>IAT compensation</strong><span>Temp correction</span></div>
            <input id="iat_comp_pct" type="range" min="-50" max="50" step="0.5" value="0">
            <div class="value"><span id="iatCompVal">0.0</span> <small>%</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>DSG gear</strong><span>Transmission gear</span></div>
            <input id="dsg_gear" type="range" min="0" max="10" step="1" value="1">
            <div class="value"><span id="dsgGearVal">1</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>DSG slip</strong><span>Clutch slip</span></div>
            <input id="dsg_clutch_slip_rpm" type="range" min="0" max="5000" step="1" value="0">
            <div class="value"><span id="dsgSlipVal">0</span> <small>rpm</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>DSG trans temp</strong><span>Oil temperature</span></div>
            <input id="dsg_trans_temp_c" type="range" min="-40" max="215" step="1" value="60">
            <div class="value"><span id="dsgTempVal">60</span> <small>°C</small></div>
          </div>

          <div class="row">
            <div class="label"><strong>DSG mode</strong><span>0..7</span></div>
            <input id="dsg_mode" type="range" min="0" max="7" step="1" value="1">
            <div class="value"><span id="dsgModeVal">1</span></div>
          </div>

          <div class="row">
            <div class="label"><strong>Shift request</strong><span>0 none, 1 up, 2 down</span></div>
            <input id="dsg_shift_request" type="range" min="0" max="3" step="1" value="0">
            <div class="value"><span id="dsgShiftReqVal">0</span></div>
          </div>

          <div class="toggles" style="margin-top:12px;">
            <div class="toggle">
              <div class="tlabel"><strong>EGT alarm</strong><span>Thermal protection</span></div>
              <label class="switch"><input id="egt_alarm" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>Oil pressure alarm</strong><span>Lubrication warning</span></div>
              <label class="switch"><input id="oil_press_alarm" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>Launch active</strong><span>Launch control state</span></div>
              <label class="switch"><input id="launch_active" type="checkbox"><span class="slider"></span></label>
            </div>
            <div class="toggle">
              <div class="tlabel"><strong>TCU ready</strong><span>Transmission available</span></div>
              <label class="switch"><input id="tcu_ready" type="checkbox"><span class="slider"></span></label>
            </div>
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
  document.getElementById("scWarn").addEventListener("click", () => startScenario("warning_blink"));
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
  const afr = bindRange("afr", "afrVal", "afr", true, 1);
  const fuel_pressure_kpa = bindRange("fuel_pressure_kpa", "fuelPressureVal", "fuel_pressure_kpa", true, 0);
  const oil_pressure_kpa = bindRange("oil_pressure_kpa", "oilPressureVal", "oil_pressure_kpa", true, 0);
  const egt_c = bindRange("egt_c", "egtVal", "egt_c", true, 0);
  const boost_target_kpa = bindRange("boost_target_kpa", "boostTargetVal", "boost_target_kpa", true, 1);
  const boost_error_kpa = bindRange("boost_error_kpa", "boostErrorVal", "boost_error_kpa", true, 1);
  const wgdc_pct = bindRange("wgdc_pct", "wgdcVal", "wgdc_pct", true, 1);
  const n75_pct = bindRange("n75_pct", "n75Val", "n75_pct", true, 1);
  const turbo_speed_krpm = bindRange("turbo_speed_krpm", "turboSpeedVal", "turbo_speed_krpm", true, 1);
  const ign_angle_deg = bindRange("ign_angle_deg", "ignAngleVal", "ign_angle_deg", true, 1);
  const dwell_ms = bindRange("dwell_ms", "dwellVal", "dwell_ms", true, 2);
  const inj_pw_ms = bindRange("inj_pw_ms", "injPwVal", "inj_pw_ms", true, 2);
  const fuel_trim_st = bindRange("fuel_trim_st", "fuelTrimStVal", "fuel_trim_st", true, 1);
  const fuel_trim_lt = bindRange("fuel_trim_lt", "fuelTrimLtVal", "fuel_trim_lt", true, 1);
  const lambda_target = bindRange("lambda_target", "lambdaTargetVal", "lambda_target", true, 2);
  const fuel_temp_c = bindRange("fuel_temp_c", "fuelTempVal", "fuel_temp_c", true, 0);
  const ethanol_pct = bindRange("ethanol_pct", "ethanolVal", "ethanol_pct", true, 1);
  const stoich_afr = bindRange("stoich_afr", "stoichAfrVal", "stoich_afr", true, 1);
  const flex_mode = bindRange("flex_mode", "flexModeVal", "flex_mode", false, 0);
  const fuel_density = bindRange("fuel_density", "fuelDensityVal", "fuel_density", true, 1);
  const knock_retard_deg = bindRange("knock_retard_deg", "knockRetardVal", "knock_retard_deg", true, 1);
  const knock_count = bindRange("knock_count", "knockCountVal", "knock_count", false, 0);
  const iat_comp_pct = bindRange("iat_comp_pct", "iatCompVal", "iat_comp_pct", true, 1);
  const dsg_gear = bindRange("dsg_gear", "dsgGearVal", "dsg_gear", false, 0);
  const dsg_clutch_slip_rpm = bindRange("dsg_clutch_slip_rpm", "dsgSlipVal", "dsg_clutch_slip_rpm", false, 0);
  const dsg_trans_temp_c = bindRange("dsg_trans_temp_c", "dsgTempVal", "dsg_trans_temp_c", true, 0);
  const dsg_mode = bindRange("dsg_mode", "dsgModeVal", "dsg_mode", false, 0);
  const dsg_shift_request = bindRange("dsg_shift_request", "dsgShiftReqVal", "dsg_shift_request", false, 0);

  const brake_switch = bindSwitch("brake_switch", "brake_switch");
  const clutch_switch = bindSwitch("clutch_switch", "clutch_switch");
  const mil = bindSwitch("mil", "mil");
  const epc = bindSwitch("epc", "epc");
  const cruise_active = bindSwitch("cruise_active", "cruise_active");
  const fan_request = bindSwitch("fan_request", "fan_request");
  const egt_alarm = bindSwitch("egt_alarm", "egt_alarm");
  const oil_press_alarm = bindSwitch("oil_press_alarm", "oil_press_alarm");
  const launch_active = bindSwitch("launch_active", "launch_active");
  const tcu_ready = bindSwitch("tcu_ready", "tcu_ready");

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
    afr.value = s.afr; document.getElementById("afrVal").textContent = Number(s.afr).toFixed(1);
    fuel_pressure_kpa.value = s.fuel_pressure_kpa; document.getElementById("fuelPressureVal").textContent = Number(s.fuel_pressure_kpa).toFixed(0);
    oil_pressure_kpa.value = s.oil_pressure_kpa; document.getElementById("oilPressureVal").textContent = Number(s.oil_pressure_kpa).toFixed(0);
    egt_c.value = s.egt_c; document.getElementById("egtVal").textContent = Number(s.egt_c).toFixed(0);
    boost_target_kpa.value = s.boost_target_kpa; document.getElementById("boostTargetVal").textContent = Number(s.boost_target_kpa).toFixed(1);
    boost_error_kpa.value = s.boost_error_kpa; document.getElementById("boostErrorVal").textContent = Number(s.boost_error_kpa).toFixed(1);
    wgdc_pct.value = s.wgdc_pct; document.getElementById("wgdcVal").textContent = Number(s.wgdc_pct).toFixed(1);
    n75_pct.value = s.n75_pct; document.getElementById("n75Val").textContent = Number(s.n75_pct).toFixed(1);
    turbo_speed_krpm.value = s.turbo_speed_krpm; document.getElementById("turboSpeedVal").textContent = Number(s.turbo_speed_krpm).toFixed(1);
    ign_angle_deg.value = s.ign_angle_deg; document.getElementById("ignAngleVal").textContent = Number(s.ign_angle_deg).toFixed(1);
    dwell_ms.value = s.dwell_ms; document.getElementById("dwellVal").textContent = Number(s.dwell_ms).toFixed(2);
    inj_pw_ms.value = s.inj_pw_ms; document.getElementById("injPwVal").textContent = Number(s.inj_pw_ms).toFixed(2);
    fuel_trim_st.value = s.fuel_trim_st; document.getElementById("fuelTrimStVal").textContent = Number(s.fuel_trim_st).toFixed(1);
    fuel_trim_lt.value = s.fuel_trim_lt; document.getElementById("fuelTrimLtVal").textContent = Number(s.fuel_trim_lt).toFixed(1);
    lambda_target.value = s.lambda_target; document.getElementById("lambdaTargetVal").textContent = Number(s.lambda_target).toFixed(2);
    fuel_temp_c.value = s.fuel_temp_c; document.getElementById("fuelTempVal").textContent = Number(s.fuel_temp_c).toFixed(0);
    ethanol_pct.value = s.ethanol_pct; document.getElementById("ethanolVal").textContent = Number(s.ethanol_pct).toFixed(1);
    stoich_afr.value = s.stoich_afr; document.getElementById("stoichAfrVal").textContent = Number(s.stoich_afr).toFixed(1);
    flex_mode.value = s.flex_mode; document.getElementById("flexModeVal").textContent = Number(s.flex_mode).toFixed(0);
    fuel_density.value = s.fuel_density; document.getElementById("fuelDensityVal").textContent = Number(s.fuel_density).toFixed(1);
    knock_retard_deg.value = s.knock_retard_deg; document.getElementById("knockRetardVal").textContent = Number(s.knock_retard_deg).toFixed(1);
    knock_count.value = s.knock_count; document.getElementById("knockCountVal").textContent = Number(s.knock_count).toFixed(0);
    iat_comp_pct.value = s.iat_comp_pct; document.getElementById("iatCompVal").textContent = Number(s.iat_comp_pct).toFixed(1);
    dsg_gear.value = s.dsg_gear; document.getElementById("dsgGearVal").textContent = Number(s.dsg_gear).toFixed(0);
    dsg_clutch_slip_rpm.value = s.dsg_clutch_slip_rpm; document.getElementById("dsgSlipVal").textContent = Number(s.dsg_clutch_slip_rpm).toFixed(0);
    dsg_trans_temp_c.value = s.dsg_trans_temp_c; document.getElementById("dsgTempVal").textContent = Number(s.dsg_trans_temp_c).toFixed(0);
    dsg_mode.value = s.dsg_mode; document.getElementById("dsgModeVal").textContent = Number(s.dsg_mode).toFixed(0);
    dsg_shift_request.value = s.dsg_shift_request; document.getElementById("dsgShiftReqVal").textContent = Number(s.dsg_shift_request).toFixed(0);

    dtc_count.value = s.dtc_count; document.getElementById("dtcVal").textContent = s.dtc_count;

    brake_switch.checked = !!s.brake_switch;
    clutch_switch.checked = !!s.clutch_switch;
    mil.checked = !!s.mil;
    epc.checked = !!s.epc;
    cruise_active.checked = !!s.cruise_active;
    fan_request.checked = !!s.fan_request;
    egt_alarm.checked = !!s.egt_alarm;
    oil_press_alarm.checked = !!s.oil_press_alarm;
    launch_active.checked = !!s.launch_active;
    tcu_ready.checked = !!s.tcu_ready;
  };
</script>
</body>
</html>
"""

# Inject DBC path without brace templating issues
INDEX_HTML = INDEX_HTML.replace("__DBC_PATH__", DBC_PATH)


# -------------------------
# Health
# -------------------------
@app.get("/health")
def health():
    age = None if TX_LAST_TS == 0 else round(time.time() - TX_LAST_TS, 3)
    return {
        "ok": True,
        "tx_count": TX_COUNT,
        "tx_last_age_s": age,
        "bus": {"interface": BUS_INTERFACE, "channel": BUS_CHANNEL},
        "dbc": DBC_PATH,
        "messages": [m.get("dbc_message") for m in MESSAGES],
    }


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

    allowed = {"idle", "cruise", "wot", "ramp", "needle_sweep", "dash_test", "warning_blink"}
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
    log = logging.getLogger("can_tx")

    async def _run_can():
        try:
            await can_scheduler()
        except Exception:
            log.exception("CAN scheduler crashed — exiting (fail-fast)")
            os._exit(1)

    asyncio.create_task(_run_can())
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
