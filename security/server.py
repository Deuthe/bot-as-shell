#!/usr/bin/env python3
import os, subprocess, json, signal, sys, socket, base64, time, collections, ssl
from http.server import HTTPServer, BaseHTTPRequestHandler

DIR = os.path.dirname(os.path.abspath(__file__))
CTL = DIR + '/ctl.sh'
CERTS = DIR + '/certs'
HOST = '127.0.0.1'
PORT = 8901
PIDFILE = DIR + '/.server_pid'
CERT_FILE = CERTS + '/cert.pem'
KEY_FILE = CERTS + '/key.pem'

from config import load_dotenv
load_dotenv()
PASSWORD = os.environ.get('SECURITY_PW', '')
if not PASSWORD:
    print("FATAL: SECURITY_PW not set in environment or .env")
    sys.exit(1)

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 15
_rate_buckets = collections.defaultdict(list)

def check_auth(headers):
    auth = headers.get('Authorization', '')
    if not auth.startswith('Basic '):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode('utf-8', errors='replace')
        return decoded == f':{PASSWORD}'
    except Exception:
        return False

class Handler(BaseHTTPRequestHandler):
    def _rate_limited(self):
        ip = self.client_address[0]
        now = time.time()
        _rate_buckets[ip] = [t for t in _rate_buckets[ip] if now - t < RATE_LIMIT_WINDOW]
        if len(_rate_buckets[ip]) >= RATE_LIMIT_MAX:
            return True
        _rate_buckets[ip].append(now)
        return False

    def _auth_required(self):
        if self._rate_limited():
            self.send_response(429)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Rate limited. Try again later.\n')
            return False
        if not check_auth(self.headers):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Security"')
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Authorization required\n')
            return False
        return True

    def _run(self, action):
        try:
            out = subprocess.check_output([CTL, action], stderr=subprocess.STDOUT, timeout=10).decode().strip()
            return (200, out + '\n')
        except subprocess.CalledProcessError as e:
            return (500, e.output.decode().strip() + '\n')
        except Exception as e:
            return (500, str(e) + '\n')

    def do_GET(self):
        if not self._auth_required():
            return
        path = self.path.lower().rstrip('/')
        if path in ('/activate', '/activatesecurity'):
            code, msg = self._run('activate')
        elif path in ('/deactivate', '/deactivatesecurity'):
            code, msg = self._run('deactivate')
        elif path == '/status':
            code, msg = self._run('status')
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Use /activate, /deactivate, or /status\n')
            return
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-XSS-Protection', '0')
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

def write_pid():
    with open(PIDFILE, 'w') as f:
        f.write(str(os.getpid()))

if __name__ == '__main__':
    if not os.path.isfile(CERT_FILE) or not os.path.isfile(KEY_FILE):
        print(f"FATAL: TLS cert/key not found in {CERTS}/")
        print(f"  Generate with: openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} -days 3650 -nodes -subj '/CN=localhost'")
        sys.exit(1)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)

    write_pid()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(5)
    server = HTTPServer((HOST, PORT), Handler, bind_and_activate=False)
    server.socket = ctx.wrap_socket(s, server_side=True)
    server.server_bind = lambda: None
    server.server_activate = lambda: None
    print(f"Security command server on https://{HOST}:{PORT}")
    server.serve_forever()
