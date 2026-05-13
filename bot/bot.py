#!/usr/bin/env python3
# NOTE: The security/monitoring system (security/ directory) is an ISOLATED OPTIONAL
# component. Most people only want the Telegram + opencode bot. The security module
# (camera motion detection, alerting) is separate and can be deleted entirely if
# not needed. Keep it or remove it — the bot works fine either way.
import asyncio, subprocess, re, os, glob, time, json, shutil, ssl, base64, urllib.request
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

try:
    from faster_whisper import WhisperModel
    _HAS_WHISPER = True
except ImportError:
    _HAS_WHISPER = False

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

for _candidate in [
    os.path.join(_PROJECT_ROOT, '.env'),
    os.path.join(_SCRIPT_DIR, '.env'),
    os.path.join(_PROJECT_ROOT, 'security', '.env'),
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
STATUS_INTERVAL = 30
SECURITY_URL = os.environ.get('SECURITY_URL', 'https://localhost:8901')
SECURITY_PW = os.environ.get('SECURITY_PW', '')
_SEC_TLS_CTX = None
SEC_RUN_SH = os.environ.get('SEC_RUN_SH', os.path.join(_PROJECT_ROOT, 'security', 'run.sh'))

PERSONA = (
    "You are a witty but professional server admin assistant. "
    "You manage a Linux server. "
    "Be concise and direct. Execute the needed commands rather than just suggesting them. "
    "Keep responses under 4000 characters. "
    "Start every response with a brief one-line summary of what you did. "
    "Do NOT prefix your response with '>' or any model name. Reply with just your answer, nothing else."
)

MAX_HISTORY = 5
chat_history = {}
personas = {}
_camera_lock = asyncio.Lock()
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None and _HAS_WHISPER:
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model


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


async def _send(update_or_msg, text, edit_msg=None):
    text = text.strip() or "..."
    MAX = 4000
    chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)]
    if edit_msg:
        await edit_msg.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await update_or_msg.reply_text(chunk)
    else:
        for chunk in chunks:
            await update_or_msg.reply_text(chunk)


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
Just type what you want — the AI figures out the commands.
/cancel — abort a running request
/docs — this documentation
/persona [text] — view or change AI personality
/reset — clear conversation history
/logs — view today's activity logs
/pic — capture photo from camera
/activatesecurity — arm camera motion detection
/deactivatesecurity — disarm camera
/securitystatus — check if security is active

*Example commands*
"check disk usage"  "who is online"  "update packages"
"check system stats (cpu, ram, disk)"  "find large files"  "restart service X"

*Voice*
Send a voice message and the bot will transcribe it with Whisper and execute your command.
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
        "/logs — view today's activity logs\n"
        "/pic — capture photo from camera\n"
        "/activatesecurity — arm camera motion detection\n"
        "/deactivatesecurity — disarm camera\n"
        "/securitystatus — check if security is active\n\n"
        "If a request takes >3 min a status update will appear. "
        "Logs auto-delete after 24h."
    )


async def docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_chat.id):
        return
    await update.message.reply_text(DOCS_TEXT.strip(), parse_mode="Markdown")


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


async def _execute_opencode(chat_id, msg, update, context, edit_msg=None):
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
        out, err = await asyncio.wait_for(proc.communicate(), timeout=PROCESS_TIMEOUT)
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
                response = re.sub(r'^> .+$', '', response, flags=re.MULTILINE).strip()
                response = re.sub(r'(?m)^\s*\[.*?\]\s*$', '', response).strip()
                response = re.sub(r'\n{3,}', '\n\n', response).strip()
                log_event(chat_id, "RESPONSE", response)
                chat_history[chat_id].append((msg, response[:300]))
                await _send(update.message, response, edit_msg=edit_msg)
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
                await _send(update.message, msg_text, edit_msg=edit_msg)
                chat_history[chat_id].append((msg, msg_text[:300]))
                return

            if elapsed >= 180 and not notified:
                status = f"Still working on your request... ({int(elapsed)}s)"
                if edit_msg:
                    await edit_msg.edit_text(status)
                else:
                    await update.message.reply_text(status)
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    msg = update.message.text.strip()
    if not msg:
        return
    await _execute_opencode(chat_id, msg, update, context)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    status = await update.message.reply_text("Transcribing voice message...")

    file = await context.bot.get_file(voice.file_id)
    ogg_path = f"/tmp/voice_{chat_id}_{int(time.time())}.ogg"
    await file.download_to_drive(ogg_path)

    try:
        model = _get_whisper()
        if model is None:
            await status.edit_text("Whisper not installed — voice transcription unavailable.")
            return
        segments, _ = model.transcribe(ogg_path, beam_size=5)
        text = " ".join(s.text for s in segments).strip()
        if not text:
            await status.edit_text("Could not transcribe that audio.")
            return
        await status.edit_text(f"Transcribed: _{text}_")
        await _execute_opencode(chat_id, text, update, context, edit_msg=status)
    finally:
        try:
            os.remove(ogg_path)
        except OSError:
            pass


def _sec_request(path):
    global _SEC_TLS_CTX
    if _SEC_TLS_CTX is None:
        _SEC_TLS_CTX = ssl.create_default_context()
        _SEC_TLS_CTX.check_hostname = False
        _SEC_TLS_CTX.verify_mode = ssl.CERT_NONE
    auth = base64.b64encode(f":{SECURITY_PW}".encode()).decode()
    req = urllib.request.Request(
        f"{SECURITY_URL}{path}",
        headers={"Authorization": f"Basic {auth}"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5, context=_SEC_TLS_CTX)
        return resp.read().decode().strip()
    except urllib.error.HTTPError as e:
        return f"Error: {e.code} {e.read().decode().strip()}"
    except Exception as e:
        return f"Error: {e}"


async def activatesecurity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    msg = await update.message.reply_text("Activating security...")
    result = _sec_request("/activate")
    await msg.edit_text(f"Security activated.\n{result}")


async def deactivatesecurity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    msg = await update.message.reply_text("Deactivating security...")
    result = _sec_request("/deactivate")
    await msg.edit_text(f"Security deactivated.\n{result}")


async def pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    async with _camera_lock:
        msg = await update.message.reply_text("Capturing from camera...")
        tmp = f"/tmp/pic_{chat_id}_{int(time.time())}.jpg"
        SERVICE = "security-monitor.service"
        try:
            stop_proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", SERVICE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(stop_proc.wait(), timeout=15)

            for _ in range(10):
                chk = await asyncio.create_subprocess_exec(
                    "fuser", "/dev/video0",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await chk.communicate()
                if not stdout.strip():
                    break
                kill_proc = await asyncio.create_subprocess_exec(
                    "fuser", "-k", "/dev/video0",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await kill_proc.wait()
                await asyncio.sleep(0.5)

            last_err = None
            for attempt in range(3):
                ff = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-f", "v4l2", "-i", "/dev/video0",
                    "-vframes", "1", "-q:v", "2", "-update", "1", tmp,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                try:
                    await asyncio.wait_for(ff.wait(), timeout=10)
                except asyncio.TimeoutError:
                    ff.kill()
                    await ff.wait()
                    last_err = "timeout"
                    await asyncio.sleep(1)
                    continue

                if ff.returncode != 0:
                    last_err = f"ffmpeg exited code {ff.returncode}"
                    await asyncio.sleep(1)
                    continue

                if os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
                    with open(tmp, "rb") as f:
                        await update.message.reply_photo(photo=f)
                    await msg.delete()
                    return
                last_err = "file too small or missing"
                await asyncio.sleep(1)

            await msg.edit_text(f"Failed to capture image after 3 attempts ({last_err}).")
        except Exception as e:
            await msg.edit_text(f"Camera error: {e}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
            asyncio.create_task(_restart_security())


async def _restart_security():
    await asyncio.sleep(3)
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "start", "security-monitor.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=15)
    except Exception:
        pass


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


async def securitystatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    result = _sec_request("/status")
    try:
        state = json.loads(result)
        status = "ACTIVE" if state.get("active") else "INACTIVE"
        await update.message.reply_text(f"Security Status: {status}")
    except Exception:
        await update.message.reply_text(f"Security status:\n{result}")


def _build_app():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("docs", docs))
    app.add_handler(CommandHandler("persona", persona))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("pic", pic))
    app.add_handler(CommandHandler("activatesecurity", activatesecurity))
    app.add_handler(CommandHandler("deactivatesecurity", deactivatesecurity))
    app.add_handler(CommandHandler("securitystatus", securitystatus))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def main():
    print("Bot starting...")
    while True:
        try:
            app = _build_app()
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as exc:
            print(f"Polling error: {exc}. Restarting in 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
