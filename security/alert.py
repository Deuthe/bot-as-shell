import subprocess, os, json, urllib.request, smtplib, email.mime.text, email.mime.multipart, email.mime.image, datetime, time, re
from config import CONFIG

_SANITIZE_PATTERNS = [
    (re.compile(r'(bot|token|key|secret|password|passwd|api[_-]?key)[=:]\s*\S+', re.IGNORECASE), r'\1=***'),
    (re.compile(r'\b\d{9,}:[A-Za-z0-9_-]{20,}\b'), '***TOKEN***'),
]

def _sanitize(msg):
    for pat, repl in _SANITIZE_PATTERNS:
        msg = pat.sub(repl, str(msg))
    return msg

def send_alert(message, image_path=None, video_path=None):
    sent = False
    if CONFIG.get('telegram_token') and CONFIG.get('telegram_chat_id'):
        sent = send_telegram(message, image_path, video_path) or sent
    if CONFIG['ntfy_url']:
        sent = send_ntfy(message, image_path) or sent
    if CONFIG['alert_email']:
        sent = send_email(message, image_path) or sent
    if CONFIG['alert_url']:
        sent = send_webhook(message, image_path) or sent
    if not sent:
        print(f"[ALERT] {message}")
        log_alert(message, image_path)
    return sent


def send_telegram(message, image_path=None, video_path=None):
    token = CONFIG['telegram_token']
    chat_id = CONFIG['telegram_chat_id']

    def _upload(file_path, method):
        import uuid
        boundary = str(uuid.uuid4()).replace('-', '')
        ext = os.path.splitext(file_path)[1].lower()
        is_video = method == 'sendVideo'
        content_type = 'video/mp4' if is_video else 'image/jpeg'

        with open(file_path, 'rb') as f:
            file_data = f.read()

        part_name = 'video' if is_video else 'photo'
        params = [
            f'--{boundary}',
            f'Content-Disposition: form-data; name="chat_id"',
            '',
            str(chat_id),
            f'--{boundary}',
            f'Content-Disposition: form-data; name="caption"',
            '',
            message,
            f'--{boundary}',
            f'Content-Disposition: form-data; name="{part_name}"; filename="{os.path.basename(file_path)}"',
            f'Content-Type: {content_type}',
            '',
        ]
        head = '\r\n'.join(params) + '\r\n'
        tail = f'\r\n--{boundary}--\r\n'
        data = head.encode('utf-8') + file_data + tail.encode('utf-8')
        url = f'https://api.telegram.org/bot{token}/{method}'
        req = urllib.request.Request(url, data=data,
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
            method='POST')
        urllib.request.urlopen(req, timeout=30)

    for attempt in range(2):
        try:
            if video_path and os.path.exists(video_path):
                _upload(video_path, 'sendVideo')
                return True
            if image_path and os.path.exists(image_path):
                _upload(image_path, 'sendPhoto')
                return True
            url = f'https://api.telegram.org/bot{token}/sendMessage'
            payload = json.dumps({'chat_id': chat_id, 'text': message}).encode()
            req = urllib.request.Request(url, data=payload,
                headers={'Content-Type': 'application/json'}, method='POST')
            urllib.request.urlopen(req, timeout=15)
            return True
        except Exception as e:
            if attempt == 0:
                print(f"telegram retry: {_sanitize(e)}")
                time.sleep(2)
            else:
                print(f"telegram error: {_sanitize(e)}")
    return False

def send_ntfy(message, image_path=None):
    try:
        topic = CONFIG['ntfy_topic']
        url = f"{CONFIG['ntfy_url'].rstrip('/')}/{topic}"

        if image_path and os.path.exists(image_path):
            import uuid
            boundary = str(uuid.uuid4()).replace('-', '')
            head = []
            head.append(f'--{boundary}')
            head.append('Content-Disposition: form-data; name="message"')
            head.append('')
            head.append(message)
            head.append(f'--{boundary}')
            head.append(f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(image_path)}"')
            head.append('Content-Type: image/jpeg')
            head.append('')
            raw = '\r\n'.join(head) + '\r\n'
            with open(image_path, 'rb') as f:
                img_data = f.read()
            tail = f'\r\n--{boundary}--\r\n'
            data = raw.encode('utf-8') + img_data + tail.encode('utf-8')
            req = urllib.request.Request(url, data=data,
                headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                method='POST')
        else:
            req = urllib.request.Request(url, data=message.encode('utf-8'),
                headers={'Content-Type': 'text/plain'}, method='POST')

        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"ntfy error: {_sanitize(e)}")
        return False

def send_email(message, image_path=None):
    try:
        if not all([CONFIG['alert_email'], CONFIG['smtp_server'], CONFIG['smtp_user'], CONFIG['smtp_pass']]):
            return False

        msg = email.mime.multipart.MIMEMultipart()
        msg['Subject'] = f"[Security Alert] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        msg['From'] = CONFIG['alert_email_from'] or CONFIG['smtp_user']
        msg['To'] = CONFIG['alert_email']
        msg.attach(email.mime.text.MIMEText(message, 'plain'))

        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as f:
                img = email.mime.image.MIMEImage(f.read(), name=os.path.basename(image_path))
                msg.attach(img)

        with smtplib.SMTP(CONFIG['smtp_server'], CONFIG['smtp_port']) as server:
            server.starttls()
            server.login(CONFIG['smtp_user'], CONFIG['smtp_pass'])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"email error: {_sanitize(e)}")
        return False

def send_webhook(message, image_path=None):
    try:
        payload = json.dumps({'message': message, 'timestamp': str(datetime.datetime.now())}).encode()
        req = urllib.request.Request(CONFIG['alert_url'], data=payload,
            headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"webhook error: {_sanitize(e)}")
        return False

def log_alert(message, image_path=None):
    log_dir = os.path.dirname(os.path.abspath(__file__)) + '/captures'
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'alerts.log')
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(log_file, 'a') as f:
        f.write(f"[{ts}] {message}\n")
        if image_path:
            f.write(f"  Image: {image_path}\n")
