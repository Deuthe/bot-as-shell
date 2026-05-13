#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
USER="${SUDO_USER:-deuthe}"

echo "=== Security System Setup ==="

echo "[1/4] Adding user to video and audio groups..."
usermod -aG video,audio "$USER"

echo "[2/4] Installing systemd services..."
cp "$DIR/../systemd/security-server.service" /etc/systemd/system/
cp "$DIR/../systemd/security-monitor.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable security-server security-monitor
systemctl start security-server security-monitor

echo "[3/4] Generating TLS cert..."
CERTS="$DIR/certs"
mkdir -p "$CERTS"
openssl req -x509 -newkey rsa:2048 -keyout "$CERTS/key.pem" -out "$CERTS/cert.pem" -days 3650 -nodes -subj '/CN=localhost'

echo "[4/4] Generating sound effects..."
python3 "$DIR/gen_sounds.py"

echo ""
echo "=== Setup Complete ==="
echo "Log out and back in for group changes to take effect."
echo "Then configure .env and restart services."
