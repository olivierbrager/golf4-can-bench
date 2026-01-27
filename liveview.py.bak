from __future__ import annotations

import os
import json
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

app = FastAPI(title="Golf4 CAN Liveview (Base clean)")
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

@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(WEBUI_DIR, "index.html"))

@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "can": CAN_CH,
        "dbc": DBC_PATH,
        "push_hz": PUSH_HZ,
        "stale_s": STALE_S,
        "conversions": {"SPEED_FACTOR": SPEED_FACTOR, "MAP_FACTOR": MAP_FACTOR, "ATM_KPA": ATM_KPA},
    })

@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    import asyncio
    period = 1.0 / max(1.0, PUSH_HZ)
    dbc_name = os.path.basename(DBC_PATH)
    conv = {"SPEED_FACTOR": SPEED_FACTOR, "MAP_FACTOR": MAP_FACTOR, "ATM_KPA": ATM_KPA}

    while True:
        await ws.send_text(json.dumps(state.payload(CAN_CH, dbc_name, PUSH_HZ, conv), separators=(",", ":")))
        await asyncio.sleep(period)
