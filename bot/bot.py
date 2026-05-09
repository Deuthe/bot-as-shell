#!/usr/bin/env python3
import asyncio, subprocess, re, os, glob, time, json, shutil
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

for _candidate in [
    os.path.join(_PROJECT_ROOT, '.env'),
    os.path.join(_SCRIPT_DIR, '.env'),
]:
    if os.path.exists(_candidate):
        with open(_candidate) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#'):
                    _k, _, _v = _line.partition('=')
                    os.environ.setdefault(_k.strip(), _v.strip())
        break

_TZ = "UTC"
try:
    _tz_proc = subprocess.run(
        ["timedatectl", "show", "--property=Timezone", "--value"],
        capture_output=True, text=True, timeout=5
    )
    if _tz_proc.returncode == 0:
        _TZ = _tz_proc.stdout.strip()
except Exception:
    pass
os.environ.setdefault("TZ", _TZ)
try:
    time.tzset()
except AttributeError:
    pass

TOKEN = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN') or ""
ALLOWED_CHAT_ID = int(os.environ.get('ALLOWED_CHAT_ID', '0'))
WORKDIR = os.environ.get('WORKDIR', os.getcwd())
_OPENCODE_ENV = os.environ.get('OPENCODE_BIN') or shutil.which('opencode') or ''
OPENCODE_BIN = _OPENCODE_ENV if os.path.isfile(_OPENCODE_ENV) else 'opencode'
BOT_ENV = {**os.environ, "TERM": "dumb"}
LOG_DIR = os.environ.get('LOG_DIR', os.path.join(_SCRIPT_DIR, 'logs'))
PROCESS_TIMEOUT = int(os.environ.get('PROCESS_TIMEOUT', '600'))

PERSONA = (
    "You are a witty but professional server admin assistant. "
    "You manage a Linux server. "
    "Be concise and direct. Execute the needed commands rather than just suggesting them. "
    "Keep responses under 4000 characters. "
    "Start every response with a brief one-line summary of what you did."
)

MAX_HISTORY = 5
chat_history = {}
personas = {}
_active_process = {}


def authorized(chat_id):
    return chat_id == ALLOWED_CHAT_ID


def strip_ansi(text):
    return re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]').sub('', text)


def build_prompt(chat_id, new_msg):
    persona = personas.get(chat_id, PERSONA)
    lines = [f"[SYSTEM] {persona}"]
    lines.append("[SYSTEM] Below is the conversation so far. Respond to the latest [USER] message.")
    history = chat_history.get(chat_id, [])
    for user, bot in history[-MAX_HISTORY:]:
        lines.append(f"[USER] {user}")
        lines.append(f"[ASSISTANT] {bot}")
    lines.append(f"[USER] {new_msg}")
    return "\n".join(lines)


def log_event(chat_id, event_type, content):
    os.makedirs(LOG_DIR, exist_ok=True)
    now = datetime.now()
    logfile = os.path.join(LOG_DIR, now.strftime("%Y-%m-%d") + ".log")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{chat_id}] [{event_type}]\n{content}\n\n"
    with open(logfile, "a") as f:
        f.write(entry)


def cleanup_logs():
    now = time.time()
    for f in glob.glob(os.path.join(LOG_DIR, "*.log")):
        try:
            if os.path.getmtime(f) < now - 86400:
                os.remove(f)
        except OSError:
            pass


DOCS_TEXT = """
*bot-as-shell — Full Documentation*

*Architecture*
You -> Telegram -> Bot (Python) -> opencode (AI) -> Server shell
                                                      ↓
                                              You get the response

The bot runs as a systemd service on the server. It restarts automatically on boot and crash.

*Memory & Sessions*
Each message is a *fresh opencode session* — the AI sees your current message plus the last 5 exchanges for context. There is no persistent memory between restarts.

*Personality*
A system prompt gives the AI a consistent personality. Check with /persona, change with /persona <text>.

*Commands*
Just type what you want — the AI figures out the commands. Each message is independent.
/cancel — abort a running request
/docs — this documentation
/persona [text] — view or change AI personality
/reset — clear conversation history
/logs — view today's activity logs

*Example commands*
"check disk usage"  "who is online"  "update packages"
"check system stats (cpu, ram, disk)"  "find large files"  "restart service X"
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        await update.message.reply_text("Unauthorized.")
        return
    chat_history.setdefault(chat_id, [])
    personas.setdefault(chat_id, PERSONA)
    await update.message.reply_text(
        "bot-as-shell — online.\n"
        "Send any message to manage the server.\n\n"
        "/docs — full documentation\n"
        "/persona — view or change my personality\n"
        "/reset — clear conversation history\n"
        "/cancel — cancel a running request\n"
        "/logs — view today's activity logs\n\n"
        "If a request takes >3 min a status update will appear. "
        "Logs auto-delete after 24h."
    )


async def docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_chat.id):
        return
    await update.message.reply_text(DOCS_TEXT.strip())


async def persona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        new_persona = parts[1]
        personas[chat_id] = new_persona
        await update.message.reply_text(f"Personality updated. Now I am:\n\n{new_persona}")
    else:
        current = personas.get(chat_id, PERSONA)
        await update.message.reply_text(f"Current personality:\n\n{current}\n\nTo change: /persona <new instructions>")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    chat_history[chat_id] = []
    await update.message.reply_text("Conversation history cleared.")


async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    logfile = os.path.join(LOG_DIR, today + ".log")
    if not os.path.exists(logfile):
        await update.message.reply_text("No logs for today.")
        return
    with open(logfile) as f:
        content = f.read().strip()
    if not content:
        await update.message.reply_text("Log file is empty.")
        return
    header = f"Logs for {today}:\n\n"
    remaining = header + content
    for i in range(0, len(remaining), 4000):
        await update.message.reply_text(remaining[i:i+4000])


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    proc = _active_process.pop(chat_id, None)
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
        await update.message.reply_text("Cancelled.")
    else:
        await update.message.reply_text("Nothing to cancel.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return

    msg = update.message.text.strip()
    if not msg:
        return

    prev = _active_process.pop(chat_id, None)
    if prev:
        try:
            prev.kill()
        except Exception:
            pass

    chat_history.setdefault(chat_id, [])
    personas.setdefault(chat_id, PERSONA)
    cleanup_logs()
    log_event(chat_id, "REQUEST", msg)

    prompt = build_prompt(chat_id, msg)

    proc = await asyncio.create_subprocess_exec(
        OPENCODE_BIN, "run", "--dangerously-skip-permissions", prompt,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKDIR,
        env=BOT_ENV
    )

    async def get_output():
        out, err = await proc.communicate()
        return out, err

    output_task = asyncio.create_task(get_output())
    _active_process[chat_id] = proc
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    start_time = time.time()
    notified = False

    try:
        while True:
            done, _ = await asyncio.wait([output_task], timeout=10)
            if output_task in done:
                stdout, stderr = output_task.result()
                response = (stdout.decode().strip() or stderr.decode().strip() or "No response.")
                response = strip_ansi(response).strip()
                log_event(chat_id, "RESPONSE", response[:1000])
                chat_history[chat_id].append((msg, response[:300]))
                MAX = 4000
                if len(response) > MAX:
                    for i in range(0, len(response), MAX):
                        await update.message.reply_text(response[i:i+MAX])
                else:
                    await update.message.reply_text(response)
                return

            elapsed = time.time() - start_time
            if elapsed >= PROCESS_TIMEOUT:
                proc.kill()
                try:
                    stdout, stderr = await output_task
                    partial = (stdout.decode().strip() or stderr.decode().strip() or "")
                    partial = strip_ansi(partial).strip()
                except Exception:
                    partial = ""
                msg_text = "Request timed out. Check /logs for details."
                if partial:
                    log_event(chat_id, "TIMEOUT_PARTIAL", partial[:1000])
                    msg_text += f"\n\nPartial output:\n{partial[:500]}"
                else:
                    log_event(chat_id, "TIMEOUT", "No partial output")
                await update.message.reply_text(msg_text)
                chat_history[chat_id].append((msg, msg_text[:300]))
                return

            if elapsed >= 180 and not notified:
                await update.message.reply_text(f"Still working on your request... ({int(elapsed)}s)")
                notified = True

            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    finally:
        if proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass
        _active_process.pop(chat_id, None)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("docs", docs))
    app.add_handler(CommandHandler("persona", persona))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
