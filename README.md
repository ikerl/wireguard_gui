# WireGuard GUI

A secure, modern web interface for managing WireGuard VPN servers. Built with Flask and designed for production use with security-first principles.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

WireGuard GUI provides a complete administration panel for WireGuard VPN servers. Create and manage clients, receive real-time connection notifications via Telegram, monitor peer activity, and export/import configurations - all through an intuitive, responsive and simple interface.

## Features

### Peer Management
- Create new peers with automatic key generation using `wg genkey`
- Import existing peer configurations
- Enable/disable peers without deleting
- Real-time connection status monitoring
- QR code generation for mobile client configuration

### Security
- **Input Validation**: Strict validation on all user inputs (ports, IPs, CIDR notation, hostnames, etc.)
- **Path Traversal Protection**: Config path must be within `/etc/wireguard/`
- **SQL Injection Prevention**: SQLAlchemy ORM with parameterized queries throughout
- **Template Injection Protection**: Telegram message templates use variable allowlist with escaping
- **Peer Name Sanitization**: Alphanumeric, spaces, underscores, dashes only (max 64 chars)
- **No Sensitive Data Exposure**: Stack traces never shown to users; logged internally
- **Private Key Handling**: Server private keys stored encrypted; client private keys exist only in memory during creation

### Notifications
- Telegram bot integration for real-time connection/disconnection alerts
- Customizable message templates with safe variable substitution
- Support for `{name}`, `{ip}`, `{endpoint_ip}`, `{timestamp}`, `{public_key}`, `{status}`

### Server Configuration
- Interface and tunnel name configuration
- Listen port (1024-65535 validation)
- Network and AllowedIPs configuration (CIDR validation)
- DNS server configuration
- Endpoint host/port configuration
- Config file path validation (must be within `/etc/wireguard/`)

### Monitoring
- Real-time peer connection status via polling
- Connection history tracking with timestamps
- Dashboard with statistics (total peers, connected now, created today)
- Prerequisites validation (WireGuard installed, tunnel active, permissions)

### Data Management
- Export full configuration to JSON (settings + peers)
- Import configuration with validation (rejects invalid fields)
- Automatic config file generation and backup

## Security Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     INPUT VALIDATION                        │
├─────────────────────────────────────────────────────────────┤
│  • Port numbers: 1024-65535                                  │
│  • Network CIDR: IP/prefix validation with octet bounds      │
│  • Hostnames: alphanumeric + dots + dashes, max 253 chars    │
│  • Peer names: [a-zA-Z0-9_\-\s], max 64 chars               │
│  • Config paths: Must resolve to /etc/wireguard/ after       │
│    normalization (prevents ../../../ traversal)              │
│  • Telegram tokens: \d+:[\w-]+ format                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     DATA PROTECTION                         │
├─────────────────────────────────────────────────────────────┤
│  • Private keys: never written to disk (memory only)         │
│  • Server keys: stored in DB with restricted access         │
│  • Config files: atomic writes with backup                  │
│  • Error messages: generic to users, detailed to logs        │
└─────────────────────────────────────────────────────────────┘
```

## Requirements

- Python 3.9+
- WireGuard tools (`wg`, `wg-quick`)
- Linux with wireguard module

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd wireguard_gui

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Ensure WireGuard is installed
# Debian/Ubuntu: apt install wireguard
# RHEL/Fedora: dnf install wireguard-tools
```

## Usage

```bash
# Run the application
python app.py

# Access at http://localhost:5000
# On first run, create the admin user
```

## Configuration

1. **Initial Setup**
   - Create admin account at first login
   - Go to Settings → Server Keys → Generate Keys
   - Configure WireGuard interface parameters

2. **Telegram Notifications** (optional)
   - Create a bot via @BotFather
   - Get your chat ID via @userinfobot
   - Configure in Settings → Telegram

3. **Peer Management**
   - Create new peers with auto-generated keys
   - Scan QR code with WireGuard mobile app
   - Import existing peers by public key

## Project Structure

```
wireguard_gui/
├── app.py                      # Flask application and routes
├── config.py                   # Flask configuration
├── requirements.txt            # Python dependencies
├── wggui/
│   ├── __init__.py
│   ├── auth.py                 # Flask-Login authentication
│   ├── database.py             # SQLAlchemy models + validators
│   ├── tunnel.py               # Tunnel management and prerequisites
│   ├── wireguard.py            # Key generation and config building
│   ├── telegram.py             # Telegram notifications
│   ├── scheduler.py            # Background polling scheduler
│   ├── config_service.py       # Config file generation
│   └── templates/              # Jinja2 HTML templates
└── instance/                   # SQLite database
```

## Security Notes

- **Password Hashing**: SHA256 with unique salt per user (consider using bcrypt for higher security)
- **Session Management**: Flask-Login with configurable session lifetime
- **CSRF Protection**: Consider implementing Flask-WTF for form protection
- **Rate Limiting**: Recommended for production (Flask-Limiter)
- **HTTPS**: Should be used in production behind a reverse proxy

## License

MIT
