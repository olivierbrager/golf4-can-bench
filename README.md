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


# Golf 4 CAN Bench — ECU Emulator (TX) + Liveview (RX)

Ce repo contient :
- **can_tx_emulator.py** : émulateur ECU qui **émet** des trames CAN sur SocketCAN (TX) en encodant via `dbc/golf4_min.dbc`.
- **liveview.py** : dashboard / viewer qui **lit** le CAN (RX), décode via le DBC et expose une UI web.
- **frames.yaml** : configuration des trames émises (périodes + mapping signaux -> champs d’état).
- **dbc/golf4_min.dbc** : DBC minimal (messages : `EngineFast` et `EngineStatus`).

## Prérequis
- Linux avec SocketCAN (can-utils recommandé)
- Python 3.x + venv
- Accès à une interface CAN `can0` (ex: gs_usb)

Paquets Python :
- `python-can`, `cantools`, `fastapi`, `uvicorn[standard]`, `pyyaml`

## Setup rapide

### 1) CAN interface
Configurer `can0` à 500k (adapter si besoin) :
```bash
sudo ip link set can0 down || true
sudo ip link set can0 up type can bitrate 500000
ip -details link show can0
```


Tester réception/émission locale :

cansend can0 123#1122334455667788
candump -L can0 -n 3

2) Python venv
cd /home/olivier/ecu_emulator
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install python-can cantools fastapi "uvicorn[standard]" pyyaml

Lancer en manuel (sans systemd)
TX (émulateur)
cd /home/olivier/ecu_emulator
source .venv/bin/activate
uvicorn can_tx_emulator:app --host 0.0.0.0 --port 8000


Vérifier que ça émet :

candump -L can0 -n 20

RX (liveview)

Exemple (adapter variables si ton liveview les utilise) :

cd /home/olivier/ecu_emulator
source .venv/bin/activate

CAN_CH=can0 DBC_PATH=dbc/golf4_min.dbc PUSH_HZ=15 STALE_S=1.0 \
SPEED_FACTOR=1.0 MAP_FACTOR=1.0 ATM_KPA=101.3 \
python -m uvicorn liveview:app --host 0.0.0.0 --port 8010


UI :

TX UI (si activée dans can_tx_emulator) : http://<ip>:8000

Liveview : http://<ip>:8010

Lancer via systemd (recommandé)
TX service

Service : can-tx.service

ExecStart doit être : uvicorn can_tx_emulator:app ...

WorkingDirectory : /home/olivier/ecu_emulator

Commandes :

sudo systemctl enable --now can-tx.service
systemctl status can-tx.service --no-pager
journalctl -u can-tx.service -f

RX service (si présent)
sudo systemctl enable --now liveview.service
systemctl status liveview.service --no-pager
journalctl -u liveview.service -f

Configuration
DBC

dbc/golf4_min.dbc contient :

EngineFast (0x280): RPM, Throttle, Load, Speed, MAP_kPa

EngineStatus (0x288): CoolantTemp, OilTemp, BatteryV, Lambda, MIL, EPC, Fan, Cruise, BrakeSwitch, ClutchSwitch

frames.yaml

frames.yaml pilote ce que le TX émet (période + mapping).
Si le TX n’émet pas et que le service reste “running”, regarder :

journalctl -u can-tx.service -n 200 --no-pager

Troubleshooting
candump ne montre rien

vérifier can0 UP + bitrate :

ip -details link show can0


vérifier que le TX tourne :

systemctl status can-tx.service --no-pager


vérifier erreurs d’encodage DBC (signals manquants, mauvais noms) :

journalctl -u can-tx.service -n 200 --no-pager

“address already in use”

Changer le port ou arrêter le service qui occupe le port :

sudo ss -ltnp 'sport = :8010'
sudo systemctl stop liveview.service

Backup GitHub

Utiliser backup.sh (voir ci-dessous) :

./backup.sh "message optionnel"


Ou avec remote en argument :

./backup.sh "backup full" git@github.com:olivierbrager/golf4-can-bench.git