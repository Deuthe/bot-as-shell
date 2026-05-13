import os


def load_dotenv(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, _, val = line.partition('=')
                    os.environ.setdefault(key.strip(), val.strip())


load_dotenv()

CONFIG = {
    'camera_id': 0,
    'motion_threshold': 9000,
    'min_area': 500,
    'cooldown_seconds': 30,
    'capture_dir': os.path.dirname(os.path.abspath(__file__)) + '/captures',
    'alert_url': None,
    'alert_email': None,
    'alert_email_from': None,
    'smtp_server': None,
    'smtp_port': 587,
    'smtp_user': None,
    'smtp_pass': None,
    'ntfy_topic': 'nuc-security-' + os.environ.get('USER', 'home'),
    'ntfy_url': 'https://ntfy.sh',
    'telegram_token': os.environ.get('TELEGRAM_BOT_TOKEN'),
    'telegram_chat_id': os.environ.get('TELEGRAM_CHAT_ID'),
}
