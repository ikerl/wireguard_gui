import shutil


def is_wg_installed():
    """Check if WireGuard tools (wg) are installed."""
    return shutil.which('wg') is not None


def is_wg_quick_installed():
    """Check if wg-quick is installed."""
    return shutil.which('wg-quick') is not None


def get_wg_install_instructions():
    """Return instructions for installing WireGuard."""
    return "WireGuard tools not found. Install with: sudo apt install wireguard"