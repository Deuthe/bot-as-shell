#!/usr/bin/env bash
ACTION="${1:-status}"
DIR="$(cd "$(dirname "$0")" && pwd)"

case "$ACTION" in
  activate)
    echo '{"active": true}' > "$DIR/.state"
    chmod 644 "$DIR/.state"
    timeout 2 python3 "$DIR/play_sound.py" activate 2>/dev/null || true
    echo "Security ACTIVATED"
    ;;
  deactivate)
    echo '{"active": false}' > "$DIR/.state"
    chmod 644 "$DIR/.state"
    timeout 2 python3 "$DIR/play_sound.py" deactivate 2>/dev/null || true
    echo "Security DEACTIVATED"
    ;;
  status)
    if [ -f "$DIR/.state" ]; then
      cat "$DIR/.state"
    else
      echo '{"active": false}'
    fi
    ;;
  *)
    echo "Usage: $0 {activate|deactivate|status}"
    exit 1
    ;;
esac
