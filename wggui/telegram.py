import asyncio
import re
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
from .database import get_setting, ConnectionHistory, db


# Whitelist of allowed template variables for security
ALLOWED_TEMPLATE_VARS = frozenset({
    'name', 'ip', 'endpoint_ip', 'timestamp', 'public_key', 'status'
})


def escape_template_value(value):
    """
    Safely escape a value for use in Python str.format().
    Replaces { and } with {{ and }} to prevent format injection.
    """
    if not isinstance(value, str):
        value = str(value)
    # Replace { and } with {{ and }} to escape them
    return value.replace('{', '{{').replace('}', '}}')


def extract_template_variables(template):
    """
    Extract all variable names from a template.
    Returns a set of variable names (without braces).
    """
    # Match {var} or {var:format} patterns
    pattern = r'\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]*)?\}'
    matches = re.findall(pattern, template)
    return set(matches)


def validate_template_variables(template):
    """
    Validate that a template only uses allowed variables.
    Returns (is_valid, error_message).
    """
    used_vars = extract_template_variables(template)
    invalid_vars = used_vars - ALLOWED_TEMPLATE_VARS

    if invalid_vars:
        return False, f"Variables no permitidas: {', '.join(sorted(invalid_vars))}. Variables permitidas: {', '.join(sorted(ALLOWED_TEMPLATE_VARS))}"

    return True, None


def safe_format_template(template, **values):
    """
    Safely format a template with values.
    Escapes values to prevent format injection.
    """
    # Escape all values before formatting
    escaped_values = {k: escape_template_value(v) for k, v in values.items()}
    return template.format(**escaped_values)


async def send_telegram_notification(peer, event_type='connection'):
    """Send Telegram notification for peer connection/disconnection."""
    enabled = get_setting('telegram_enabled', 'False')
    
    print(f"[DEBUG] send_telegram_notification for {peer.name}: telegram_enabled='{enabled}'")
    print(f"[DEBUG] Setting type: {type(enabled)}, repr: {repr(enabled)}")

    if enabled != 'True':
        print(f"[DEBUG] Telegram notifications are disabled for peer {peer.name}")
        return False, "Telegram notifications are disabled"

    bot_token = get_setting('telegram_bot_token', '')
    chat_id = get_setting('telegram_chat_id', '')

    if not bot_token or not chat_id:
        print(f"[DEBUG] Telegram credentials not configured for peer {peer.name}")
        return False, "Telegram credentials not configured"

    # Build message from template
    template = get_setting('telegram_message_template', '')

    # Replace variables safely
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    # Use safe formatting to prevent template injection
    message = safe_format_template(
        template,
        name=peer.name,
        ip=peer.assigned_ip,
        endpoint_ip=peer.endpoint_ip or 'Desconectado',
        timestamp=timestamp,
        public_key=peer.public_key[:16] + '...',
        status='Conectado' if event_type == 'connection' else 'Desconectado',
    )

    print(f"[DEBUG] Telegram message template: {repr(template)}")
    print(f"[DEBUG] Telegram message built: {repr(message)}")
    print(f"[DEBUG] Message length: {len(message)}, byte 53: {repr(message[53:54]) if len(message) > 53 else 'N/A'}")

    try:
        bot = Bot(token=bot_token)
        # Send message as plain text (template now uses emojis without HTML)
        await bot.send_message(chat_id=chat_id, text=message)
        return True, "Notification sent successfully"
    except TelegramError as e:
        return False, f"Telegram error: {str(e)}"


def sync_send_telegram_notification(peer, event_type='connection'):
    """Synchronous wrapper for send_telegram_notification."""
    return asyncio.run(send_telegram_notification(peer, event_type))


def should_notify_peer(peer, was_connected=False):
    """Check if a peer should trigger a notification.
    
    Args:
        peer: The peer to check
        was_connected: Whether the peer was connected before (from scheduler state)
    """
    expire_seconds = int(get_setting('telegram_expire_seconds', '300'))

    print(f"[DEBUG] should_notify_peer for {peer.name}: connection_notified={peer.connection_notified}, was_connected={was_connected}")
    print(f"[DEBUG] peer.last_handshake = {peer.last_handshake}")
    print(f"[DEBUG] peer.last_disconnection = {peer.last_disconnection}")

    # If peer was never connected (was_connected=False), this is a NEW connection
    # Always notify for new connections, unless already notified in database
    if not was_connected:
        if not peer.connection_notified:
            print(f"[DEBUG] Peer {peer.name} is new connection, should notify")
            return True
        else:
            # Database is out of sync - peer was marked as notified but scheduler says not connected
            # Treat as new connection
            print(f"[DEBUG] Peer {peer.name} database out of sync, treating as new connection")
            return True

    # Peer was connected before - this is a RECONNECTION after disconnect
    # Use last_disconnection for elapsed calculation
    if peer.last_disconnection:
        elapsed = (datetime.utcnow() - peer.last_disconnection).total_seconds()
        print(f"[DEBUG] Peer {peer.name}: elapsed_since_disconnect={elapsed}s, expire_seconds={expire_seconds}s")
        if elapsed > expire_seconds:
            print(f"[DEBUG] Peer {peer.name} should be notified (reconnected after {elapsed}s)")
            return True
    else:
        # No disconnection timestamp - check if ever connected before
        # If connection_notified=True but no disconnection, it was a stable connection
        # Check last_handshake for elapsed
        if peer.last_handshake:
            elapsed = (datetime.utcnow() - peer.last_handshake).total_seconds()
            print(f"[DEBUG] Peer {peer.name}: elapsed_since_handshake={elapsed}s, expire_seconds={expire_seconds}s")
            if elapsed > expire_seconds:
                print(f"[DEBUG] Peer {peer.name} should be notified (stable peer reconnected)")
                return True

    print(f"[DEBUG] Peer {peer.name} should NOT be notified")
    return False


def notify_connected_peers(peers, peers_state=None):
    """Notify about newly connected peers.
    
    Args:
        peers: List of peers to check for notifications
        peers_state: Dict mapping peer names to their previous connection state (was_connected)
    """
    notifications = []
    if peers_state is None:
        peers_state = {}
    
    for peer in peers:
        was_connected = peers_state.get(peer.name, False)
        print(f"[DEBUG] Processing peer {peer.name} for connection notification (was_connected={was_connected})")
        if should_notify_peer(peer, was_connected):
            print(f"[DEBUG] Calling send_telegram_notification for {peer.name}")
            success, msg = sync_send_telegram_notification(peer, 'connection')
            print(f"[DEBUG] send_telegram_notification result for {peer.name}: success={success}, msg={msg}")

            if success:
                peer.connection_notified = True
                db.session.commit()

                # Log in history
                history = ConnectionHistory(
                    peer_id=peer.id,
                    event_type='connection',
                    details='Notification sent via Telegram',
                    endpoint_ip=peer.endpoint_ip
                )
                db.session.add(history)
                db.session.commit()

                notifications.append({
                    'peer': peer.name,
                    'status': 'success',
                    'message': msg
                })
            else:
                notifications.append({
                    'peer': peer.name,
                    'status': 'error',
                    'message': msg
                })

    return notifications


def notify_disconnected_peers(peers):
    """Notify about disconnected peers (session expired)."""
    notifications = []

    for peer in peers:
        print(f"[DEBUG] Processing peer {peer.name} for disconnection notification")
        success, msg = sync_send_telegram_notification(peer, 'disconnection')
        print(f"[DEBUG] send_telegram_notification result for {peer.name}: success={success}, msg={msg}")

        if success:
            # Log in history
            history = ConnectionHistory(
                peer_id=peer.id,
                event_type='disconnection',
                details='Notificación de desconexión enviada',
                endpoint_ip=peer.endpoint_ip
            )
            db.session.add(history)
            db.session.commit()

            notifications.append({
                'peer': peer.name,
                'status': 'success',
                'message': msg
            })
        else:
            notifications.append({
                'peer': peer.name,
                'status': 'error',
                'message': msg
            })

    return notifications


async def test_telegram_connection_async():
    """Test if Telegram credentials are valid (async)."""
    bot_token = get_setting('telegram_bot_token', '')
    chat_id = get_setting('telegram_chat_id', '')

    if not bot_token or not chat_id:
        return False, "Bot token or chat ID not configured"

    try:
        bot = Bot(token=bot_token)
        test_message = "✅ Prueba de conexion exitosa\n\nWireGuard GUI esta correctamente configurado para enviar notificaciones."
        await bot.send_message(chat_id=chat_id, text=test_message)
        return True, "Test message sent successfully"
    except TelegramError as e:
        return False, f"Connection failed: {str(e)}"


def test_telegram_connection():
    """Test if Telegram credentials are valid."""
    return asyncio.run(test_telegram_connection_async())


def format_telegram_variables_help():
    """Return help text for available template variables."""
    return """
    **Variables disponibles para plantillas (HTML):**

    - `{name}` - Nombre del peer/cliente
    - `{ip}` - IP asignada (interna WireGuard)
    - `{endpoint_ip}` - IP pública del peer (remota)
    - `{timestamp}` - Fecha y hora del evento
    - `{public_key}` - Clave pública del cliente (truncada)
    - `{status}` - Estado de conexión

    **Etiquetas HTML:**
    - `<b>texto</b>` - Texto en negrita
    - `<code>texto</code>` - Texto en código monoespaciado
    - `<i>texto</i>` - Texto en cursiva
    - `<s>texto</s>` - Texto tachado

    **Ejemplo:**
    ```
    <b>Nuevo Acceso WireGuard</b>

    <b>Cliente:</b> {name}
    <b>IP Asignada:</b> <code>{ip}</code>
    <b>IP Publica:</b> {endpoint_ip}
    <b>Hora:</b> {timestamp}
    ```
    """
