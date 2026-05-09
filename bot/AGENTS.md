# bot-as-shell — Telegram Bot

Telegram bot (`bot/bot.py`) that relays messages to the opencode CLI for AI-powered server management.

## Key config (via .env)

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `ALLOWED_CHAT_ID` | `0` | Only authorized Telegram user ID |
| `WORKDIR` | cwd | opencode working directory |
| `OPENCODE_BIN` | `opencode` | Path to opencode CLI |
| `LOG_DIR` | `./logs` | Log storage location |
| `PROCESS_TIMEOUT` | `600` | Max command execution time (seconds) |
