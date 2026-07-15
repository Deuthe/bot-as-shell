# bot-as-shell

**Manage your Linux server over Telegram using natural language.** The AI interprets your messages, runs shell commands on your server, and replies with the result — all on your own hardware with no cloud AI subscriptions.

```
  You --(Telegram)--> Bot API --> bot.py --> opencode CLI --> AI model --> Linux shell
                                                                 (big-pickle, local)
```

## System Architecture

The system has three main components, each optional and independently deployable:

```
  Telegram Bot ---subprocess---> opencode CLI ---> shell commands
       |
       |---HTTPS:8901---> Security Server (arm/disarm/status)
       |                      |
       |                  Camera Monitor (OpenCV MOG2)
       |                      |
       |                  Alert Dispatcher (Telegram / ntfy / Email / Webhook)
       |
       |---ffmpeg---> USB Camera (/pic)
       |
       +---faster-whisper---> Voice Transcription

  Monitoring Stack (optional):
    node_exporter (9100) ----+
    bot_exporter  (9101) ----+--- Prometheus (9090) --- Grafana (3000)
    process-exp   (9256) ----+        |
                                Alertmanager (9093)
                                     |
                                 telegram_webhook (9095)
```

All services that need remote access go through Tailscale (WireGuard VPN). No ports are open on the public internet. The security server, Prometheus, and Alertmanager bind exclusively to `127.0.0.1`.

## Features

- **Natural language server management** — "check disk usage", "update packages", "who is online", "restart service X"
- **Voice commands** — send a voice message, Whisper transcribes and executes it
- **SSH auth monitor** — tails `/var/log/auth.log`, alerts on failed SSH/sudo/su attempts and new user creation
- **Plan / Build modes** — `/plan` to preview before executing, `/build` to execute directly
- **Destructive action confirmation** — keywords like "wipe", "format", "shutdown" require explicit confirmation
- **Prompt injection defense** — blocks attempts to override the AI's system prompt
- **USB camera capture** — `/pic` captures from a connected webcam
- **Camera motion detection** *(optional)* — OpenCV-based security system with multi-channel alerting
- **Prometheus + Grafana monitoring** *(optional)* — system metrics, alert rules, dashboards, Telegram alerts

## Prerequisites

- **Python 3.11+**
- **opencode CLI** — `npm install -g @opencode/cli`
- **Telegram bot token** — free from [@BotFather](https://t.me/BotFather)

## Quick Setup

```
  1. Clone the repository
  2. Create a Python virtual environment and install dependencies
  3. Configure .env with your Telegram credentials
  4. Run the bot
```

```bash
# Clone
git clone <repo-url> && cd bot-as-shell

# Virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r bot/requirements.txt

# Configure
cp .env.example .env
chmod 600 .env
```

### Getting your Telegram credentials

**Bot token:** Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, pick a name and username. It replies with a token that looks like `123456:ABCdef...`.

**Your chat ID:** Message [@userinfobot](https://t.me/userinfobot) — it replies with your numeric ID.

Edit `.env` with both values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_CHAT_ID=your_numeric_chat_id
```

```bash
# Run
python3 bot/bot.py
```

Message your bot on Telegram. That's it.

## Configuration

All configuration is read from `.env` at startup. The bot discovers the file by checking the project root, the `bot/` directory, and the `security/` directory. The `.env` file should be `chmod 600`.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Bot API token from @BotFather |
| `ALLOWED_CHAT_ID` | *(required)* | Only this Telegram user ID can talk to the bot |
| `WORKDIR` | current directory | Working directory for AI shell commands |
| `OPENCODE_BIN` | `opencode` from PATH | Path to opencode CLI binary |
| `LOG_DIR` | `./logs` | Log storage location |
| `PROCESS_TIMEOUT` | `600` | Max command execution time in seconds |
| `CONFIRM_DESTRUCTIVE` | `true` | Ask confirmation before destructive ops |
| `INJECTION_DEFENSE` | `true` | Block prompt injection attempts |
| `SECURITY_PW` | *(required for security)* | HTTP Basic Auth password for security API |
| `SECURITY_URL` | `https://localhost:8901` | Security API endpoint |
| `SSH_MONITOR_LOG` | `/var/log/auth.log` | Path to auth log for SSH monitor |

## Commands

| Command | Description |
|---------|-------------|
| *anything* | Natural language — AI interprets it and runs the appropriate shell commands |
| `/docs` | Full documentation |
| `/persona [text]` | View or change AI personality |
| `/reset` | Clear conversation history |
| `/cancel` | Abort a running request |
| `/logs` | View today's activity log |
| `/pic` | Capture photo from USB camera |
| `/plan` | Enable plan mode — AI presents a plan before executing |
| `/build` | Enable build mode — AI executes directly |
| `/mode` | Show current mode |
| `/activatesecurity` | Arm camera motion detection |
| `/deactivatesecurity` | Disarm camera motion detection |
| `/securitystatus` | Check if security is active |

Voice and audio messages are transcribed using faster-whisper (tiny model, int8 quantized) and processed as natural language text. Commands spoken in voice messages are recognized and routed to their handlers.

## Safety Features

### 1. Authorization

Only the Telegram user ID specified in `ALLOWED_CHAT_ID` is authorized. All other messages are silently ignored with no response.

### 2. Prompt Injection Defense

Every message is scanned for injection patterns before being passed to opencode. These patterns attempt to override the AI's system prompt or extract sensitive information:

> "ignore all previous instructions", "you are now required to", "forget everything", "system prompt", "rewrite your prompt", "output the prompt", "reveal your instructions"

When detected: the message is blocked, logged as `INJECTION_BLOCKED`, and the user receives a warning.

### 3. Destructive Action Confirmation

Messages matching keywords associated with destructive operations trigger a confirmation prompt before execution:

> delete all, remove all, wipe, format, destroy, erase everything, nuke, shutdown, reboot, reset to factory, clean install, reinstall OS, kill all

The bot stores the message with a timestamp and asks the user to reply "yes" to proceed. Pending confirmations expire after 60 seconds.

### 4. Prompt Structural Hardening

All user messages are wrapped in delimiters:

```
[USER] ---BEGIN USER MESSAGE---
[user text]
---END USER MESSAGE---
```

This prevents injected text from breaking out of the user-message boundary and overriding system instructions. The AI sees a clear separation between its system persona and user-provided content.

## SSH Auth Monitor

The bot tails `/var/log/auth.log` and sends a Telegram alert when it detects:

- **Failed SSH login attempts** — includes the source IP and attempted usernames
- **sudo authentication failures**
- **su authentication failures**
- **New user accounts** created via `useradd`

The monitor persists its position across bot restarts using a state file (`.ssh_monitor.state`). It checks every 10 seconds. The log path is configurable via `SSH_MONITOR_LOG`.

## Message Processing Pipeline

When you send a message to the bot, it goes through this pipeline:

```
  1. AUTHORIZATION CHECK — chat_id must match ALLOWED_CHAT_ID
  2. PENDING CONFIRMATION — check for pending destructive-action reply
  3. PROMPT INJECTION DEFENSE — scan and block injection patterns
  4. DESTRUCTIVE ACTION CONFIRMATION — scan for destructive keywords
  5. PROMPT BUILDING — assemble persona + history + delimiters
  6. OPENCODE EXECUTION — subprocess with 600s timeout
  7. RESPONSE PROCESSING — strip ANSI, clean markers, chunk at 4000 chars
  8. CLEANUP — update history, increment counter, delete old logs
```

Key design decisions:

- **No `--continue` flag** — every request starts a fresh opencode session. The AI sees the current message plus the last 5 exchanges for context. This prevents stale context from confusing the AI.
- **`--dangerously-skip-permissions`** — opencode is launched with this flag because stdin is piped from subprocess. opencode never prompts for approval when stdin is a pipe.
- **Polling auto-restart** — if Telegram polling fails from a network error, the bot catches it, waits 5 seconds, and restarts automatically. No silent death.
- **Progress notifications** — typing indicator every 30 seconds, status updates at 3, 5, 7, and 9 minutes.

## Optional: Camera Security System

The `security/` directory contains a complete camera motion detection system. It is **isolated and optional**. The bot works perfectly without it — commands like `/activatesecurity` just return errors if the security server isn't running.

**Delete the `security/` directory entirely if not needed. No code changes required.**

### Architecture

```
  server.py --[ctl.sh]--> .state + play_sound.py
                               |
                         monitor.py (OpenCV MOG2)
                               |
                          alert.py
                         /   |   \
                   Telegram ntfy Email/Webhook
```

### Components

| File | Description |
|------|-------------|
| `server.py` | HTTPS command server on `127.0.0.1:8901`. Basic auth, rate-limited (15 req/60s), self-signed TLS, security headers. Endpoints: `/activate`, `/deactivate`, `/status` |
| `monitor.py` | OpenCV MOG2 background subtraction motion detection. Configurable threshold (default 9000), 150-frame warmup, 30s cooldown, PID lock, auto-cleanup of captures after 12 hours |
| `alert.py` | Multi-channel alert dispatcher. Primary: Telegram (MP4 video + JPEG + caption). Fallbacks: ntfy.sh push notification, SMTP email, JSON webhook |
| `config.py` | dotenv loader and configuration dictionary |
| `ctl.sh` | Writes `{"active": true/false}` to `.state`, plays WAV sounds through speakers |
| `run.sh` | Process manager for monitor (start/stop/restart/status/log) — fallback when systemd is not used |
| `setup.sh` | One-time setup: adds user to `video` and `audio` groups, copies systemd service files, generates TLS cert + sound effects |
| `gen_sounds.py` | Generates WAV files: `activate.wav` (rising 880+1320Hz), `deactivate.wav` (descending), `alarm.wav` (alternating) |
| `play_sound.py` | Plays WAV files via `aplay` or `ffplay` fallback |

### Setup

```bash
cd security
sudo bash setup.sh
```

## Optional: Monitoring Stack

The `monitoring/` directory contains a complete Prometheus + Grafana observability stack.

### Architecture

```
  node_exporter (127.0.0.1:9100) ──┐
  bot_exporter  (127.0.0.1:9101) ──┤── Prometheus (127.0.0.1:9090) ── Grafana
  process-exp   (127.0.0.1:9256) ──┘         │
                                        Alertmanager (127.0.0.1:9093)
                                              │
                                        telegram_webhook.py (127.0.0.1:9095)
                                              │
                                        Telegram Bot API
```

### Port Map

| Port | Component | Description |
|------|-----------|-------------|
| 9100 | node_exporter | System metrics (CPU, RAM, disk, network) |
| 9101 | bot_exporter | Service health metrics (bot_up, sec_server_up, etc.) |
| 9256 | process-exporter | Per-process CPU and memory |
| 9090 | Prometheus | Metrics store, query API, alert evaluation |
| 9093 | Alertmanager | Alert deduplication and routing |
| 9095 | telegram_webhook | Python receiver that forwards alerts to Telegram |
| 3000 | Grafana | Dashboards with Prometheus datasource |

All services bind to `127.0.0.1` except Grafana (`0.0.0.0:3000`). If Grafana is exposed, restrict access with a firewall rule (e.g., UFW allow only from your VPN interface). Systemd service templates in `systemd/` use `{{USER}}` and `{{DIR}}` placeholders.

### Alert Rules

| Alert | Condition | Severity |
|-------|-----------|----------|
| BotDown | `up{job="bot"} == 0` for 30s | critical |
| NodeDown | `up{job="node"} == 0` for 30s | critical |
| DiskSpaceLow | disk usage > 85% for 5m | warning |

## Production Deployment (systemd)

```bash
# Substitute placeholders with your actual values
USER=yourusername DIR=/path/to/bot-as-shell

# Copy and customize service files
for svc in systemd/*.service; do
  sed -e "s/{{USER}}/$USER/g" -e "s|{{DIR}}|$DIR|g" "$svc" \
    | sudo tee /etc/systemd/system/$(basename "$svc") > /dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now bot-as-shell-bot
```

### Managing Services

```bash
# Check all services
for svc in bot-as-shell-bot security-server security-monitor \
           node-exporter bot-exporter process-exporter prometheus \
           alertmanager telegram-webhook; do
  echo "$svc: $(sudo systemctl is-active $svc)"
done

# View logs
sudo journalctl -u bot-as-shell-bot -n 50 --no-pager
```

## What it Costs

| Component | Cost |
|-----------|------|
| opencode CLI | Free (MIT license) |
| AI model (big-pickle) | Free, runs locally |
| Telegram Bot API | Free |
| Your server/electricity | Whatever you already pay |

No API keys or cloud subscriptions. The model runs on your own machine.

## Other Platforms

The same pattern works with Discord, Slack, Matrix, or any chat platform. Only the connection boilerplate changes:

```python
# Pseudocode — Discord version
@bot.event
async def on_message(message):
    prompt = build_prompt(message.author.id, message.content)
    result = await run_opencode(prompt)
    await message.channel.send(result)
```

The opencode bridge stays the same. Only the platform SDK and message routing change.

## License

MIT
