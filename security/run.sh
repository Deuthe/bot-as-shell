#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/.monitor_pid"

case "${1:-help}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "Monitor already running (PID $(cat "$PIDFILE"))"
      exit 1
    fi
    if [ -f "$DIR/.env" ]; then
      set -a
      . "$DIR/.env"
      set +a
    fi
    nohup python3 -u "$DIR/monitor.py" > "$DIR/monitor.log" 2>&1 &
    PID=$!
    echo $PID > "$PIDFILE"
    echo "Monitor started (PID $PID)"
    ;;
  stop)
    if [ ! -f "$PIDFILE" ]; then
      echo "No PID file found"
      exit 1
    fi
    PID=$(cat "$PIDFILE")
    kill "$PID" 2>/dev/null && echo "Monitor stopped (PID $PID)" || echo "Monitor not running"
    rm -f "$PIDFILE"
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "Monitor running (PID $(cat "$PIDFILE"))"
      "$DIR/ctl.sh" status
    else
      echo "Monitor not running"
      rm -f "$PIDFILE"
    fi
    ;;
  log)
    tail -f "$DIR/monitor.log"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|log}"
    exit 1
    ;;
esac
