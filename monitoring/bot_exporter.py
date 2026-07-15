#!/usr/bin/env python3
"""Prometheus exporter for bot and service health."""
import subprocess, time, os
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_SERVICE = "opencode-bot.service"
SEC_SERVER = "security-server.service"
SEC_MONITOR = "security-monitor.service"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
COUNTER_FILE = os.path.join(_PROJECT_ROOT, 'bot', '.counter')
MOTION_COUNTER = os.path.join(_PROJECT_ROOT, 'security', '.motion_counter')
POLL_INTERVAL = 15

cache = {"time": 0, "metrics": ""}

def _read_counter(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return int(f.read().strip() or '0')
    except Exception:
        pass
    return 0

def collect():
    now = time.time()
    if now - cache["time"] < POLL_INTERVAL:
        return cache["metrics"]
    lines = []
    # Add more services here as (metric_name, service_name) tuples.
    # Example: ("glance", "glance.service")
    for name, svc in [("bot", BOT_SERVICE), ("sec_server", SEC_SERVER), ("sec_monitor", SEC_MONITOR)]:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
        up = 1 if r.stdout.strip() == "active" else 0
        lines.append(f'{name}_up {up}')
        lines.append(f'service_up{{service="{svc.replace(".service","")}"}} {up}')
    r = subprocess.run(["pgrep", "-f", "opencode"], capture_output=True, text=True, timeout=5)
    lines.append(f'opencode_processes {len(r.stdout.strip().splitlines()) if r.stdout.strip() else 0}')
    lines.append(f'bot_commands_total {_read_counter(COUNTER_FILE)}')
    lines.append(f'motion_events_total {_read_counter(MOTION_COUNTER)}')
    lines.append(f'scrape_duration_seconds {time.time() - now}')
    result = (
        "# HELP bot_up Bot service health\n# TYPE bot_up gauge\n"
        "# HELP service_up Labeled service health (add more by extending the list)\n# TYPE service_up gauge\n"
        "# HELP opencode_processes Number of opencode processes\n# TYPE opencode_processes gauge\n"
        "# HELP bot_commands_total Total bot commands received\n# TYPE bot_commands_total counter\n"
        "# HELP motion_events_total Total security motion events\n# TYPE motion_events_total counter\n"
        + "\n".join(lines) + "\n"
    )
    cache["time"] = time.time()
    cache["metrics"] = result
    return result

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(collect().encode())
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 9101), Handler).serve_forever()
