import subprocess
import ipaddress
import qrcode
import base64
from io import BytesIO
from datetime import datetime, timezone
from .database import Peer, ConnectionHistory, get_setting, set_setting, db
from .system import is_wg_installed, get_wg_install_instructions


def generate_private_key():
    """Generate WireGuard private key using wg genkey."""
    if not is_wg_installed():
        raise Exception(get_wg_install_instructions())
    result = subprocess.run(['wg', 'genkey'], capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Error generating private key: {result.stderr}")
    return result.stdout.strip()


def derive_public_key(private_key):
    """Derive public key from private key using wg pubkey."""
    if not is_wg_installed():
        raise Exception(get_wg_install_instructions())
    result = subprocess.run(['wg', 'pubkey'], input=private_key, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Error deriving public key: {result.stderr}")
    return result.stdout.strip()


def generate_pre_shared_key():
    """Generate WireGuard pre-shared key using wg genpsk."""
    if not is_wg_installed():
        raise Exception(get_wg_install_instructions())
    result = subprocess.run(['wg', 'genpsk'], capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Error generating PSK: {result.stderr}")
    return result.stdout.strip()


def generate_key_pair():
    """Generate a complete key pair (private + public)."""
    private_key = generate_private_key()
    public_key = derive_public_key(private_key)
    return private_key, public_key


def generate_peer_keys():
    """Generate keys for a new peer (private, public, PSK)."""
    private_key, public_key = generate_key_pair()
    pre_shared_key = generate_pre_shared_key()
    return private_key, public_key, pre_shared_key


def get_next_available_ip():
    """Get the next available IP from the configured network.
    
    Excludes:
    - First 100 IPs (1-100)
    - Last 10 IPs of the range (e.g., 240-254 for a /24)
    - Network address and broadcast address
    """
    network_str = get_setting('wg_network', '10.0.0.0/24')
    network = ipaddress.ip_network(network_str, strict=False)

    # Get all used IPs
    used_ips = set()
    for peer in Peer.query.all():
        if peer.assigned_ip:
            used_ips.add(ipaddress.ip_address(peer.assigned_ip))

    # Calculate IP range bounds
    network_int = int(network.network_address)
    broadcast_int = int(network.broadcast_address)
    
    # Define reserved ranges (excluding network and broadcast)
    # First 100 IPs: network_address + 1 to network_address + 100
    first_100_start = network_int + 1
    first_100_end = network_int + 100
    
    # Last 10 IPs: broadcast_address - 10 to broadcast_address - 1
    last_10_start = broadcast_int - 10
    last_10_end = broadcast_int - 1

    # Find next available IP in the allowed range
    for ip_int in range(network_int, broadcast_int + 1):
        ip = ipaddress.ip_address(ip_int)
        
        # Skip network address
        if ip_int == network_int:
            continue
        # Skip broadcast address
        if ip_int == broadcast_int:
            continue
        # Skip first 100 IPs
        if first_100_start <= ip_int <= first_100_end:
            continue
        # Skip last 10 IPs
        if last_10_start <= ip_int <= last_10_end:
            continue
        # Skip used IPs
        if ip in used_ips:
            continue
        
        return str(ip)

    raise Exception("No available IPs in the allowed range")


def generate_client_config(peer, private_key):
    """Generate WireGuard client configuration file content."""
    endpoint_host = get_setting('wg_endpoint_host', '')
    endpoint_port = get_setting('wg_endpoint_port', '51820')
    endpoint = f"{endpoint_host}:{endpoint_port}" if endpoint_host else 'vpn.example.com:51820'
    dns = get_setting('wg_dns', '')
    network = get_setting('wg_network', '10.0.0.0/24')
    server_public_key = get_setting('server_public_key', '')
    
    # Determine AllowedIPs (priority: peer setting > global setting > network)
    allowed_ips = peer.allowed_ips if peer.allowed_ips else get_setting('wg_allowed_ips', '')
    if not allowed_ips:
        allowed_ips = network

    config = f"""[Interface]
PrivateKey = {private_key}
Address = {peer.assigned_ip}/32
DNS = {dns}

[Peer]
PublicKey = {server_public_key}
Endpoint = {endpoint}
AllowedIPs = {allowed_ips}
PersistentKeepalive = 25
"""

    if peer.pre_shared_key:
        config = config.replace('[Peer]', f'[Peer]\nPreSharedKey = {peer.pre_shared_key}')

    return config


def generate_qr_image(config_content):
    """Generate QR code image as base64 string for embedding in HTML."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(config_content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format='PNG')

    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def generate_server_config():
    """Generate complete WireGuard server configuration."""
    interface = get_setting('wg_interface', 'wg0')
    listen_port = get_setting('wg_listen_port', '51820')
    network = get_setting('wg_network', '10.0.0.0/24')
    server_private_key = get_setting('server_private_key', '')

    if not server_private_key:
        raise Exception("Server private key not configured")

    network_obj = ipaddress.ip_network(network, strict=False)
    server_ip = str(network_obj.network_address + 1) + '/' + str(network_obj.prefixlen)

    config = f"""[Interface]
Address = {server_ip}
ListenPort = {listen_port}
PrivateKey = {server_private_key}

"""

    for peer in Peer.query.filter_by(status='enabled').all():
        config += f"""# Peer: {peer.name}
[Peer]
PublicKey = {peer.public_key}
AllowedIPs = {peer.assigned_ip}/32

"""

        if peer.pre_shared_key:
            # Insert PSK after AllowedIPs line
            lines = config.split('\n')
            for i, line in enumerate(lines):
                if line.startswith(f'AllowedIPs = {peer.assigned_ip}/32'):
                    lines.insert(i + 1, f'PreSharedKey = {peer.pre_shared_key}')
                    break
            config = '\n'.join(lines)

    return config


def parse_wg_show():
    """Parse output of 'wg show' command."""
    result = subprocess.run(['wg', 'show'], capture_output=True, text=True)
    if result.returncode != 0:
        return None, f"Error executing wg show: {result.stderr}"

    output = result.stdout
    peers_status = {}

    # Parse interface sections
    sections = output.split('\n\n')
    for section in sections:
        if not section.strip():
            continue

        lines = section.strip().split('\n')
        interface_name = None

        for line in lines:
            if line.startswith('interface:'):
                interface_name = line.replace('interface:', '').strip()
            elif line.startswith('peer:'):
                peer_key = line.replace('peer:', '').strip()
                peers_status[peer_key] = {
                    'interface': interface_name,
                    'last_handshake': None,
                    'transfer': {'rx': 0, 'tx': 0},
                }

            # Parse handshake time
            elif 'handshake' in line.lower():
                # Extract handshake info
                pass

    return peers_status, None


def parse_wg_show_dump():
    """Parse output of 'wg show all dump' command.
    
    Returns a dict with peer status including:
    - endpoint: IP:port pública del peer (o None si no está conectado)
    - latest_handshake: timestamp del último handshake
    - tx_bytes: bytes transmitidos
    - rx_bytes: bytes recibidos
    - persistent_keepalive: estado del keepalive
    """
    result = subprocess.run(['wg', 'show', 'all', 'dump'], capture_output=True, text=True)
    if result.returncode != 0:
        return None, f"Error executing wg show all dump: {result.stderr}"

    peers_status = {}

    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue

        parts = line.split()
        # Formato: interface public_key preshared_key endpoint allowed_ips latest_handshake tx_bytes rx_bytes persistent_keepalive
        if len(parts) >= 8:
            interface = parts[0]
            public_key = parts[1]
            preshared_key = parts[2]
            endpoint = parts[3]  # IP:port o (none)
            allowed_ips = parts[4]
            latest_handshake = parts[5]
            tx_bytes = parts[6]
            rx_bytes = parts[7]
            persistent_keepalive = parts[8] if len(parts) > 8 else 'off'

            peers_status[public_key] = {
                'interface': interface,
                'endpoint': endpoint if endpoint != '(none)' else None,
                'allowed_ips': allowed_ips,
                'latest_handshake': latest_handshake,
                'tx_bytes': int(tx_bytes) if tx_bytes.isdigit() else 0,
                'rx_bytes': int(rx_bytes) if rx_bytes.isdigit() else 0,
                'persistent_keepalive': persistent_keepalive,
            }

    return peers_status, None


def update_peer_handshakes():
    """Update peer handshakes and endpoint IPs from wg show all dump.
    
    This function parses 'wg show all dump' to update:
    - last_handshake timestamp
    - endpoint_ip (IP pública del peer)
    """
    peers_status, error = parse_wg_show_dump()
    if error:
        return [], error

    updated_peers = []
    now = datetime.utcnow()

    for peer_key, status in peers_status.items():
        peer = Peer.query.filter_by(public_key=peer_key).first()
        if peer:
            # Update endpoint IP (public IP of the peer)
            if status['endpoint']:
                peer.endpoint_ip = status['endpoint']

            # Update handshake timestamp
            try:
                handshake_ts = int(status['latest_handshake'])
                # Only update if it's a valid timestamp (> 0), otherwise keep old value
                if handshake_ts > 0:
                    peer.last_handshake = datetime.fromtimestamp(handshake_ts, tz=timezone.utc)
                    updated_peers.append(peer)
            except ValueError:
                pass

    db.session.commit()
    return updated_peers, None


def get_connected_peers():
    """Get list of currently connected peers with their endpoint IPs.
    
    Uses 'wg show all dump' to get real-time connection status.
    A peer is considered connected if handshake was less than disconnect_timeout seconds ago.
    """
    from .database import get_setting
    
    peers_status, error = parse_wg_show_dump()
    if error:
        return []

    connected = []
    now = datetime.now(timezone.utc)
    disconnect_timeout = int(get_setting('disconnect_timeout', '600'))

    for peer_key, status in peers_status.items():
        try:
            handshake_ts = int(status['latest_handshake'])
            handshake_dt = datetime.fromtimestamp(handshake_ts, tz=timezone.utc)

            # Consider connected if handshake was less than disconnect_timeout seconds ago
            if (now - handshake_dt).total_seconds() < disconnect_timeout:
                peer = Peer.query.filter_by(public_key=peer_key).first()
                if peer:
                    # Update endpoint IP
                    if status['endpoint']:
                        peer.endpoint_ip = status['endpoint']
                    connected.append(peer)
        except (ValueError, TypeError):
            continue

    db.session.commit()
    return connected


def get_peer_transfer_stats(public_key):
    """Get transfer stats (tx/rx bytes) for a specific peer."""
    peers_status, _ = parse_wg_show_dump()
    
    if public_key in peers_status:
        return peers_status[public_key]
    return None
