#!/usr/bin/env python3
"""Prometheus exporter for bot and service health."""
import subprocess, time
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_SERVICE = "opencode-bot.service"
SEC_SERVER = "security-server.service"
SEC_MONITOR = "security-monitor.service"
POLL_INTERVAL = 15

cache = {"time": 0, "metrics": ""}

def collect():
    now = time.time()
    if now - cache["time"] < POLL_INTERVAL:
        return cache["metrics"]
    lines = []
    for name, svc in [("bot", BOT_SERVICE), ("sec_server", SEC_SERVER), ("sec_monitor", SEC_MONITOR)]:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
        up = 1 if r.stdout.strip() == "active" else 0
        lines.append(f'{name}_up {up}')
    r = subprocess.run(["pgrep", "-f", "opencode"], capture_output=True, text=True, timeout=5)
    lines.append(f'opencode_processes {len(r.stdout.strip().splitlines()) if r.stdout.strip() else 0}')
    lines.append(f'scrape_duration_seconds {time.time() - now}')
    result = "# HELP bot_up Bot service health\n# TYPE bot_up gauge\n" + "\n".join(lines) + "\n"
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
