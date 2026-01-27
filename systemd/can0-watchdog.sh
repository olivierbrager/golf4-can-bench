#!/bin/bash
IF=can0

STATE=$(ip -details link show $IF | grep -o "state [A-Z-]*" | awk '{print $2}')

if [[ "$STATE" == "BUS-OFF" || "$STATE" == "STOPPED" || "$STATE" == "DOWN" ]]; then
  logger -t can0-watchdog "Resetting $IF (state=$STATE)"
  ip link set $IF down
  ip link set $IF up type can bitrate 500000
fi
