# System Interaction Component Context

## Overview

The System Interaction component handles all interactions with the WireGuard system, including key generation, tunnel management, and configuration file handling.

## Key Files

- [`wggui/wireguard.py`](wggui/wireguard.py) - WireGuard operations (key generation, config generation)
- [`wggui/tunnel.py`](wggui/tunnel.py) - Tunnel management (start, stop, restart, validation)
- [`wggui/config_service.py`](wggui/config_service.py) - Centralized config file management

## Key Generation

Located in [`wireguard.py`](wggui/wireguard.py:10-45):

### Functions

```python
generate_private_key()           # Uses 'wg genkey' - never persists to disk
derive_public_key(private_key)   # Uses 'wg pubkey' to derive from private
generate_pre_shared_key()        # Uses 'wg genpsk'
generate_key_pair()              # Returns (private_key, public_key)
generate_peer_keys()             # Returns (private_key, public_key, pre_shared_key)
```

**Important:** Private keys are generated in memory only and never written to disk.

## IP Assignment

Located in [`wireguard.py`](wggui/wireguard.py:48-68):

```python
get_next_available_ip()  # Finds next available IP from configured network
```

- Skips network address and broadcast address
- Checks existing peers for used IPs
- Raises exception if no IPs available

## Configuration Generation

### Client Configuration

Located in [`wireguard.py`](wggui/wireguard.py:71-95):

```python
generate_client_config(peer, private_key)
```

Generates INI-format config with:
- Interface section (private key, address, DNS)
- Peer section (server public key, endpoint, allowed IPs, persistent keepalive)
- Pre-shared key if configured

### Server Configuration

Located in [`wireguard.py`](wggui/wireguard.py:111-145):

```python
generate_server_config()
```

Generates wg0.conf format:
- Interface section (address, listen port, server private key)
- Peer sections for each enabled peer (public key, allowed IPs, pre-shared key)

## QR Code Generation

Located in [`wireguard.py`](wggui/wireguard.py:98-108):

```python
generate_qr_image(config_content)  # Returns base64-encoded PNG
```

Uses qrcode library to generate QR codes for client configuration.

## WireGuard Status Parsing

Located in [`wireguard.py`](wggui/wireguard.py:148-303):

### parse_wg_show_dump()

Parses output of `wg show all dump` command:
```
interface public_key preshared_key endpoint allowed_ips latest_handshake tx_bytes rx_bytes persistent_keepalive
```

Returns dict with peer status including:
- `interface` - WireGuard interface name
- `endpoint` - Public IP:port of peer (or None)
- `latest_handshake` - Unix timestamp
- `tx_bytes` / `rx_bytes` - Transfer statistics
- `persistent_keepalive` - Keepalive setting

### update_peer_handshakes()

Updates database with:
- `last_handshake` timestamp from parsed output
- `endpoint_ip` from peer endpoint

### get_connected_peers()

Returns list of peers with handshakes within 120 seconds (2 minutes).

### get_peer_transfer_stats()

Returns transfer statistics for a specific peer.

## Tunnel Management

Located in [`tunnel.py`](wggui/tunnel.py):

### Functions

```python
restart_tunnel()                          # Delegates to ConfigService
check_tunnel_status()                     # Delegates to ConfigService
generate_and_store_server_keys()          # Generate and persist server keys
import_and_store_server_keys(pk, pubk)    # Import existing server keys
check_server_keys_exist()                 # Check if server keys are configured
validate_prerequisites()                  # Validate all prerequisites
```

## Config Service

Located in [`config_service.py`](wggui/config_service.py):

### Class: ConfigService

**Key Methods:**

```python
generate_server_config()          # Generate server config content
get_config_path()                 # Get config file path from settings
write_config_file(config)         # Write config to disk (atomic rename)
generate_and_write_config()       # Generate and write config
restart_tunnel()                  # Regenerate config + wg-quick down/up
should_auto_restart()             # Check if auto-restart is enabled
on_peer_change(action)            # Handle peer create/modify/delete
check_tunnel_status()             # Check if tunnel is active
apply_config_to_running_tunnel()  # Apply config without restart (wg sync)
```

**Caching:**
- `_config_cache` - Cached config content
- `_cache_valid` - Cache validity flag
- `_last_config_hash` - Hash for change detection
- `mark_dirty()` - Invalidate cache

## Prerequisites Validation

Located in [`tunnel.py`](wggui/tunnel.py:67-113):

**Errors (blocking):**
- Server keys not configured
- Tunnel name not configured
- Listen port not configured
- Network not configured
- Tunnel not active

**Warnings (non-blocking):**
- Endpoint not configured
- DNS not configured
- Telegram not enabled
- Auto-restart disabled

## System Commands Used

| Command | Purpose |
|---------|---------|
| `wg genkey` | Generate private key |
| `wg pubkey` | Derive public key from private |
| `wg genpsk` | Generate pre-shared key |
| `wg show all dump` | Get peer status and transfer stats |
| `wg show <tunnel>` | Check tunnel status |
| `wg-quick down <tunnel>` | Bring down tunnel |
| `wg-quick up <tunnel>` | Bring up tunnel |
| `wg sync all <config>` | Sync config without restart |

## Config File Path

Configured via `server_config_path` setting, defaults to:
```
/etc/wireguard/{wg_tunnel_name}.conf
```

## Workflow: Peer Creation

1. Generate keys in memory (private, public, PSK)
2. Assign IP from available pool
3. Create peer record (only public key and PSK stored)
4. Generate client config with private key
5. Generate QR code
6. Generate server config with new peer
7. Write server config to disk
8. Optionally restart tunnel

## Workflow: Tunnel Restart

1. Generate server config
2. Write config file (atomic rename)
3. Run `wg-quick down <tunnel>`
4. Wait 1 second
5. Run `wg-quick up <tunnel>`
6. Verify success

## Security Notes

- Private keys generated via `wg genkey` never persist to disk
- Server private key stored in database but marked as write-only
- Config file written atomically (temp file + rename)
- Private key only available during peer creation request
