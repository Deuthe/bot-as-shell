#!/usr/bin/env python3
"""Receives Alertmanager webhooks and forwards to Telegram."""
import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler

CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "your_chat_id")
TOKEN_FILE = os.environ.get("TELEGRAM_TOKEN_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

token = ""
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        for line in f:
            if line.startswith("TELEGRAM_BOT_TOKEN=") or line.startswith("BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            for alert in data.get("alerts", []):
                labels = alert.get("labels", {})
                annotations = alert.get("annotations", {})
                status = alert.get("status", "firing")
                name = labels.get("alertname", "Unknown")
                summary = annotations.get("summary", "")
                severity = labels.get("severity", "unknown")
                emoji = "\U0001f534" if status == "firing" else "\U0001f7e2"
                msg = f"{emoji} *{name}* [{severity}]\n{summary}"
                self._send_telegram(msg)
        except Exception as e:
            print(f"webhook error: {e}", file=sys.stderr)
        self.send_response(200)
        self.end_headers()

    def _send_telegram(self, text):
        if not token:
            return
        import urllib.request
        data = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)

    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 9095), Handler).serve_forever()
