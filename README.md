# Golf 4 CAN Bench — ECU Emulator (TX) + Liveview (RX)

This repo contains:
- **can_tx_emulator.py**: ECU emulator that **transmits** CAN frames on SocketCAN (TX), encoded via `dbc/golf4_min.dbc`.
- **liveview.py**: dashboard / viewer that **reads** CAN (RX), decodes via the DBC, and exposes a web UI.
- **frames.yaml**: emitted frame configuration (periods + signal-to-state mapping).
- **dbc/golf4_min.dbc**: minimal DBC (messages: `EngineFast` and `EngineStatus`).

## Prerequisites
- Linux with SocketCAN (can-utils recommended)
- Python 3.x + venv
- Access to a CAN interface `can0` (e.g. gs_usb)

Python packages:
- `python-can`, `cantools`, `fastapi`, `uvicorn[standard]`, `pyyaml`

## Quick setup

### 1) CAN interface
Configure `can0` at 500k (adjust if needed):
```bash
sudo ip link set can0 down || true
sudo ip link set can0 up type can bitrate 500000
ip -details link show can0
```

Test local RX/TX:
```bash
cansend can0 123#1122334455667788
candump -L can0 -n 3
```

### 2) Python venv
```bash
cd /home/olivier/ecu_emulator
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install python-can cantools fastapi "uvicorn[standard]" pyyaml
```

Manual run (without systemd)
TX (emulator)
```bash
cd /home/olivier/ecu_emulator
source .venv/bin/activate
uvicorn can_tx_emulator:app --host 0.0.0.0 --port 8000
```

Verify that it is transmitting:
```bash
candump -L can0 -n 20
```

## RX (liveview)

Example (adjust variables if your liveview uses them):
```bash
cd /home/olivier/ecu_emulator
source .venv/bin/activate

CAN_CH=can0 DBC_PATH=dbc/golf4_min.dbc PUSH_HZ=15 STALE_S=1.0 \
SPEED_FACTOR=1.0 MAP_FACTOR=1.0 ATM_KPA=101.3 \
python -m uvicorn liveview:app --host 0.0.0.0 --port 8010
```

## UI

### TX UI (if enabled in can_tx_emulator): http://<ip>:8000

#### Snapshot (ECU Emulator)
![ECU Emulator](docs/snapshots/ecu-emulator.png)

### Liveview: http://<ip>:8010

#### Interface (Liveview)
- Top bar: WebSocket status (`connected/connecting`), visual latency via the dot (green/red).
- CAN/DBC info bar: CAN source, loaded DBC, RX counters, and `stale` indicator.
- Tabs: `Debug` (full payload), `Dev` (KPIs + derived), `Street`/`Race` (placeholders).
- Debug:
  - Full-text filter (e.g. `RPM`, `MAP`, `0x280`) on the payload.
  - Flags (MIL/EPC/Fan/...).
  - Signals table + ages (`age`) in seconds.
- Dev:
  - Derived KPIs (5s rolling window).
  - Synthetic flags.

#### Snapshots (UI)
Debug:
![Liveview Debug](docs/snapshots/liveview-debug.png)

Dev:
![Liveview Dev](docs/snapshots/liveview-dev.png)

#### Snapshots (snippets)
Status line (CAN/DBC info bar):
```
CAN:can0 | DBC:golf4_min.dbc | rx:115482/47736 | stale:yes
```

Snapshot payload (excerpt from /metrics):
```json
{
  "meta": {
    "src": "can0",
    "dbc": "golf4_min.dbc",
    "rx_total": 115482,
    "rx_decoded": 47736,
    "last_rx_age_s": 125.6694688796997,
    "stale": true,
    "push_hz": 15.0
  },
  "flags": {
    "MIL": 0,
    "EPC": 0,
    "Fan": 0,
    "Cruise": 0,
    "Brake": 0,
    "Clutch": 0
  },
  "dev": {
    "BoostMax5": 0.9390000000000002,
    "LambdaMin5": 0.001,
    "LambdaMax5": 0.058
  },
  "compat.signals": {
    "rpm": {"v": 1329.75, "unit": "rpm", "age": 125.67373180389404},
    "speed": {"v": 37.0, "unit": "kmh", "age": 125.6737380027771},
    "throttle": {"v": 49.0, "unit": "%", "age": 125.67374205589294},
    "map_kpa": {"v": 165.8, "unit": "kPa", "age": 125.67374968528748},
    "boost": {"v": 0.6450000000000001, "unit": "bar", "age": 125.67375326156616}
  }
}
```

Run with systemd (recommended)
TX service

Service: can-tx.service

ExecStart must be: uvicorn can_tx_emulator:app ...

WorkingDirectory: /home/olivier/ecu_emulator

Commands:
```bash
sudo systemctl enable --now can-tx.service
systemctl status can-tx.service --no-pager
journalctl -u can-tx.service -f
```

RX service (if present)
```bash
sudo systemctl enable --now liveview.service
systemctl status liveview.service --no-pager
journalctl -u liveview.service -f
```

Configuration
DBC

Two DBCs are available:
- `dbc/golf4_min.dbc` (minimal, 2 messages)
- `dbc/golf4_ext.dbc` (extended, 8 messages)

`dbc/golf4_min.dbc` contains:
- EngineFast (0x280): RPM, Throttle, Load, Speed, MAP_kPa
- EngineStatus (0x288): CoolantTemp, OilTemp, BatteryV, Lambda, MIL, EPC, Fan, Cruise, BrakeSwitch, ClutchSwitch

`dbc/golf4_ext.dbc` contains everything from the minimal DBC plus:
- EngineSensors (0x290): IAT_C, AFR, FuelPressure_kPa, OilPressure_kPa, EGT_C
- BoostControl (0x291): BoostTarget_kPa, BoostError_kPa, WGDC_pct, N75_pct, TurboSpeed_krpm
- FuelIgnition (0x292): IgnAngle_deg, Dwell_ms, InjPW_ms, FuelTrimST_pct, FuelTrimLT_pct, LambdaTarget, FuelTemp_C
- Ethanol (0x293): Ethanol_pct, StoichAFR, FlexFuelMode, FuelDensity
- Knock (0x294): KnockRetard_deg, KnockCount, IATComp_pct, EGTAlarm, OilPressAlarm
- DSG (0x2A0): Gear, ClutchSlip_rpm, TransTemp_C, Mode, ShiftRequest, LaunchActive, TCU_Ready

frames.yaml

frames.yaml drives what TX emits (period + mapping).
If TX does not emit and the service stays “running”, check:
```bash
journalctl -u can-tx.service -n 200 --no-pager
```

Troubleshooting
candump shows nothing

Check can0 UP + bitrate:
```bash
ip -details link show can0
```

Check that TX is running:
```bash
systemctl status can-tx.service --no-pager
```

Check DBC encode errors (missing signals, wrong names):
```bash
journalctl -u can-tx.service -n 200 --no-pager
```

"address already in use"

Change the port or stop the service using it:
```bash
sudo ss -ltnp 'sport = :8010'
sudo systemctl stop liveview.service
```

Backup GitHub

Use backup.sh (see below):
```bash
./backup.sh "optional message"
```

Or with a remote as argument:
```bash
./backup.sh "backup full" git@github.com:olivierbrager/golf4-can-bench.git
```
