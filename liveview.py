from __future__ import annotations

import os
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dbc_codec import DbcCodec
from model import CanonicalState

from can_reader import CanReader, RemoteStateReader
from debug_utils import DEBUG, METRICS, log_debug, log_error, log_info

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEBUI_DIR = os.path.join(BASE_DIR, "webui")
STATIC_DIR = os.path.join(BASE_DIR, "static")

CAN_CH = os.getenv("CAN_CH", "can0")
DBC_PATH = os.getenv("DBC_PATH", "dbc/golf4_min.dbc")
PUSH_HZ = float(os.getenv("PUSH_HZ", "15"))
STALE_S = float(os.getenv("STALE_S", "1.0"))
SOURCE_MODE = os.getenv("SOURCE_MODE", "auto").strip().lower()
REMOTE_STATE_URL = os.getenv("REMOTE_STATE_URL", "http://192.168.0.60:8001/api/state")
REMOTE_POLL_HZ = float(os.getenv("REMOTE_POLL_HZ", "20"))

SPEED_FACTOR = float(os.getenv("SPEED_FACTOR", "1.0"))
MAP_FACTOR = float(os.getenv("MAP_FACTOR", "1.0"))
ATM_KPA = float(os.getenv("ATM_KPA", "101.3"))

app = FastAPI(title="Golf4 CAN Liveview (Base clean — Dev/Debug invariant)")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

codec = DbcCodec(DBC_PATH)
state = CanonicalState(stale_s=STALE_S)

def _can_iface_exists(can_ch: str) -> bool:
    return os.path.exists(f"/sys/class/net/{can_ch}")


def _source_label_from_url(url: str) -> str:
    p = urlsplit(url)
    host = p.hostname or url
    port = f":{p.port}" if p.port else ""
    return f"remote:{host}{port}"


def _build_reader():
    mode = SOURCE_MODE
    if mode not in ("auto", "can", "remote"):
        mode = "auto"

    if mode == "remote":
        src = _source_label_from_url(REMOTE_STATE_URL)
        return (
            RemoteStateReader(
                remote_state_url=REMOTE_STATE_URL,
                poll_hz=REMOTE_POLL_HZ,
                codec=codec,
                state=state,
                speed_factor=SPEED_FACTOR,
                map_factor=MAP_FACTOR,
                atm_kpa=ATM_KPA,
            ),
            src,
            "remote",
        )

    if mode == "can" or _can_iface_exists(CAN_CH):
        return (
            CanReader(
                can_ch=CAN_CH,
                codec=codec,
                state=state,
                speed_factor=SPEED_FACTOR,
                map_factor=MAP_FACTOR,
                atm_kpa=ATM_KPA,
            ),
            CAN_CH,
            "can",
        )

    src = _source_label_from_url(REMOTE_STATE_URL)
    return (
        RemoteStateReader(
            remote_state_url=REMOTE_STATE_URL,
            poll_hz=REMOTE_POLL_HZ,
            codec=codec,
            state=state,
            speed_factor=SPEED_FACTOR,
            map_factor=MAP_FACTOR,
            atm_kpa=ATM_KPA,
        ),
        src,
        "remote",
    )


reader, SOURCE_LABEL, SOURCE_BACKEND = _build_reader()
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
            "source_mode": SOURCE_MODE,
            "source_backend": SOURCE_BACKEND,
            "source": SOURCE_LABEL,
            "remote_state_url": REMOTE_STATE_URL,
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
    snap = state.payload(SOURCE_LABEL, dbc_name, PUSH_HZ, conv)
    snap = _augment_payload(snap)
    if DEBUG:
        snap["debug_metrics"] = METRICS.snapshot()
    return JSONResponse(snap)


@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    import asyncio

    period = 1.0 / max(1.0, PUSH_HZ)
    dbc_name = os.path.basename(DBC_PATH)
    conv = {"SPEED_FACTOR": SPEED_FACTOR, "MAP_FACTOR": MAP_FACTOR, "ATM_KPA": ATM_KPA}
    METRICS.on_ws_connected()
    log_info("liveview", "ws_connected", ws_clients=METRICS.ws_clients_connected)

    try:
        while True:
            payload = state.payload(SOURCE_LABEL, dbc_name, PUSH_HZ, conv)
            payload = _augment_payload(payload)
            try:
                await ws.send_text(json.dumps(payload, separators=(",", ":")))
                METRICS.on_ws_sent()
            except Exception as exc:
                METRICS.on_ws_dropped()
                log_error("liveview", "ws_send_error", err=str(exc), dropped=1)
                break
            await asyncio.sleep(period)
    except WebSocketDisconnect:
        log_debug("liveview", "ws_disconnected")
    except Exception as exc:
        log_error("liveview", "ws_error", err=str(exc))
    finally:
        METRICS.on_ws_disconnected()
        log_info("liveview", "ws_closed", ws_clients=METRICS.ws_clients_connected)
