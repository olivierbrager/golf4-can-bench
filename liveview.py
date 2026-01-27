from __future__ import annotations

import os
import json
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dbc_codec import DbcCodec
from model import CanonicalState

from can_reader import CanReader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEBUI_DIR = os.path.join(BASE_DIR, "webui")
STATIC_DIR = os.path.join(BASE_DIR, "static")

CAN_CH = os.getenv("CAN_CH", "can0")
DBC_PATH = os.getenv("DBC_PATH", "dbc/golf4_min.dbc")
PUSH_HZ = float(os.getenv("PUSH_HZ", "15"))
STALE_S = float(os.getenv("STALE_S", "1.0"))

SPEED_FACTOR = float(os.getenv("SPEED_FACTOR", "1.0"))
MAP_FACTOR = float(os.getenv("MAP_FACTOR", "1.0"))
ATM_KPA = float(os.getenv("ATM_KPA", "101.3"))

app = FastAPI(title="Golf4 CAN Liveview (Base clean — Dev/Debug invariant)")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

codec = DbcCodec(DBC_PATH)
state = CanonicalState(stale_s=STALE_S)

reader = CanReader(
    can_ch=CAN_CH,
    codec=codec,
    state=state,
    speed_factor=SPEED_FACTOR,
    map_factor=MAP_FACTOR,
    atm_kpa=ATM_KPA,
)
reader.start()


def _last_age_s() -> Optional[float]:
    # model.py may expose rx_last_age_s property; fallback if not.
    if hasattr(state, "rx_last_age_s"):
        return getattr(state, "rx_last_age_s")
    ts = getattr(state, "last_rx_ts", 0.0) or 0.0
    return None if not ts else max(0.0, time.time() - ts)


def _augment_payload(p: Dict[str, Any]) -> Dict[str, Any]:
    """Add stable aliases for dashboards.

    - Keeps original canonical keys (e.g. RPM, Speed, Boost, CoolantTemp, OilTemp, BatteryV, Lambda)
    - Adds lowercase/legacy aliases expected by some dashboards: rpm, speed, boost, coolant, oil, battery, lambda
    """
    sigs = p.get("signals", {}) or {}

    def pick(src_key: str) -> Optional[Dict[str, Any]]:
        v = sigs.get(src_key)
        return v if isinstance(v, dict) else None

    aliases = {
        "rpm": pick("RPM"),
        "speed": pick("Speed"),
        "boost": pick("Boost"),
        "map_kpa": pick("MAP"),
        "throttle": pick("Throttle"),
        "load": pick("Load"),
        "coolant": pick("CoolantTemp"),
        "oil": pick("OilTemp"),
        "battery": pick("BatteryV"),
        "lambda": pick("Lambda"),
    }

    # Only inject aliases that exist
    for k, v in list(aliases.items()):
        if v is None:
            aliases.pop(k, None)

    if aliases:
        p.setdefault("compat", {})
        p["compat"]["signals"] = aliases

    # Also mirror rx stats at top-level for ultra-simple clients
    meta = p.get("meta", {}) or {}
    rx_total = meta.get("rx_total")
    rx_decoded = meta.get("rx_decoded")
    p.setdefault("rx", {})
    if rx_total is not None:
        p["rx"]["total"] = rx_total
    if rx_decoded is not None:
        p["rx"]["decoded"] = rx_decoded
    if "last_rx_ts" in meta:
        p["rx"]["last_age_s"] = _last_age_s()

    return p


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(WEBUI_DIR, "index.html"))


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "can": CAN_CH,
            "dbc": os.path.abspath(DBC_PATH),
            "push_hz": PUSH_HZ,
            "stale_s": STALE_S,
            "conversions": {
                "SPEED_FACTOR": SPEED_FACTOR,
                "MAP_FACTOR": MAP_FACTOR,
                "ATM_KPA": ATM_KPA,
            },
            # DEV/DEBUG invariants
            "rx": {
                "total": getattr(state, "rx_total", None),
                "decoded": getattr(state, "rx_decoded", None),
                "last_age_s": _last_age_s(),
                "last_frame": getattr(state, "last_frame", None),
            },
        }
    )


@app.get("/metrics")
def metrics() -> JSONResponse:
    # Full snapshot for debugging: same shape as WS payload (plus compat signals)
    dbc_name = os.path.basename(DBC_PATH)
    conv = {"SPEED_FACTOR": SPEED_FACTOR, "MAP_FACTOR": MAP_FACTOR, "ATM_KPA": ATM_KPA}
    snap = state.payload(CAN_CH, dbc_name, PUSH_HZ, conv)
    snap = _augment_payload(snap)
    return JSONResponse(snap)


@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    import asyncio

    period = 1.0 / max(1.0, PUSH_HZ)
    dbc_name = os.path.basename(DBC_PATH)
    conv = {"SPEED_FACTOR": SPEED_FACTOR, "MAP_FACTOR": MAP_FACTOR, "ATM_KPA": ATM_KPA}

    while True:
        payload = state.payload(CAN_CH, dbc_name, PUSH_HZ, conv)
        payload = _augment_payload(payload)
        await ws.send_text(json.dumps(payload, separators=(",", ":")))
        await asyncio.sleep(period)
