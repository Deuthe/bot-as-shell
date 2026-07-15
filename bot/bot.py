#!/usr/bin/env python3
# NOTE: The security/monitoring system (security/ directory) is an ISOLATED OPTIONAL
# component. Most people only want the Telegram + opencode bot. The security module
# (camera motion detection, alerting) is separate and can be deleted entirely if
# not needed. Keep it or remove it — the bot works either way.
import asyncio, subprocess, re, os, glob, time, json, shutil, ssl, base64, urllib.request, sqlite3
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
CONFIRM_DESTRUCTIVE = os.environ.get('CONFIRM_DESTRUCTIVE', 'true').lower() == 'true'
INJECTION_DEFENSE = os.environ.get('INJECTION_DEFENSE', 'true').lower() == 'true'

_SSH_MONITOR_STATE = os.path.join(_SCRIPT_DIR, '.ssh_monitor.state')
_SSH_MONITOR_LOG = os.environ.get('SSH_MONITOR_LOG', '/var/log/auth.log')
_SSH_EVENT_PATTERNS = [
    (re.compile(r'sshd\[\d+\]:\s*(?:Failed password for (?:invalid user )?|Invalid user )(\S+) from (\S+)'), 'ssh'),
    (re.compile(r'sudo[\[:].*authentication failure'), 'sudo'),
    (re.compile(r'(?<!sudo)su\[\d+\]:.*(?:FAILED SU|authentication failure)'), 'su'),
    (re.compile(r'(?:useradd\[\d+\]:\s*new user|new user:)'), 'new_user'),
]

PERSONA = (
    "You are a witty but professional server admin assistant. "
    "You manage a Linux server. "
    "Be concise and direct. Follow these guidelines:\n"
    "1. Think before coding — state assumptions, surface tradeoffs, ask if uncertain.\n"
    "2. Simplicity first — minimum code that solves the problem, nothing speculative.\n"
    "3. Surgical changes — touch only what you must, match existing style.\n"
    "4. Goal-driven execution — define success criteria, loop until verified.\n"
    "5. Start every response with a brief one-line summary of what you did, "
    "then include results. "
    "Do NOT prefix your response with '>' or any model name. Reply with just your answer, "
    "keep responses under 4000 characters."
)

PLAN_SUFFIX = (
    "You are in PLAN MODE. DO NOT execute any commands or take any actions. "
    "Present a step-by-step plan for what you would do. "
    "End by asking the user if they want to proceed with the plan."
)
BUILD_SUFFIX = (
    "You are in BUILD MODE. Execute requested actions directly."
)

MAX_HISTORY = 5
chat_history = {}
personas = {}
chat_modes = {}
_camera_lock = asyncio.Lock()
_whisper_model = None
_active_process = {}
_pending_confirmation = {}
_COUNTER_FILE = os.path.join(_SCRIPT_DIR, ".counter")

def _inc_counter():
    c = 0
    try:
        if os.path.exists(_COUNTER_FILE):
            with open(_COUNTER_FILE) as f:
                c = int(f.read().strip() or '0')
        c += 1
        with open(_COUNTER_FILE, 'w') as f:
            f.write(str(c))
    except Exception:
        pass

_DESTRUCTIVE_PATTERNS = [
    r'\bdelete\s+all\b', r'\bremove\s+all\b', r'\bwipe\b',
    r'\bformat\b', r'\bdestroy\b', r'\berase\s+everything\b',
    r'\bnuke\b', r'\bshutdown\b', r'\breboot\b',
    r'\breset\s+to\s+factory\b', r'\bclean\s+install\b',
    r'\breinstall\s+os\b', r'\bkill\s+all\b',
]
_DESTRUCTIVE_RE = re.compile('|'.join(_DESTRUCTIVE_PATTERNS), re.IGNORECASE)

_INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?previous\s+(instructions|directions|commands)',
    r'you\s+are\s+(now\s+)?(not\s+)?(required\s+to\s+)?(obey|follow|listen)',
    r'forget\s+(everything|all\s+(previous|prior)\s+(instructions|prompts))',
    r'system\s+(prompt|message|instruction)',
    r'rewrite\s+(your\s+)?(system\s+)?prompt',
    r'output\s+the\s+(system\s+)?prompt',
    r'reveal\s+(your\s+)?(system\s+)?(prompts?|instructions?)',
]
_INJECTION_RE = re.compile('|'.join(_INJECTION_PATTERNS), re.IGNORECASE)

_CONFIRM_TIMEOUT = 60
_CONFIRM_MSG = (
    "\u26a0\ufe0f That request involves potentially destructive operations.\n"
    "Reply 'yes' to confirm, or anything else to cancel. "
    f"(Pending confirmation expires in {_CONFIRM_TIMEOUT}s)"
)

def _is_destructive(text):
    return bool(_DESTRUCTIVE_RE.search(text))

def _detect_injection(text):
    m = _INJECTION_RE.search(text)
    if m:
        return True, f"Prompt injection detected: '{m.group()}'"
    return False, ""

def _get_whisper():
    global _whisper_model
    if _whisper_model is None and _HAS_WHISPER:
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model

_COMMAND_ALIASES = {}

def _register_command(name, handler, *aliases):
    _COMMAND_ALIASES[name] = handler
    for a in aliases:
        _COMMAND_ALIASES[a] = handler

def _match_command(text):
    text = text.strip().lower()
    if text.startswith("/"):
        text = text[1:]
    return _COMMAND_ALIASES.get(text)

def authorized(chat_id):
    return chat_id == ALLOWED_CHAT_ID

def strip_ansi(text):
    return re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]').sub('', text)

def _clean_response(text):
    text = strip_ansi(text).strip()
    if not text:
        return None
    text = re.sub(r'^> .+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'(?m)^\s*\[.*?\]\s*$', '', text)
    text = re.sub(r'^✱ .+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\$ .+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^→ .+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^─+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text or None

_DB_PATH = os.environ.get('OPENCODE_DB_PATH', os.path.expanduser('~/.local/share/opencode/opencode.db'))

def _query_opencode_db():
    if not os.path.isfile(_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM session ORDER BY time_created DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        cursor.execute("""
            SELECT p.data
            FROM part p
            JOIN message m ON p.message_id = m.id
            WHERE m.session_id = ?
              AND json_extract(p.data, '$.type') = 'text'
              AND json_extract(m.data, '$.role') = 'assistant'
              AND json_extract(p.data, '$.text') IS NOT NULL
              AND json_extract(p.data, '$.text') != ''
            ORDER BY p.rowid ASC
        """, (row[0],))
        rows = cursor.fetchall()
        conn.close()
        texts = [json.loads(r[0])["text"] for r in rows]
        return "\n".join(texts) if texts else None
    except Exception:
        return None

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

def build_prompt(chat_id, new_msg):
    persona = personas.get(chat_id, PERSONA)
    mode = chat_modes.get(chat_id, "build")
    if mode == "plan":
        persona = persona + " " + PLAN_SUFFIX
    lines = [f"[SYSTEM] {persona}"]
    lines.append("[SYSTEM] Below is the conversation so far. Respond to the latest [USER] message.")
    history = chat_history.get(chat_id, [])
    for user, bot in history[-MAX_HISTORY:]:
        lines.append("[USER] ---BEGIN USER MESSAGE---")
        lines.append(user)
        lines.append("---END USER MESSAGE---")
        lines.append(f"[ASSISTANT] {bot}")
    if not (history and history[-1][0] == new_msg):
        lines.append("[USER] ---BEGIN USER MESSAGE---")
        lines.append(new_msg)
        lines.append("---END USER MESSAGE---")
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
/plan — switch to plan mode (present plan, ask approval)
/build — switch to build mode (execute directly)
/mode — show current mode

*Example commands*
"check disk usage"  "who is online"  "update packages"
"check system stats (cpu, ram, disk)"  "find large files"  "restart service X"

*Voice*
Send a voice message and the bot will transcribe it with Whisper and execute your command.

*SSH Monitor*
The bot tails /var/log/auth.log and alerts you on failed SSH, sudo, and su attempts.
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
        "/securitystatus — check if security is active\n"
        "/plan — plan mode (present plan, ask approval)\n"
        "/build — build mode (execute directly)\n"
        "/mode — show current mode\n\n"
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
    notify_idx = 0
    notify_times = [180, 300, 420, 540]

    def _history_response(response):
        if chat_history.get(chat_id) and chat_history[chat_id][-1][0] == msg:
            chat_history[chat_id][-1] = (msg, response[:2000])

    try:
        while True:
            done, _ = await asyncio.wait([output_task], timeout=10)
            if output_task in done:
                try:
                    stdout, stderr = output_task.result()
                except asyncio.TimeoutError:
                    msg_text = "Request timed out. Check /logs for details."
                    log_event(chat_id, "TIMEOUT", "No partial output")
                    await _send(update.message, msg_text, edit_msg=edit_msg)
                    chat_history[chat_id].append((msg, msg_text[:300]))
                    return
                except Exception:
                    msg_text = "An unexpected error occurred while processing your request."
                    log_event(chat_id, "ERROR", str(output_task.exception())[:1000])
                    await _send(update.message, msg_text, edit_msg=edit_msg)
                    chat_history[chat_id].append((msg, msg_text[:300]))
                    return

                raw = stdout.decode().strip() or stderr.decode().strip()
                response = _query_opencode_db() or _clean_response(raw) or "Done."
                log_event(chat_id, "RESPONSE", response)
                _history_response(response)
                await _send(update.message, response, edit_msg=edit_msg)
                return

            elapsed = time.time() - start_time
            if elapsed >= PROCESS_TIMEOUT:
                proc.kill()
                try:
                    stdout, stderr = await output_task
                    raw = stdout.decode().strip() or stderr.decode().strip()
                    partial = _clean_response(raw) or ""
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

            if notify_idx < len(notify_times) and elapsed >= notify_times[notify_idx]:
                status = f"Still working on your request... ({int(elapsed)}s)"
                if edit_msg:
                    await edit_msg.edit_text(status)
                else:
                    await update.message.reply_text(status)
                notify_idx += 1

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
    _inc_counter()
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    msg = update.message.text.strip()
    if not msg:
        return

    if chat_id in _pending_confirmation:
        entry = _pending_confirmation.pop(chat_id)
        if time.time() - entry["time"] > _CONFIRM_TIMEOUT:
            await update.message.reply_text("Confirmation expired. Send your request again.")
            return
        if msg.lower() in ("yes", "y", "confirm", "do it", "proceed"):
            await _execute_opencode(chat_id, entry["msg"], update, context)
        else:
            await update.message.reply_text("Cancelled.")
        return

    if INJECTION_DEFENSE:
        injected, reason = _detect_injection(msg)
        if injected:
            log_event(chat_id, "INJECTION_BLOCKED", f"{reason}\nMessage: {msg}")
            await update.message.reply_text(f"\u26a0\ufe0f {reason}\nRequest blocked.")
            return

    if CONFIRM_DESTRUCTIVE and _is_destructive(msg):
        _pending_confirmation[chat_id] = {"msg": msg, "time": time.time()}
        await update.message.reply_text(_CONFIRM_MSG)
        return

    await _execute_opencode(chat_id, msg, update, context)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _inc_counter()
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

        handler = _match_command(text)
        if handler:
            await handler(update, context)
            return

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

_MONITOR_SERVICE = "security-monitor.service"

async def _ensure_monitor_running():
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "is-active", _MONITOR_SERVICE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )
    stdout, _ = await proc.communicate()
    if stdout.decode().strip() != "active":
        start = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "start", _MONITOR_SERVICE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await start.wait()

async def activatesecurity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    msg = await update.message.reply_text("Activating security...")
    await _ensure_monitor_running()
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

def _parse_ssh_events(lines):
    events = {}
    for line in lines:
        for pat, etype in _SSH_EVENT_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            if etype == 'ssh':
                user, ip = m.groups()
                events.setdefault('ssh', {}).setdefault(ip, set()).add(user)
            else:
                events[etype] = events.get(etype, 0) + 1
            break
    return events

async def _send_ssh_alert(app, events):
    lines = ['Suspicious Activity Detected']
    if events.get('ssh'):
        lines.append('')
        lines.append('-- SSH --')
        for ip, users in sorted(events['ssh'].items()):
            ulist = ', '.join(sorted(users)[:5])
            extra = f' ... +{len(users) - 5}' if len(users) > 5 else ''
            count = 'attempt' if len(users) == 1 else 'attempts'
            lines.append(f'* {len(users)} {count} from `{ip}`')
            lines.append(f'  as {ulist}{extra}')
    for key, label in [('sudo', 'sudo'), ('su', 'su'), ('new_user', 'new user')]:
        c = events.get(key, 0)
        if c:
            lines.append('')
            lines.append(f'-- {label} --')
            lines.append(f'* {c} failure{"s" if c != 1 else ""}')
    try:
        await app.bot.send_message(
            chat_id=ALLOWED_CHAT_ID, text='\n'.join(lines), parse_mode='Markdown'
        )
    except Exception as exc:
        log_event(0, 'SSH_MONITOR_ERROR', f'send failed: {exc}')

async def _ssh_monitor(app):
    state = {'inode': 0, 'position': 0}
    if os.path.exists(_SSH_MONITOR_STATE):
        try:
            with open(_SSH_MONITOR_STATE) as f:
                state = json.load(f)
        except Exception:
            pass

    while True:
        try:
            if not os.path.isfile(_SSH_MONITOR_LOG):
                await asyncio.sleep(10)
                continue

            st = os.stat(_SSH_MONITOR_LOG)
            ino, sz = st.st_ino, st.st_size

            if ino != state.get('inode', 0) or sz < state.get('position', 0):
                state.update(inode=ino, position=0)

            if sz > state.get('position', 0):
                with open(_SSH_MONITOR_LOG) as f:
                    f.seek(state['position'])
                    new_lines = f.readlines()
                events = _parse_ssh_events(new_lines)
                if events:
                    await _send_ssh_alert(app, events)
                state['position'] = sz

            with open(_SSH_MONITOR_STATE, 'w') as f:
                json.dump(state, f)
        except Exception as exc:
            log_event(0, 'SSH_MONITOR_ERROR', str(exc)[:500])
        await asyncio.sleep(10)

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

async def plan_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    chat_modes[chat_id] = "plan"
    await update.message.reply_text(
        "Plan Mode enabled.\n"
        "I will present a plan and ask for your approval before executing anything."
    )

async def build_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    chat_modes[chat_id] = "build"
    await update.message.reply_text(
        "Build Mode enabled.\n"
        "I will execute requested actions directly."
    )

async def show_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not authorized(chat_id):
        return
    mode = chat_modes.get(chat_id, "build")
    label = "Plan" if mode == "plan" else "Build"
    await update.message.reply_text(
        f"Current mode: {label}\n"
        f"Switch with /plan or /build."
    )

def _register_commands():
    _register_command("start", start)
    _register_command("docs", docs)
    _register_command("persona", persona)
    _register_command("reset", reset)
    _register_command("logs", logs_cmd)
    _register_command("pic", pic)
    _register_command("cancel", cancel)
    _register_command("plan", plan_mode)
    _register_command("build", build_mode)
    _register_command("mode", show_mode)
    _register_command("activatesecurity", activatesecurity, "activate security", "arm security")
    _register_command("deactivatesecurity", deactivatesecurity, "deactivate security", "disarm security")
    _register_command("securitystatus", securitystatus, "security status")

_register_commands()

def _build_app():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("docs", docs))
    app.add_handler(CommandHandler("persona", persona))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("pic", pic))
    app.add_handler(CommandHandler("activatesecurity", activatesecurity))
    app.add_handler(CommandHandler("deactivatesecurity", deactivatesecurity))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("securitystatus", securitystatus))
    app.add_handler(CommandHandler("plan", plan_mode))
    app.add_handler(CommandHandler("build", build_mode))
    app.add_handler(CommandHandler("mode", show_mode))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

async def _run_with_monitor(app):
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        monitor = asyncio.create_task(_ssh_monitor(app))
        try:
            await asyncio.Event().wait()
        finally:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

def main():
    print("Bot starting...")
    app = _build_app()
    asyncio.run(_run_with_monitor(app))

if __name__ == "__main__":
    main()
