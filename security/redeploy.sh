#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Redeploying security services ==="

cp "$DIR/../systemd/security-server.service" /etc/systemd/system/
cp "$DIR/../systemd/security-monitor.service" /etc/systemd/system/
systemctl daemon-reload

echo "Restarting security-server..."
systemctl restart security-server

echo "Restarting security-monitor..."
systemctl restart security-monitor

echo "Done."
