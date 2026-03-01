#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/DashBoard/golf4-can-bench}"
APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
BITRATE="${BITRATE:-500000}"
KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8011/?fullscreen=street}"

WITH_TX=0
WITH_KIOSK=1
INSTALL_PACKAGES=1
CHROMIUM_BIN=""

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install_rpi5_kiosk.sh [options]

Options:
  --with-tx            Enable can-tx.service
  --without-kiosk      Do not install/enable Chromium kiosk service
  --skip-packages      Skip apt package installation
  --repo-dir PATH      Override repository directory
  --app-user USER      User running liveview/kiosk services
  --bitrate N          CAN bitrate (default: 500000)
  --kiosk-url URL      Kiosk URL (default: http://127.0.0.1:8011/?fullscreen=street)
  -h, --help           Show this help
EOF
}

enable_now_or_warn() {
  local unit="$1"
  if ! systemctl enable --now "$unit"; then
    echo "Warning: failed to start $unit now. Unit installed and enabled; check logs when hardware/session is ready." >&2
    return 0
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-tx) WITH_TX=1; shift ;;
    --without-kiosk) WITH_KIOSK=0; shift ;;
    --skip-packages) INSTALL_PACKAGES=0; shift ;;
    --repo-dir) REPO_DIR="$2"; shift 2 ;;
    --app-user) APP_USER="$2"; shift 2 ;;
    --bitrate) BITRATE="$2"; shift 2 ;;
    --kiosk-url) KIOSK_URL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repository directory not found: $REPO_DIR" >&2
  exit 1
fi

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  echo "User not found: $APP_USER" >&2
  exit 1
fi

if [[ "$INSTALL_PACKAGES" -eq 1 ]]; then
  apt-get update
  if ! apt-get install -y python3-venv python3-pip can-utils chromium unclutter; then
    apt-get install -y python3-venv python3-pip can-utils chromium-browser unclutter
  fi
fi

if command -v chromium-browser >/dev/null 2>&1; then
  CHROMIUM_BIN="$(command -v chromium-browser)"
elif command -v chromium >/dev/null 2>&1; then
  CHROMIUM_BIN="$(command -v chromium)"
fi

if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$REPO_DIR/.venv"
fi
sudo -u "$APP_USER" "$REPO_DIR/.venv/bin/pip" install -U pip
sudo -u "$APP_USER" "$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

mkdir -p /etc/golf4
if [[ ! -f /etc/golf4/liveview.env ]]; then
  cp "$REPO_DIR/systemd/liveview.env.example" /etc/golf4/liveview.env
fi
sed -i "s#^DBC_PATH=.*#DBC_PATH=$REPO_DIR/dbc/golf4_ext.dbc#" /etc/golf4/liveview.env

cp "$REPO_DIR/systemd/can0.service" /etc/systemd/system/can0.service
sed -i "s/bitrate [0-9][0-9]*/bitrate $BITRATE/" /etc/systemd/system/can0.service
cp "$REPO_DIR/systemd/can0-watchdog.service" /etc/systemd/system/can0-watchdog.service
cp "$REPO_DIR/systemd/can0-watchdog.timer" /etc/systemd/system/can0-watchdog.timer
install -m 0755 "$REPO_DIR/systemd/can0-watchdog.sh" /usr/local/bin/can0-watchdog.sh

sed \
  -e "s#__APP_USER__#$APP_USER#g" \
  -e "s#__REPO_DIR__#$REPO_DIR#g" \
  "$REPO_DIR/systemd/liveview.service.example" > /etc/systemd/system/liveview.service

if [[ "$WITH_TX" -eq 1 ]]; then
  sed \
    -e "s#__APP_USER__#$APP_USER#g" \
    -e "s#__REPO_DIR__#$REPO_DIR#g" \
    "$REPO_DIR/systemd/can-tx.service.example" > /etc/systemd/system/can-tx.service
fi

if [[ "$WITH_KIOSK" -eq 1 ]]; then
  if [[ -z "$CHROMIUM_BIN" ]]; then
    echo "Chromium binary not found. Install chromium/chromium-browser or run with --without-kiosk." >&2
    exit 1
  fi
  sed \
    -e "s#__APP_USER__#$APP_USER#g" \
    -e "s#__CHROMIUM_BIN__#$CHROMIUM_BIN#g" \
    -e "s#__KIOSK_URL__#$KIOSK_URL#g" \
    "$REPO_DIR/systemd/chromium-kiosk.service.example" > /etc/systemd/system/chromium-kiosk.service
fi

systemctl daemon-reload
enable_now_or_warn can0.service
enable_now_or_warn can0-watchdog.timer
enable_now_or_warn liveview.service

if [[ "$WITH_TX" -eq 1 ]]; then
  enable_now_or_warn can-tx.service
fi

if [[ "$WITH_KIOSK" -eq 1 ]]; then
  enable_now_or_warn chromium-kiosk.service
fi

echo
echo "Install complete."
echo "Health: curl http://127.0.0.1:8011/health"
echo "Metrics: curl http://127.0.0.1:8011/metrics"
