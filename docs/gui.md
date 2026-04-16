# GUI Component Context

## Overview

The GUI component handles all web interface rendering, user interactions, and template rendering using Flask with Jinja2 templates and Tailwind CSS.

## Key Files

- [`wggui/templates/base.html`](wggui/templates/base.html) - Base template with navbar, flash messages, and footer
- [`wggui/templates/dashboard.html`](wggui/templates/dashboard.html) - Main dashboard with stats, alerts, and peer cards
- [`wggui/templates/peers.html`](wggui/templates/peers.html) - Peer list with filtering
- [`wggui/templates/create_peer.html`](wggui/templates/create_peer.html) - New peer creation form
- [`wggui/templates/import_peer.html`](wggui/templates/import_peer.html) - Existing peer import form
- [`wggui/templates/peer_created.html`](wggui/templates/peer_created.html) - Post-creation page with QR and config
- [`wggui/templates/login.html`](wggui/templates/login.html) - Login page
- [`wggui/templates/setup.html`](wggui/templates/setup.html) - Initial setup page
- [`wggui/templates/users.html`](wggui/templates/users.html) - User management page
- [`wggui/templates/settings.html`](wggui/templates/settings.html) - Settings pages (redirects to wireguard settings)
- [`wggui/templates/settings/*.html`](wggui/templates/settings/) - Individual settings pages (wireguard, server_keys, tunnel, telegram, refresh, export_import)

## Structure

### Navigation (Navbar)

Located in [`base.html`](wggui/templates/base.html):
- WireGuard GUI logo
- Dashboard link
- Peers link
- Users link
- Settings dropdown (WireGuard, Server Keys, Tunnel, Telegram, Refresh, Export/Import)
- Notification bell with badge
- User info and logout
- Mobile hamburger menu

### Flash Messages

Located in [`base.html`](wggui/templates/base.html:183-196):
- Fixed position (top-right)
- Auto-hide after 5 seconds
- Success (green) and Error (red) variants
- Icons for visual feedback

### Dashboard Components

Located in [`dashboard.html`](wggui/templates/dashboard.html):

1. **Alerts Section** - Validation errors and warnings from `validate_prerequisites()`
2. **Stats Cards** - Total peers, enabled peers, connected now, created today
3. **Quick Actions** - New peer, import peer, restart tunnel buttons
4. **Peers Grid** - Recent peer cards with status, IP, public key, last handshake
5. **Recent History Table** - Connection events with filtering

### Settings Pages

Individual settings pages in [`wggui/templates/settings/`](wggui/templates/settings/):
- **wireguard.html** - WireGuard configuration (interface, port, network, allowed_ips, DNS, endpoint)
- **server_keys.html** - Server keys management (generate/import)
- **tunnel.html** - Tunnel control (start, stop, status)
- **telegram.html** - Telegram notification settings
- **refresh.html** - Refresh interval configuration
- **export_import.html** - Export/import configuration JSON

## Routes

Main routes in [`app.py`](app.py):

| Route | Function | Description |
|-------|----------|-------------|
| `/` | `index()` | Dashboard |
| `/login` | `login()` | Login page |
| `/logout` | `logout()` | Logout |
| `/setup` | `setup()` | Initial setup |
| `/users` | `users()` | User management |
| `/peers` | `peers()` | Peer list |
| `/peers/create` | `create_peer()` | Create new peer |
| `/peers/import` | `import_peer()` | Import existing peer |
| `/peers/<id>/toggle` | `toggle_peer()` | Enable/disable peer |
| `/peers/<id>/delete` | `delete_peer()` | Delete peer |
| `/settings/wireguard` | `settings_wireguard()` | WireGuard settings |
| `/settings/server-keys` | `settings_server_keys()` | Server keys settings |
| `/settings/tunnel` | `settings_tunnel()` | Tunnel settings |
| `/settings/telegram` | `settings_telegram()` | Telegram settings |
| `/settings/refresh` | `settings_refresh()` | Refresh settings |
| `/settings/export-import` | `settings_export_import()` | Export/Import settings |

## API Endpoints

| Endpoint | Function | Description |
|----------|----------|-------------|
| `/api/stats` | `api_stats()` | Dashboard statistics |
| `/api/notifications` | `api_notifications()` | Recent notifications |
| `/api/telegram-vars` | `api_telegram_vars()` | Telegram template variables help |

## Styling

- **Tailwind CSS** via CDN (script tag in base.html)
- **Custom colors** defined in tailwind.config (primary, success, warning, danger)
- **Font Awesome** for icons
- **Custom animations** - fade-in, slide-in effects
- **Responsive design** - Mobile-first with breakpoint prefixes

## JavaScript Functions

Located in [`base.html`](wggui/templates/base.html:217-325):

1. **Mobile menu toggle** - Toggle mobile navigation
2. **Flash message auto-hide** - Auto-hide after 5 seconds
3. **Settings dropdown** - Toggle settings dropdown
4. **Notifications dropdown** - Load and display notifications via API
5. **Close dropdowns on outside click** - UX improvement

## Validation Messages

The dashboard displays validation results from `validate_prerequisites()` in [`tunnel.py`](wggui/tunnel.py:67-113):

- **Errors** (red) - Critical issues blocking functionality
- **Warnings** (yellow) - Incomplete configuration
- Each alert includes a link to relevant settings page

## Peer Card Display

Each peer displays:
- Name and assigned IP
- Status badge (enabled/disabled)
- Public key (truncated)
- Endpoint IP (if connected)
- Last handshake timestamp
- Action buttons (toggle, delete)

## Flash Message Categories

- `success` - Green, checkmark icon
- `error` - Red, exclamation icon
- `warning` - Yellow (via success category with warning text)
