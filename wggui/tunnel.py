import subprocess
import time
from .database import get_setting, set_setting
from .wireguard import generate_key_pair
from .config_service import ConfigService
from .system import is_wg_installed, get_wg_install_instructions


def restart_tunnel():
    """Restart the WireGuard tunnel using wg-quick.

    This function now delegates to ConfigService for consistent behavior.
    """
    return ConfigService.restart_tunnel()


def check_wg_permissions():
    """Check if the current user has permission to run 'wg show'.
    
    Returns:
        tuple: (ok: bool, error_message: str or None)
    """
    try:
        result = subprocess.run(
            ['wg', 'show'],
            capture_output=True,
            text=True
        )
        if result.returncode != 0 and 'permission denied' in result.stderr.lower():
            return False, "Permission denied when running 'wg show'. The current user needs permission to access WireGuard."
        return True, None
    except Exception:
        return True, None


def check_tunnel_status():
    """Check if the WireGuard tunnel is active.

    This function now delegates to ConfigService for consistent behavior.
    """
    return ConfigService.check_tunnel_status()


def generate_and_store_server_keys():
    """Generate server keys and store them in the database."""
    if not is_wg_installed():
        return False, get_wg_install_instructions()

    try:
        private_key, public_key = generate_key_pair()

        set_setting('server_private_key', private_key)
        set_setting('server_public_key', public_key)

        return True, "Server keys generated successfully"
    except Exception as e:
        return False, f"Error generating server keys: {str(e)}"


def import_and_store_server_keys(private_key, public_key):
    """Import and store existing server keys in the database."""
    if not is_wg_installed():
        return False, get_wg_install_instructions()

    try:
        # Validate the private key by deriving public key
        result = subprocess.run(['wg', 'pubkey'], input=private_key, 
                              capture_output=True, text=True)
        
        if result.returncode != 0:
            return False, "Invalid private key. Could not derive public key."
        
        derived_public = result.stdout.strip()
        if derived_public != public_key:
            return False, "Public key does not match the derived key from private key."

        set_setting('server_private_key', private_key)
        set_setting('server_public_key', public_key)

        return True, "Server keys imported successfully"
    except Exception as e:
        return False, f"Error importing server keys: {str(e)}"


def check_server_keys_exist():
    """Check if server keys are configured."""
    private_key = get_setting('server_private_key')
    public_key = get_setting('server_public_key')

    return bool(private_key and public_key)


def validate_prerequisites():
    """Validate all prerequisites for the application to work."""
    errors = []
    warnings = []

    # Check if WireGuard is installed - this is a critical error
    if not is_wg_installed():
        errors.append(get_wg_install_instructions())
        # Don't check tunnel status if wg isn't installed - it will fail misleadingly
        return errors, warnings

    # Check wg show permissions
    wg_ok, wg_error = check_wg_permissions()
    if not wg_ok:
        errors.append(wg_error)
        return errors, warnings

    # Check server keys
    if not check_server_keys_exist():
        errors.append("Server keys not configured. Go to Settings to generate them.")

    # Check tunnel name
    tunnel_name = get_setting('wg_tunnel_name')
    if not tunnel_name:
        errors.append("Tunnel name not configured")

    # Check listen port
    listen_port = get_setting('wg_listen_port')
    if not listen_port:
        errors.append("Listen port not configured")

    # Check network
    network = get_setting('wg_network')
    if not network:
        errors.append("Network not configured")

    # Check if tunnel is active (uses ConfigService now)
    tunnel_ok, _ = check_tunnel_status()
    if not tunnel_ok:
        errors.append(f"Tunnel '{tunnel_name}' is not active")

    # Warnings
    endpoint_host = get_setting('wg_endpoint_host')
    if not endpoint_host:
        warnings.append("Endpoint (Host) not configured (needed for client configs)")

    dns = get_setting('wg_dns')
    if not dns:
        warnings.append("DNS not configured")

    telegram_enabled = get_setting('telegram_enabled')
    if telegram_enabled != 'True':
        warnings.append("Telegram notifications not enabled")

    auto_restart = get_setting('auto_restart_tunnel')
    if auto_restart != 'True':
        warnings.append("Auto-restart of tunnel is disabled")

    return errors, warnings
