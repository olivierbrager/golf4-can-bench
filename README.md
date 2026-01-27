# Golf4 CAN Dashboard — Base clean (Dev/Debug invariant)

Goal: Dev + Debug dashboards are stable and **invariant** to changes in Street/Race.
- Backend produces a **single canonical payload**.
- UI views are **read-only renderers**.

## Run (manual)
```bash
cd ~/ecu_emulator
source .venv/bin/activate
CAN_CH=can0 DBC_PATH=dbc/golf4_min.dbc PUSH_HZ=15 STALE_S=1.0 \
  python -m uvicorn liveview:app --host 0.0.0.0 --port 8010
```

## Environment variables (conversions)
These are applied **server-side** only:
- SPEED_FACTOR (km/h multiplier after DBC scaling)
- MAP_FACTOR (kPa multiplier after DBC scaling)
- ATM_KPA (boost = (MAP-ATM_KPA)/100)

## Files
- liveview.py: FastAPI server + WebSocket
- can_reader.py: SocketCAN reader thread
- dbc_codec.py: DBC wrapper
- model.py: canonical state + payload builder
- webui/index.html + static/*: front (tabs + Dev + Debug)

## Systemd
Examples are in `systemd/`.
