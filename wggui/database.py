from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import hashlib
import secrets
import re
from pathlib import Path

ALLOWED_WIREGUARD_DIR = '/etc/wireguard'
VALID_PEER_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\s]{1,64}$')
VALID_INTERFACE_PATTERN = re.compile(r'^[a-zA-Z0-9_]{1,15}$')
VALID_IPV4_CIDR_PATTERN = re.compile(r'^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$')
VALID_PORT_PATTERN = re.compile(r'^(102[4-9]|10[3-9]\d|1[1-9]\d{2}|[2-9]\d{3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-2]\d{2}|653[0-5]\d|6536[0-5]|6553[0-5])$')
VALID_POSITIVE_INT_PATTERN = re.compile(r'^[1-9]\d*$')
VALID_HOSTNAME_OR_IP_PATTERN = re.compile(r'^[a-zA-Z0-9.\-]{1,253}$')

db = SQLAlchemy()


def generate_salt():
    """Generate a unique salt for password hashing."""
    return secrets.token_hex(32)


def hash_password(password, salt):
    """Hash password using SHA256 with salt."""
    return hashlib.sha256(f"{password}{salt}".encode()).hexdigest()


class User(db.Model, UserMixin):
    """User model for panel authentication."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(64), nullable=False)
    salt = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        """Set password with automatic salt generation."""
        self.salt = generate_salt()
        self.password_hash = hash_password(password, self.salt)

    def check_password(self, password):
        """Check if password matches."""
        return self.password_hash == hash_password(password, self.salt)


class Peer(db.Model):
    """WireGuard peer model."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    public_key = db.Column(db.String(44), unique=True, nullable=False)
    pre_shared_key = db.Column(db.String(44), nullable=True)
    assigned_ip = db.Column(db.String(15), unique=True, nullable=False)
    allowed_ips = db.Column(db.String(255), nullable=True)  # Comma-separated CIDRs for AllowedIPs
    status = db.Column(db.String(20), default='enabled')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_handshake = db.Column(db.DateTime, nullable=True)
    last_connection = db.Column(db.DateTime, nullable=True)  # Mantiene la última fecha de conexión
    last_disconnection = db.Column(db.DateTime, nullable=True)  # Mantiene la fecha de última desconexión
    connection_notified = db.Column(db.Boolean, default=False)
    endpoint_ip = db.Column(db.String(50), nullable=True)  # IP:port pública del peer

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'name': self.name,
            'public_key': self.public_key,
            'pre_shared_key': self.pre_shared_key,
            'assigned_ip': self.assigned_ip,
            'allowed_ips': self.allowed_ips,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_handshake': self.last_handshake.isoformat() if self.last_handshake else None,
        }


class ConnectionHistory(db.Model):
    """Connection history log."""
    id = db.Column(db.Integer, primary_key=True)
    peer_id = db.Column(db.Integer, db.ForeignKey('peer.id'), nullable=False)
    event_type = db.Column(db.String(20), nullable=False)  # 'connection' or 'disconnection'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    details = db.Column(db.Text, nullable=True)
    endpoint_ip = db.Column(db.String(50), nullable=True)  # IP pública del peer en el evento

    peer = db.relationship('Peer', backref='connection_history')


class Settings(db.Model):
    """Application settings stored in database."""
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=True)


def get_setting(key, default=None):
    """Get a setting value from database."""
    setting = Settings.query.get(key)
    return setting.value if setting else default


def set_setting(key, value):
    """Set a setting value in database."""
    setting = Settings.query.get(key)
    if setting:
        setting.value = str(value)
    else:
        setting = Settings(key=key, value=str(value))
        db.session.add(setting)
    db.session.commit()


def init_default_settings():
    """Initialize default settings if they don't exist."""
    defaults = {
        'wg_interface': 'wg0',
        'wg_tunnel_name': 'wg0',
        'wg_listen_port': '51820',
        'wg_network': '10.0.0.0/24',
        'wg_allowed_ips': '',  # Default AllowedIPs for clients (empty = use wg_network)
        'wg_dns': '',
        'refresh_interval': '30',
        'disconnect_timeout': '600',
        'auto_restart_tunnel': 'True',
        'server_config_path': '/etc/wireguard/wg0.conf',
        'timezone': 'Europe/Madrid',
        'telegram_enabled': 'False',
        'telegram_bot_token': '',
        'telegram_chat_id': '',
        'telegram_expire_seconds': '300',
        'telegram_message_template': '''🔔 NEW WireGuard ACCESS

🖥️ Client: {name}
📡 Assigned IP: {ip}
🌐 Public IP: {endpoint_ip}
🕐 Time: {timestamp}

✅ Session established''',
    }

    for key, value in defaults.items():
        if not get_setting(key):
            set_setting(key, value)


def validate_config_path(path):
    """Validate that a config path is within /etc/wireguard/ directory.

    Uses resolve() to normalize path and follow symlinks, preventing
    path traversal attacks via ../../../ or other techniques.

    Args:
        path: The file path to validate

    Returns:
        The resolved absolute path

    Raises:
        ValueError: If path is outside /etc/wireguard/ or is invalid
    """
    if not path:
        raise ValueError("Config path cannot be empty")

    try:
        resolved_path = Path(path).resolve()
    except (OSError, ValueError) as e:
        raise ValueError(f"Invalid path: {path}")

    allowed_dir = Path(ALLOWED_WIREGUARD_DIR).resolve()

    if not str(resolved_path).startswith(str(allowed_dir) + '/'):
        raise ValueError(
            f"Configuration paths must be within {ALLOWED_WIREGUARD_DIR}/ directory. "
            f"Got: {path}"
        )

    return resolved_path


def validate_peer_name(name):
    """Validate and sanitize a peer name.

    Args:
        name: The peer name to validate

    Returns:
        The sanitized peer name

    Raises:
        ValueError: If name is invalid
    """
    if not name:
        raise ValueError("Peer name cannot be empty")

    sanitized = name.strip()

    if len(sanitized) > 64:
        raise ValueError("Peer name must be 64 characters or less")

    if not VALID_PEER_NAME_PATTERN.match(sanitized):
        raise ValueError(
            "Peer name can only contain letters, numbers, spaces, underscores, and dashes"
        )

    return sanitized


def validate_interface(value):
    """Validate WireGuard interface name (e.g., wg0)."""
    if not value:
        raise ValueError("Interface name cannot be empty")
    value = value.strip()
    if not VALID_INTERFACE_PATTERN.match(value):
        raise ValueError(
            "Interface name must be 1-15 characters (letters, numbers, underscores only)"
        )
    return value


def validate_tunnel_name(value):
    """Validate WireGuard tunnel name (e.g., wg0)."""
    if not value:
        raise ValueError("Tunnel name cannot be empty")
    value = value.strip()
    if not VALID_INTERFACE_PATTERN.match(value):
        raise ValueError(
            "Tunnel name must be 1-15 characters (letters, numbers, underscores only)"
        )
    return value


def validate_port(value):
    """Validate port number (1024-65535)."""
    if not value:
        raise ValueError("Port cannot be empty")
    value = value.strip()
    if not VALID_PORT_PATTERN.match(value):
        raise ValueError("Port must be between 1024 and 65535")
    return value


def validate_network(value):
    """Validate network in CIDR notation (e.g., 10.0.0.0/24)."""
    if not value:
        raise ValueError("Network cannot be empty")
    value = value.strip()

    if not VALID_IPV4_CIDR_PATTERN.match(value):
        raise ValueError("Network must be in CIDR notation (e.g., 10.0.0.0/24)")

    ip, prefix = value.split('/')
    parts = ip.split('.')
    for part in parts:
        if int(part) > 255:
            raise ValueError("Network contains invalid IP octet")

    prefix_num = int(prefix)
    if prefix_num > 32:
        raise ValueError("Network prefix must be between 0 and 32")

    return value


def validate_allowed_ips(value):
    """Validate allowed IPs (comma-separated CIDRs)."""
    if not value:
        return value

    entries = [e.strip() for e in value.split(',') if e.strip()]
    for entry in entries:
        if not VALID_IPV4_CIDR_PATTERN.match(entry):
            raise ValueError(f"Invalid CIDR in AllowedIPs: {entry}")
        ip, prefix = entry.split('/')
        parts = ip.split('.')
        for part in parts:
            if int(part) > 255:
                raise ValueError(f"Invalid IP octet in AllowedIPs: {entry}")
        prefix_num = int(prefix)
        if prefix_num > 32:
            raise ValueError(f"Invalid prefix in AllowedIPs: {entry}")

    return value


def validate_dns(value):
    """Validate DNS servers (comma-separated IPs or hostnames)."""
    if not value:
        return value

    entries = [e.strip() for e in value.split(',') if e.strip()]
    for entry in entries:
        if len(entry) > 253:
            raise ValueError(f"DNS entry exceeds maximum length: {entry}")
        if not VALID_HOSTNAME_OR_IP_PATTERN.match(entry):
            raise ValueError(f"Invalid DNS entry (alphanumeric, dots, dashes only): {entry}")

    return value


def validate_endpoint_host(value):
    """Validate endpoint hostname or IP."""
    if not value:
        raise ValueError("Endpoint host cannot be empty")
    value = value.strip()
    if len(value) > 253:
        raise ValueError("Endpoint host exceeds maximum length (253)")
    if not VALID_HOSTNAME_OR_IP_PATTERN.match(value):
        raise ValueError(
            "Endpoint host must be alphanumeric with dots and dashes only"
        )
    return value


def validate_endpoint_port(value):
    """Validate endpoint port number (1024-65535)."""
    return validate_port(value)


def validate_telegram_bot_token(value):
    """Validate Telegram bot token format."""
    if not value:
        return value
    value = value.strip()
    if value and not re.match(r'^\d+:[\w\-]+$', value):
        raise ValueError("Invalid Telegram bot token format")
    return value


def validate_telegram_chat_id(value):
    """Validate Telegram chat ID format."""
    if not value:
        return value
    value = value.strip()
    if value and not re.match(r'^-?\d+$', value):
        raise ValueError("Invalid Telegram chat ID format (must be numeric)")
    return value


def validate_positive_int(value, field_name="Value"):
    """Validate positive integer."""
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
    value = value.strip()
    if not VALID_POSITIVE_INT_PATTERN.match(value):
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def validate_timezone(value):
    """Validate IANA timezone string."""
    if not value:
        raise ValueError("Timezone cannot be empty")
    value = value.strip()

    import pytz
    try:
        pytz.timezone(value)
    except pytz.exceptions.UnknownTimeZoneError:
        raise ValueError(f"Invalid timezone: {value}")

    return value
