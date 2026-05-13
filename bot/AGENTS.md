# bot-as-shell — Telegram Bot

Telegram bot (`bot/bot.py`) that relays messages to the opencode CLI for AI-powered server management.

## Architecture

```
Telegram → Bot API → bot.py → opencode CLI → Server shell
```

## IMPORTANT: Security module is isolated + optional

The `security/` directory (camera motion detection, alerting) is a separate module.
It is NOT required for the bot to work. Delete `security/` entirely if unwanted.
The bot gracefully handles the absence of security features — security commands
just return errors, voice transcription requires `faster-whisper` (optional dep).

## Key config (via .env)

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `ALLOWED_CHAT_ID` | `0` | Only authorized Telegram user ID |
| `WORKDIR` | cwd | opencode working directory |
| `OPENCODE_BIN` | `opencode` | Path to opencode CLI |
| `LOG_DIR` | `./logs` | Log storage location |
| `PROCESS_TIMEOUT` | `600` | Max command execution time (seconds) |
| `SECURITY_PW` | — | Password for security HTTP API |
| `SECURITY_URL` | `https://localhost:8901` | Security API endpoint |
| `CONFIRM_DESTRUCTIVE` | `true` | Ask confirmation before destructive operations |
| `INJECTION_DEFENSE` | `true` | Block prompt injection attempts |

## Features

- Natural language server commands via opencode AI
- Voice message transcription (Whisper, optional)
- USB camera capture (`/pic`)
- Motion detection security system integration
- Conversation memory (last 5 exchanges)
- Polling auto-restart on network errors
- Subprocess timeout (600s) with partial output capture
- Daily activity logs with 24h auto-cleanup
- Destructive action confirmation (keyword-based guard before opencode)
- Prompt injection defense (blocks system prompt override attempts)

## Bot commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with command list |
| `/docs` | Full documentation |
| `/persona [text]` | View or change AI personality |
| `/reset` | Clear conversation history |
| `/cancel` | Cancel running request |
| `/logs` | View today's activity log |
| `/pic` | Capture photo from USB camera |
| `/activatesecurity` | Arm camera motion detection |
| `/deactivatesecurity` | Disarm camera motion detection |
| `/securitystatus` | Check security system status |
