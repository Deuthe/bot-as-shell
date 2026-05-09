# bot-as-shell

**Natural language server management over Telegram via opencode CLI.**

Send a Telegram message — the AI interprets it, runs the commands on your server, and replies with the result. Everything runs locally using opencode's built-in models. No API keys, no cloud costs, no data leaving your machine.

## How it works

```
You -- Telegram --> bot.py -- opencode CLI -- AI model -- Linux shell
                         ↕                      ↕
                   python-telegram-bot      Llama / big-pickle (local)
```

The AI model runs on your own machine via opencode. It's free, offline, and private.

## Prerequisites

- Python 3.11+
- [opencode CLI](https://opencode.ai) — `npm install -g @opencode/cli`
- A Telegram bot token — create one free at [@BotFather](https://t.me/BotFather)

## Setup

```bash
# 1. Clone
git clone <repo-url> && cd bot-as-shell

# 2. Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -r bot/requirements.txt

# 3. Configure your Telegram bot token
cp .env.example .env
chmod 600 .env
```

### Getting your Telegram credentials

**Bot token:** Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, pick a name and username — it replies with a token that looks like `123456:ABCdef...`.

**Your chat ID:** There are a few ways:
- Message [@userinfobot](https://t.me/userinfobot) — it replies with your numeric ID
- Or message [@RawDataBot](https://t.me/RawDataBot) — look for `message.from.id`
- Or after running the bot once, the chat ID appears in the logs under `bot/logs/`

Edit `.env` with both values:

```env
TELEGRAM_BOT_TOKEN=123456:ABCdefGHIjklmNOPqrSTUvWXyz
ALLOWED_CHAT_ID=123456789
```

```bash
# 4. Run
python3 bot/bot.py
```

Message your bot on Telegram. That's it.

## Production (systemd)

For 24/7 operation on a server:

```bash
# Edit the service template with your user and project path
sudo cp systemd/bot-as-shell-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-as-shell-bot
```

## What it costs

| Component | Cost |
|-----------|------|
| opencode CLI | Free (MIT license) |
| AI model (big-pickle / Llama) | Free, runs locally |
| Telegram Bot API | Free |
| Your server/electricity | Whatever you already pay |

Zero API keys, zero cloud subscriptions. The model runs on your hardware.

## Commands

| What you send | What happens |
|---|---|
| *anything* | opencode interprets it, runs shell commands, replies |
| `/docs` | Full documentation |
| `/persona [text]` | View or change the AI's personality |
| `/reset` | Clear conversation history |
| `/cancel` | Abort a running request |
| `/logs` | View today's activity |

## Other platforms

The core concept — relay messages to opencode and return the output — works with any chat platform. The current implementation uses Telegram because it's simple, free, and has the best bot API.

A Discord version would follow the same pattern:

```python
# Pseudocode — Discord version would look like:
@bot.event
async def on_message(message):
    prompt = build_prompt(message.author.id, message.content)
    result = await run_opencode(prompt)
    await message.channel.send(result)
```

The platform-specific parts are just the connection boilerplate (`discord.py`, Slack SDK, Matrix, etc.) and the message routing — the opencode bridge stays identical.

## License

MIT
