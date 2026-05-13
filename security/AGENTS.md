# Security System

Camera motion detection + HTTP command server. Controlled via Telegram bot.

## Architecture

```
Telegram Bot ──(/activate via HTTP:8901)──> server.py ──> ctl.sh ──> .state + play_sound.py
                                                          └──> monitor.py reads .state, loops camera
                                                                    └── alert.py ──> Telegram / ntfy / Email / Webhook
```

## Components

| File | Purpose |
|------|---------|
| `server.py` | HTTPS command server on :8901, Basic auth, rate-limited (15 req/60s), TLS, localhost-only |
| `monitor.py` | OpenCV motion detection loop, MOG2 background subtraction, PID lock |
| `alert.py` | Multi-channel alert dispatch: Telegram, ntfy, SMTP email, webhook |
| `config.py` | Configuration loader — reads `.env`, populates CONFIG dict |
| `ctl.sh` | Writes `.state` JSON, plays WAV sounds |
| `run.sh` | Start/stop/restart/status/log for monitor (fallback — systemd is primary) |
| `setup.sh` | One-time setup: groups, systemd, TLS cert, sound generation |
| `redeploy.sh` | Re-copy systemd service files and restart services |
| `gen_sounds.py` | Generates WAV sound effects (activate/deactivate/alarm tones) |
| `play_sound.py` | Plays WAV files via `aplay` or `ffplay` |

## Security

- `.env` chmod 600, no hardcoded secrets
- `server.py` binds to `127.0.0.1` only
- Rate limiting: 15 requests per 60s per IP
- TLS with self-signed cert
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`
- Token sanitization in error logs (`alert.py`)

## Alert delivery

| Method | Data |
|--------|------|
| Telegram (primary) | MP4 video + JPEG photo + caption |
| ntfy.sh | Push notification + image |
| SMTP email | Text + image attachment |
| Webhook | JSON POST |

## Bot commands

| Command | What it does |
|---------|-------------|
| `/activatesecurity` | HTTP `:8901/activate` → `.state: true` + sound + monitor detects motion |
| `/deactivatesecurity` | HTTP `:8901/deactivate` → `.state: false` + sound + monitor idles |
| `/securitystatus` | HTTP `:8901/status` → shows armed/disarmed |
