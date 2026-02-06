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
Lancer en manuel (sans systemd)
TX (émulateur)
```bash
cd /home/olivier/ecu_emulator
source .venv/bin/activate
uvicorn can_tx_emulator:app --host 0.0.0.0 --port 8000
```

Vérifier que ça émet :
```bash
candump -L can0 -n 20
```
## RX (liveview)

Exemple (adapter variables si ton liveview les utilise) :
```bash
cd /home/olivier/ecu_emulator
source .venv/bin/activate

CAN_CH=can0 DBC_PATH=dbc/golf4_min.dbc PUSH_HZ=15 STALE_S=1.0 \
SPEED_FACTOR=1.0 MAP_FACTOR=1.0 ATM_KPA=101.3 \
python -m uvicorn liveview:app --host 0.0.0.0 --port 8010
```

## UI

### TX UI (si activée dans can_tx_emulator) : http://<ip>:8000

#### Snapshot (ECU Emulator)
![ECU Emulator](docs/snapshots/ecu-emulator.png)

### Liveview : http://<ip>:8010

#### Interface (Liveview)
- Barre du haut : état WebSocket (`connected/connecting`), latence visuelle via le point (vert/rouge).
- Barre info CAN/DBC : source CAN, DBC chargé, compteurs RX et indicateur `stale`.
- Onglets : `Debug` (payload complet), `Dev` (KPIs + dérivés), `Street`/`Race` (placeholders).
- Debug :
  - Filtre plein‑texte (ex: `RPM`, `MAP`, `0x280`) sur le payload.
  - Flags (MIL/EPC/Fan/…).
  - Table signals + âges (`age`) en secondes.
- Dev :
  - KPIs dérivés (fenêtre glissante 5s).
  - Flags synthétiques.

#### Snapshots (UI)
Debug :
![Liveview Debug](docs/snapshots/liveview-debug.png)

Dev :
![Liveview Dev](docs/snapshots/liveview-dev.png)

#### Snapshots (extraits)
Status line (barre info CAN/DBC) :
```
CAN:can0 | DBC:golf4_min.dbc | rx:115482/47736 | stale:yes
```

Snapshot payload (extrait /metrics) :
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

Lancer via systemd (recommandé)
TX service

Service : can-tx.service

ExecStart doit être : uvicorn can_tx_emulator:app ...

WorkingDirectory : /home/olivier/ecu_emulator

Commandes :
```bash
sudo systemctl enable --now can-tx.service
systemctl status can-tx.service --no-pager
journalctl -u can-tx.service -f
```
RX service (si présent)
```bash
sudo systemctl enable --now liveview.service
systemctl status liveview.service --no-pager
journalctl -u liveview.service -f
```
Configuration
DBC

dbc/golf4_min.dbc contient :

EngineFast (0x280): RPM, Throttle, Load, Speed, MAP_kPa

EngineStatus (0x288): CoolantTemp, OilTemp, BatteryV, Lambda, MIL, EPC, Fan, Cruise, BrakeSwitch, ClutchSwitch

frames.yaml

frames.yaml pilote ce que le TX émet (période + mapping).
Si le TX n’émet pas et que le service reste “running”, regarder :
```bash
journalctl -u can-tx.service -n 200 --no-pager
```
Troubleshooting
candump ne montre rien

vérifier can0 UP + bitrate :
```bash
ip -details link show can0
```

vérifier que le TX tourne :
```bash
systemctl status can-tx.service --no-pager
```

vérifier erreurs d’encodage DBC (signals manquants, mauvais noms) :
```bash
journalctl -u can-tx.service -n 200 --no-pager
```
“address already in use”

Changer le port ou arrêter le service qui occupe le port :
```bash
sudo ss -ltnp 'sport = :8010'
sudo systemctl stop liveview.service
```
Backup GitHub

Utiliser backup.sh (voir ci-dessous) :
```bash
./backup.sh "message optionnel"
```

Ou avec remote en argument :
```bash
./backup.sh "backup full" git@github.com:olivierbrager/golf4-can-bench.git
```
