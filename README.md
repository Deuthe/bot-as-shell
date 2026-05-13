# bot-as-shell

**Manage your server over Telegram using opencode.**

Send a Telegram message, the AI figures out what to do, runs the commands on your server, and replies with the result. The model runs locally through opencode, but you need internet for Telegram to work.

## How it works

```
You -- Telegram --> bot.py -- opencode CLI -- AI model -- Linux shell
                         ↕                      ↕
                    python-telegram-bot      big-pickle (local)
```

The AI runs on your own machine via opencode, free and private. You just need internet so Telegram can deliver the messages.

## Prerequisites

- Python 3.11+
- [opencode CLI](https://opencode.ai), install with `npm install -g @opencode/cli`
- A Telegram bot token, get one free at [@BotFather](https://t.me/BotFather)

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

**Bot token:** Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, pick a name and username. It replies with a token that looks like `123456:ABCdef...`.

**Your chat ID:** There are a few ways:
- Message [@userinfobot](https://t.me/userinfobot), it replies with your numeric ID
- Or message [@RawDataBot](https://t.me/RawDataBot), look for `message.from.id`
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

To keep it running 24/7 on a server:

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
| AI model (big-pickle) | Free, runs locally |
| Telegram Bot API | Free |
| Your server/electricity | Whatever you already pay |

No API keys or cloud subscriptions. The model runs on your own machine.

## Safety

The bot has two built-in safety features, both enabled by default (toggle via `.env`):

**Destructive action confirmation** — If your message contains keywords like "delete all", "wipe", "format", "shutdown", etc., the bot asks for confirmation before executing. Reply `yes` to proceed. Pending confirmations expire after 60 seconds.

**Prompt injection defense** — Messages that attempt to override the bot's system prompt (e.g. "ignore previous instructions") are blocked and logged. The AI cannot be tricked into abandoning its persona.

Both features operate transparently — you won't notice them during normal use.

## Optional: Camera Security System

This repo includes a `security/` directory with a camera motion detection system
(OpenCV, HTTP command server, multi-channel alerting). It is **isolated and optional**.
The bot works perfectly without it — security commands like `/activatesecurity` just
return errors if the security server isn't running.

Delete the `security/` directory if you don't need it. No code changes required.

## Commands

| What you send | What happens |
|---|---|
| *anything* | opencode interprets it, runs shell commands, replies |
| `/docs` | Full documentation |
| `/persona [text]` | View or change the AI's personality |
| `/reset` | Clear conversation history |
| `/cancel` | Abort a running request |
| `/logs` | View today's activity |
| `/pic` | Capture photo from USB camera (requires security module) |
| `/activatesecurity` | Arm camera motion detection (requires security module) |
| `/deactivatesecurity` | Disarm camera (requires security module) |
| `/securitystatus` | Check if security is active (requires security module) |

## Other platforms

The same pattern works with any chat platform. This one uses Telegram because it's simple, free, and has a good bot API.

A Discord version would follow the same pattern:

```python
# Pseudocode, Discord version would look like:
@bot.event
async def on_message(message):
    prompt = build_prompt(message.author.id, message.content)
    result = await run_opencode(prompt)
    await message.channel.send(result)
```

Only the connection boilerplate (`discord.py`, Slack SDK, Matrix, etc.) and message routing change. The opencode bridge stays the same.

## License

MIT
