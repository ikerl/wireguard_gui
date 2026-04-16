import os

# Base directory
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')

# Database
SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(INSTANCE_DIR, "wireguard.db")}'
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Secret key for sessions
SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(32).hex())

# WireGuard defaults
DEFAULT_WG_INTERFACE = 'wg0'
DEFAULT_WG_TUNNEL_NAME = 'wg0'
DEFAULT_WG_PORT = 51820
DEFAULT_WG_NETWORK = '10.0.0.0/24'
DEFAULT_WG_DNS = '1.1.1.1'
DEFAULT_REFRESH_INTERVAL = 30
DEFAULT_AUTO_RESTART_TUNNEL = True

# Telegram defaults
TELEGRAM_MESSAGE_TEMPLATE = """🔔 *Nuevo Acceso WireGuard*

👤 *Cliente:* {name}
🌐 *IP:* `{ip}`
📅 *Hora:* {timestamp}

✅ Sesión establecida"""

# Upload and config paths
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
CONFIG_FOLDER = os.path.join(BASE_DIR, 'configs')
