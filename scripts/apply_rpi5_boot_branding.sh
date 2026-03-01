#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPLASH_SRC_DEFAULT="$REPO_DIR/static/Splash_OB_black.png"
SPLASH_SRC="$SPLASH_SRC_DEFAULT"
WIDTH=1600
HEIGHT=600
SKIP_EEPROM=0

usage() {
  cat <<EOF
Usage: sudo ./scripts/apply_rpi5_boot_branding.sh [options]

Options:
  --splash PATH     Source splash image (default: $SPLASH_SRC_DEFAULT)
  --width N         Splash width (default: 1600)
  --height N        Splash height (default: 600)
  --skip-eeprom     Do not change EEPROM bootloader config
  -h, --help        Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --splash) SPLASH_SRC="$2"; shift 2 ;;
    --width) WIDTH="$2"; shift 2 ;;
    --height) HEIGHT="$2"; shift 2 ;;
    --skip-eeprom) SKIP_EEPROM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

if [[ ! -f "$SPLASH_SRC" ]]; then
  echo "Splash image not found: $SPLASH_SRC" >&2
  exit 1
fi

if [[ ! -f "$REPO_DIR/systemd/chromium-kiosk-sway.service.example" ]]; then
  echo "Missing file: $REPO_DIR/systemd/chromium-kiosk-sway.service.example" >&2
  exit 1
fi
if [[ ! -f "$REPO_DIR/systemd/sway-kiosk.conf.example" ]]; then
  echo "Missing file: $REPO_DIR/systemd/sway-kiosk.conf.example" >&2
  exit 1
fi
if [[ ! -f "$REPO_DIR/systemd/plymouth-quit.override.conf.example" ]]; then
  echo "Missing file: $REPO_DIR/systemd/plymouth-quit.override.conf.example" >&2
  exit 1
fi

echo "[1/7] Install required packages..."
apt-get update -y
apt-get install -y plymouth plymouth-themes imagemagick sway chromium

echo "[2/7] Install Plymouth theme and splash..."
install -d /usr/share/plymouth/themes/golf4
cat > /usr/share/plymouth/themes/golf4/golf4.plymouth <<'EOF'
[Plymouth Theme]
Name=Golf4 Splash
Description=Golf4 custom kiosk splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/golf4
ScriptFile=/usr/share/plymouth/themes/golf4/golf4.script
EOF

cat > /usr/share/plymouth/themes/golf4/golf4.script <<'EOF'
Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);

logo.image = Image("splash.png");
logo.sprite = Sprite(logo.image);
logo.x = Window.GetX() + Window.GetWidth() / 2 - logo.image.GetWidth() / 2;
logo.y = Window.GetY() + Window.GetHeight() / 2 - logo.image.GetHeight() / 2;
logo.sprite.SetX(logo.x);
logo.sprite.SetY(logo.y);
logo.sprite.SetZ(10000);
EOF

if [[ -f /usr/share/plymouth/themes/golf4/splash.png ]]; then
  cp /usr/share/plymouth/themes/golf4/splash.png /usr/share/plymouth/themes/golf4/splash.prev.$(date +%Y%m%d-%H%M%S).png
fi
cp "$SPLASH_SRC" /usr/share/plymouth/themes/golf4/splash.png
magick /usr/share/plymouth/themes/golf4/splash.png -resize "${WIDTH}x${HEIGHT}^" -gravity center -extent "${WIDTH}x${HEIGHT}" /usr/share/plymouth/themes/golf4/splash.png
plymouth-set-default-theme -R golf4

echo "[3/7] Apply cmdline/config.txt quiet splash settings..."
CMDLINE_FILE=""
if [[ -f /boot/firmware/cmdline.txt ]]; then
  CMDLINE_FILE="/boot/firmware/cmdline.txt"
elif [[ -f /boot/cmdline.txt ]]; then
  CMDLINE_FILE="/boot/cmdline.txt"
fi
if [[ -z "$CMDLINE_FILE" ]]; then
  echo "cmdline.txt not found" >&2
  exit 1
fi

cp "$CMDLINE_FILE" "${CMDLINE_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
CMDLINE="$(cat "$CMDLINE_FILE")"
for tok in quiet splash logo.nologo vt.global_cursor_default=0 plymouth.ignore-serial-consoles loglevel=0 systemd.show_status=0 rd.udev.log_level=0 udev.log_priority=0 video=HDMI-A-1:1600x600@60; do
  [[ " $CMDLINE " == *" $tok "* ]] || CMDLINE="$CMDLINE $tok"
done
echo "$CMDLINE" | tr -s ' ' | sed -E 's/^ //; s/ $//' > "$CMDLINE_FILE"

CFG_FILE=""
if [[ -f /boot/firmware/config.txt ]]; then
  CFG_FILE="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  CFG_FILE="/boot/config.txt"
fi
if [[ -n "$CFG_FILE" ]]; then
  cp "$CFG_FILE" "${CFG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
  grep -q '^disable_splash=1$' "$CFG_FILE" || printf '\n# Hide firmware splash for kiosk boot\ndisable_splash=1\n' >> "$CFG_FILE"
fi

echo "[4/7] Install Sway kiosk + Plymouth handoff units..."
install -d /etc/sway
install -d /etc/systemd/system/plymouth-quit.service.d
cp "$REPO_DIR/systemd/sway-kiosk.conf.example" /etc/sway/kiosk.conf
cp "$REPO_DIR/systemd/chromium-kiosk-sway.service.example" /etc/systemd/system/chromium-kiosk-sway.service
cp "$REPO_DIR/systemd/plymouth-quit.override.conf.example" /etc/systemd/system/plymouth-quit.service.d/override.conf

echo "[5/7] Disable conflicting services..."
systemctl disable --now chromium-kiosk.service 2>/dev/null || true
systemctl disable --now chromium-kiosk-wayland.service 2>/dev/null || true
systemctl disable --now getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service 2>/dev/null || true

echo "[6/7] Enable Sway kiosk service..."
systemctl daemon-reload
systemctl enable --now chromium-kiosk-sway.service

echo "[7/7] Apply EEPROM bootloader quiet settings..."
if [[ "$SKIP_EEPROM" -eq 0 ]]; then
  BOOT_ORDER="$(rpi-eeprom-config | awk -F= '/^BOOT_ORDER=/{print $2; exit}')"
  [[ -n "$BOOT_ORDER" ]] || BOOT_ORDER="0xf461"
  TMP_CFG="$(mktemp)"
  cat > "$TMP_CFG" <<EOF
[all]
BOOT_UART=0
BOOT_ORDER=$BOOT_ORDER
NET_INSTALL_AT_POWER_ON=0
EOF
  rpi-eeprom-config --apply "$TMP_CFG"
  rm -f "$TMP_CFG"
else
  echo "Skipped EEPROM update (--skip-eeprom)."
fi

echo
echo "Boot branding applied."
echo "Active splash image: /usr/share/plymouth/themes/golf4/splash.png"
echo "Reboot required: sudo reboot"
