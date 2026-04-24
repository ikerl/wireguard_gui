import os
import subprocess
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timezone
import io
import json
import logging

from config import *
from wggui.database import db, User, Peer, ConnectionHistory, Settings, init_default_settings, get_setting, set_setting
from wggui.auth import login_manager, init_login_manager, create_admin_user, create_user, delete_user, get_all_users
from wggui.wireguard import generate_peer_keys, get_next_available_ip, generate_client_config, generate_server_config, generate_qr_image
from wggui.tunnel import restart_tunnel, check_tunnel_status, generate_and_store_server_keys, validate_prerequisites, import_and_store_server_keys
from wggui.telegram import test_telegram_connection, format_telegram_variables_help, validate_template_variables
from wggui.scheduler import start_scheduler, update_scheduler_interval
from wggui.config_service import ConfigService
from wggui.system import is_wg_installed, get_wg_install_instructions

app = Flask(__name__, template_folder='wggui/templates')
app.config.from_object('config')

db.init_app(app)
init_login_manager(app)


@app.template_filter('local_time')
def local_time(value, fmt='%Y-%m-%d %H:%M:%S'):
    """Format datetime using the configured timezone."""
    if value is None:
        return '-'
    try:
        timezone_str = get_setting('timezone', 'Europe/Madrid')
        if timezone_str:
            import pytz
            tz = pytz.timezone(timezone_str)
            if value.tzinfo is None:
                value = pytz.utc.localize(value)
            return value.astimezone(tz).strftime(fmt)
    except Exception:
        pass
    return value.strftime(fmt) if hasattr(value, 'strftime') else '-'

# Ensure instance directory exists for SQLite database
instance_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
if not os.path.exists(instance_dir):
    os.makedirs(instance_dir)

with app.app_context():
    db.create_all()
    init_default_settings()
    scheduler = start_scheduler(app)


@app.route('/')
@login_required
def index():
    """Main dashboard page."""
    peers = Peer.query.order_by(db.case((Peer.last_connection.isnot(None), Peer.last_connection), else_=datetime.min).desc()).all()
    settings = {s.key: s.value for s in Settings.query.all()}
    errors, warnings = validate_prerequisites()

    connected_peers = []
    for peer in peers:
        if peer.last_handshake and (datetime.utcnow() - peer.last_handshake).total_seconds() < 120:
            connected_peers.append(peer.id)

    stats = {
        'total_peers': len(peers),
        'enabled_peers': len([p for p in peers if p.status == 'enabled']),
        'connected_now': len(connected_peers),
        'created_today': len([p for p in peers if p.created_at and p.created_at.date() == datetime.utcnow().date()]),
    }

    return render_template('dashboard.html',
                          peers=peers,
                          errors=errors,
                          warnings=warnings,
                          connected_peers=connected_peers,
                          settings=settings,
                          stats=stats,
                          now=datetime.utcnow())


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if not User.query.first():
        return redirect(url_for('setup'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    """Logout user."""
    logout_user()
    return redirect(url_for('login'))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Initial setup - create first admin user."""
    # Check if setup is needed
    if User.query.first():
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if password != confirm_password:
            flash('Passwords do not match', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters', 'error')
        else:
            success, msg = create_admin_user(username, password)
            if success:
                flash('Admin user created. Sign in.', 'success')
                return redirect(url_for('login'))
            else:
                flash(msg, 'error')

    return render_template('setup.html')


# ============== USERS ==============

@app.route('/users')
@login_required
def users():
    """User management page."""
    users_list = get_all_users()
    return render_template('users.html', users=users_list)


@app.route('/users/create', methods=['POST'])
@login_required
def create_user_route():
    """Create a new user."""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    if not username or not password:
        flash('All fields are required', 'error')
    else:
        success, msg = create_user(username, password)
        flash(msg, 'success' if success else 'error')

    return redirect(url_for('users'))


@app.route('/users/delete/<int:user_id>')
@login_required
def delete_user_route(user_id):
    """Delete a user."""
    success, msg = delete_user(user_id)
    flash(msg, 'success' if success else 'error')
    return redirect(url_for('users'))


# ============== PEERS ==============

@app.route('/peers')
@login_required
def peers():
    """List all peers."""
    status_filter = request.args.get('status', 'all')
    search = request.args.get('search', '')

    query = Peer.query

    if status_filter != 'all':
        query = query.filter_by(status=status_filter)

    if search:
        query = query.filter(Peer.name.contains(search) | Peer.assigned_ip.contains(search))

    peers_list = query.order_by(db.case((Peer.last_connection.isnot(None), Peer.last_connection), else_=datetime.min).desc()).all()

    # Get connected peers for highlighting
    connected_peers = [p for p in peers_list if p.last_handshake and
                      (datetime.utcnow() - p.last_handshake).total_seconds() < int(get_setting('disconnect_timeout', '600'))]

    return render_template('peers.html', peers=peers_list, status_filter=status_filter, search=search, now=datetime.utcnow(), connected_peers=connected_peers, settings={s.key: s.value for s in Settings.query.all()})


@app.route('/peers/create', methods=['GET', 'POST'])
@login_required
def create_peer():
    """Create a new peer with generated keys."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        use_custom_ip = request.form.get('use_custom_ip', False)
        assigned_ip = request.form.get('assigned_ip', '').strip() if use_custom_ip else None
        allowed_ips = request.form.get('allowed_ips', '').strip() or None

        if not name:
            flash('Name is required', 'error')
        else:
            try:
                from wggui.database import validate_peer_name
                name = validate_peer_name(name)
            except ValueError as e:
                flash(str(e), 'error')
                return redirect(url_for('create_peer'))
            except Exception:
                flash('Peer name is invalid', 'error')
                return redirect(url_for('create_peer'))

            try:
                private_key, public_key, pre_shared_key = generate_peer_keys()

                if not assigned_ip:
                    assigned_ip = get_next_available_ip()

                existing = Peer.query.filter_by(assigned_ip=assigned_ip).first()
                if existing:
                    flash(f'IP {assigned_ip} is already in use', 'error')
                else:
                    peer = Peer(
                        name=name,
                        public_key=public_key,
                        pre_shared_key=pre_shared_key,
                        assigned_ip=assigned_ip,
                        allowed_ips=allowed_ips
                    )
                    db.session.add(peer)
                    db.session.commit()

                    history = ConnectionHistory(
                        peer_id=peer.id,
                        event_type='creation',
                        details='Peer created'
                    )
                    db.session.add(history)
                    db.session.commit()

                    config_content = generate_client_config(peer, private_key)
                    qr_image = generate_qr_image(config_content)

                    success, msg, restarted = ConfigService.on_peer_change('create')
                    if not success:
                        flash(f'Peer created but error updating config: {msg}', 'warning')
                    elif restarted:
                        flash(f'Peer created and tunnel restarted automatically', 'success')
                    else:
                        flash(f'Peer created. Configuration updated (manual restart pending)', 'success')

                    return render_template('peer_created.html',
                                         peer=peer,
                                         config=config_content,
                                         qr_image=qr_image)

            except Exception:
                flash('Error creating peer', 'error')

    network = get_setting('wg_network', '10.0.0.0/24')
    allowed_ips_default = get_setting('wg_allowed_ips', '')

    return render_template('create_peer.html', network=network, allowed_ips_default=allowed_ips_default)


@app.route('/peers/import', methods=['GET', 'POST'])
@login_required
def import_peer():
    """Import an existing peer."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        public_key = request.form.get('public_key', '').strip()
        assigned_ip = request.form.get('assigned_ip', '').strip()
        pre_shared_key = request.form.get('pre_shared_key', '').strip() or None

        if not name or not public_key or not assigned_ip:
            flash('Name, public key, and IP are required', 'error')
            return redirect(url_for('import_peer'))

        try:
            from wggui.database import validate_peer_name
            name = validate_peer_name(name)
        except ValueError as e:
            flash(str(e), 'error')
            return redirect(url_for('import_peer'))
        except Exception:
            flash('Peer name is invalid', 'error')
            return redirect(url_for('import_peer'))

        try:
            import ipaddress
            ipaddress.ip_address(assigned_ip)
        except ValueError:
            flash('Invalid IP', 'error')
            return redirect(url_for('import_peer'))

        existing = Peer.query.filter(
            (Peer.public_key == public_key) | (Peer.assigned_ip == assigned_ip)
        ).first()

        if existing:
            flash('Public key or IP already in use', 'error')
            return redirect(url_for('import_peer'))

        peer = Peer(
            name=name,
            public_key=public_key,
            pre_shared_key=pre_shared_key,
            assigned_ip=assigned_ip
        )
        db.session.add(peer)
        db.session.commit()

        success, msg, restarted = ConfigService.on_peer_change('create')
        if not success:
            flash(f'Peer imported but error updating config: {msg}', 'warning')
        elif restarted:
            flash(f'Peer imported and tunnel restarted automatically', 'success')
        else:
            flash(f'Peer imported correctly. Configuration updated.', 'success')

        return redirect(url_for('peers'))

    return render_template('import_peer.html')


@app.route('/peers/<int:peer_id>/download')
@login_required
def download_config(peer_id):
    """Download client configuration file - only available during peer creation."""
    flash('Configuration only available when creating peer. Private key is not stored for security.', 'error')
    return redirect(url_for('peers'))


@app.route('/peers/<int:peer_id>/qr')
@login_required
def peer_qr(peer_id):
    """Show QR code for peer configuration - only available during peer creation."""
    flash('QR code only available when creating peer. Private key is not stored for security.', 'error')
    return redirect(url_for('peers'))


@app.route('/peers/<int:peer_id>/toggle')
@login_required
def toggle_peer(peer_id):
    """Enable or disable a peer."""
    peer = Peer.query.get_or_404(peer_id)

    peer.status = 'enabled' if peer.status == 'disabled' else 'disabled'
    db.session.commit()

    status_text = 'habilitado' if peer.status == 'enabled' else 'deshabilitado'
    
    # Regenerar config del servidor
    success, msg, restarted = ConfigService.on_peer_change('modify')
    if not success:
        flash(f'Peer "{peer.name}" {status_text} but error updating config: {msg}', 'warning')
    elif restarted:
        flash(f'Peer "{peer.name}" {status_text} and tunnel restarted automatically', 'success')
    else:
        flash(f'Peer "{peer.name}" {status_text}. Configuration updated.', 'success')

    return redirect(url_for('peers'))


@app.route('/peers/<int:peer_id>/delete')
@login_required
def delete_peer(peer_id):
    """Delete a peer."""
    peer = Peer.query.get_or_404(peer_id)

    # Delete related history
    ConnectionHistory.query.filter_by(peer_id=peer_id).delete()

    name = peer.name
    db.session.delete(peer)
    db.session.commit()

    # Regenerar config del servidor
    success, msg, restarted = ConfigService.on_peer_change('delete')
    if not success:
        flash(f'Peer "{name}" deleted but error updating config: {msg}', 'warning')
    elif restarted:
        flash(f'Peer "{name}" deleted and tunnel restarted automatically', 'success')
    else:
        flash(f'Peer "{name}" deleted. Configuration updated.', 'success')
    
    return redirect(url_for('peers'))


# ============== SETTINGS PAGES ==============

@app.route('/settings/wireguard', methods=['GET', 'POST'])
@login_required
def settings_wireguard():
    """WireGuard settings page."""
    if request.method == 'POST':
        try:
            from wggui.database import (
                validate_interface, validate_tunnel_name, validate_port,
                validate_network, validate_allowed_ips, validate_dns,
                validate_endpoint_host, validate_config_path
            )

            set_setting('wg_interface', validate_interface(request.form.get('wg_interface', 'wg0')))
            set_setting('wg_tunnel_name', validate_tunnel_name(request.form.get('wg_tunnel_name', 'wg0')))
            set_setting('wg_listen_port', validate_port(request.form.get('wg_listen_port', '51820')))
            set_setting('wg_network', validate_network(request.form.get('wg_network', '10.0.0.0/24')))
            set_setting('wg_allowed_ips', validate_allowed_ips(request.form.get('wg_allowed_ips', '')))
            set_setting('wg_dns', validate_dns(request.form.get('wg_dns', '')))
            set_setting('wg_endpoint_host', validate_endpoint_host(request.form.get('wg_endpoint_host', '')))
            set_setting('wg_endpoint_port', validate_port(request.form.get('wg_endpoint_port', '51820')))
            set_setting('server_config_path', validate_config_path(request.form.get('server_config_path', '')))
            set_setting('auto_restart_tunnel', 'True' if request.form.get('auto_restart_tunnel') else 'False')

            flash('Settings saved', 'success')
            return redirect(url_for('settings_wireguard'))

        except ValueError as e:
            flash(str(e), 'error')
            return redirect(url_for('settings_wireguard'))

    settings_dict = {s.key: s.value for s in Settings.query.all()}
    return render_template('settings/wireguard.html', settings=settings_dict)


@app.route('/settings/server-keys', methods=['GET', 'POST'])
@login_required
def settings_server_keys():
    """Server keys settings page."""
    settings_dict = {s.key: s.value for s in Settings.query.all()}
    return render_template('settings/server_keys.html', settings=settings_dict)


@app.route('/settings/tunnel')
@login_required
def settings_tunnel():
    """Tunnel control settings page."""
    settings_dict = {s.key: s.value for s in Settings.query.all()}
    return render_template('settings/tunnel.html', settings=settings_dict)


@app.route('/settings/telegram', methods=['GET', 'POST'])
@login_required
def settings_telegram():
    """Telegram settings page."""
    if request.method == 'POST':
        try:
            from wggui.database import (
                validate_telegram_bot_token, validate_telegram_chat_id, validate_positive_int
            )

            set_setting('telegram_enabled', 'True' if request.form.get('telegram_enabled') else 'False')
            set_setting('telegram_bot_token', validate_telegram_bot_token(request.form.get('telegram_bot_token', '')))
            set_setting('telegram_chat_id', validate_telegram_chat_id(request.form.get('telegram_chat_id', '')))
            set_setting('telegram_expire_seconds', validate_positive_int(request.form.get('telegram_expire_seconds', '300'), 'Expire seconds'))
            set_setting('telegram_message_template', request.form.get('telegram_message_template', ''))

            flash('Telegram settings saved', 'success')
            return redirect(url_for('settings_telegram'))

        except ValueError as e:
            flash(str(e), 'error')
            return redirect(url_for('settings_telegram'))

    settings_dict = {s.key: s.value for s in Settings.query.all()}
    return render_template('settings/telegram.html', settings=settings_dict)


@app.route('/settings/refresh', methods=['GET', 'POST'])
@login_required
def settings_refresh():
    """Refresh settings page."""
    if request.method == 'POST':
        try:
            from wggui.database import validate_positive_int, validate_timezone

            refresh_interval = validate_positive_int(request.form.get('refresh_interval', '30'), 'Refresh interval')
            disconnect_timeout = validate_positive_int(request.form.get('disconnect_timeout', '600'), 'Disconnect timeout')
            timezone_val = validate_timezone(request.form.get('timezone', 'Europe/Madrid'))

            set_setting('refresh_interval', refresh_interval)
            set_setting('disconnect_timeout', disconnect_timeout)
            set_setting('timezone', timezone_val)

            success, msg = update_scheduler_interval(app, int(refresh_interval))
            if success:
                flash(f'Refresh settings saved. {msg}', 'success')
            else:
                flash(f'Settings saved but error updating scheduler: {msg}', 'warning')

            return redirect(url_for('settings_refresh'))

        except ValueError as e:
            flash(str(e), 'error')
            return redirect(url_for('settings_refresh'))

    settings_dict = {s.key: s.value for s in Settings.query.all()}
    return render_template('settings/refresh.html', settings=settings_dict)


@app.route('/settings/export-import')
@login_required
def settings_export_import():
    """Export/Import settings page."""
    settings_dict = {s.key: s.value for s in Settings.query.all()}
    return render_template('settings/export_import.html', settings=settings_dict)


# Keep old settings route for backward compatibility
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Settings page - redirects to wireguard settings."""
    return redirect(url_for('settings_wireguard'))


@app.route('/settings/generate-keys', methods=['POST'])
@login_required
def generate_keys():
    """Generate server keys."""
    success, msg = generate_and_store_server_keys()
    if success:
        # Regenerar config y reiniciar túnel
        restart_success, restart_msg = ConfigService.restart_tunnel()
        if restart_success:
            flash(f'{msg} Túnel reiniciado correctamente.', 'success')
        else:
            flash(f'{msg} Error al reiniciar túnel: {restart_msg}', 'warning')
    else:
        flash(msg, 'success' if success else 'error')
    return redirect(url_for('settings_server_keys'))


@app.route('/settings/import-keys', methods=['POST'])
@login_required
def import_keys():
    """Import existing server keys."""
    private_key = request.form.get('private_key', '').strip()
    public_key = request.form.get('public_key', '').strip()

    if not private_key or not public_key:
        flash('Both keys are required', 'error')
    else:
        success, msg = import_and_store_server_keys(private_key, public_key)
        if success:
            restart_success, restart_msg = ConfigService.restart_tunnel()
            if restart_success:
                flash(f'{msg} Tunnel restarted correctly.', 'success')
            else:
                flash(f'{msg} Error restarting tunnel: {restart_msg}', 'warning')
        else:
            flash(msg, 'error')

    return redirect(url_for('settings_server_keys'))


@app.route('/settings/restart-tunnel', methods=['POST'])
@login_required
def restart_tunnel_route():
    """Restart the WireGuard tunnel."""
    success, msg = ConfigService.restart_tunnel()
    flash(msg, 'success' if success else 'error')
    return redirect(url_for('index'))


@app.route('/settings/stop-tunnel', methods=['POST'])
@login_required
def stop_tunnel_route():
    """Stop the WireGuard tunnel."""
    from wggui.system import is_wg_quick_installed, get_wg_install_instructions
    if not is_wg_quick_installed():
        flash(get_wg_install_instructions(), 'error')
        return redirect(url_for('index'))

    tunnel_name = get_setting('wg_tunnel_name', 'wg0')
    result = subprocess.run(['wg-quick', 'down', tunnel_name], capture_output=True, text=True)
    if result.returncode != 0:
        flash(f'Error stopping tunnel: {result.stderr}', 'error')
    else:
        flash(f'Tunnel {tunnel_name} stopped correctly', 'success')
    return redirect(url_for('index'))


@app.route('/settings/start-tunnel', methods=['POST'])
@login_required
def start_tunnel_route():
    """Start the WireGuard tunnel."""
    from wggui.system import is_wg_quick_installed, get_wg_install_instructions
    if not is_wg_quick_installed():
        flash(get_wg_install_instructions(), 'error')
        return redirect(url_for('index'))

    tunnel_name = get_setting('wg_tunnel_name', 'wg0')
    result = subprocess.run(['wg-quick', 'up', tunnel_name], capture_output=True, text=True)
    if result.returncode != 0:
        flash(f'Error starting tunnel: {result.stderr}', 'error')
    else:
        flash(f'Tunnel {tunnel_name} started correctly', 'success')
    return redirect(url_for('index'))


@app.route('/settings/test-telegram', methods=['POST'])
@login_required
def test_telegram():
    """Test Telegram connection."""
    success, msg = test_telegram_connection()
    return jsonify({'success': success, 'message': msg})


@app.route('/settings/test-telegram-direct', methods=['POST'])
@login_required
def test_telegram_direct():
    """
    Test Telegram connection with direct JSON input.
    This endpoint accepts JSON data directly and validates the template
    before saving and testing the connection. Fixes the 302 redirect issue.
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'success': False, 'message': 'No se recibió datos JSON'}), 400

        bot_token = data.get('bot_token', '').strip()
        chat_id = data.get('chat_id', '').strip()
        message_template = data.get('message_template', '')

        # Validate required fields
        if not bot_token:
            return jsonify({'success': False, 'message': 'Bot token es obligatorio'}), 400
        if not chat_id:
            return jsonify({'success': False, 'message': 'Chat ID es obligatorio'}), 400

        # Validate template variables for security
        if message_template:
            is_valid, error_msg = validate_template_variables(message_template)
            if not is_valid:
                return jsonify({'success': False, 'message': error_msg}), 400

        bot_token = data.get('bot_token', '').strip()
        chat_id = data.get('chat_id', '').strip()
        message_template = data.get('message_template', '')

        try:
            from wggui.database import validate_telegram_bot_token, validate_telegram_chat_id
            set_setting('telegram_bot_token', validate_telegram_bot_token(bot_token))
            set_setting('telegram_chat_id', validate_telegram_chat_id(chat_id))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        set_setting('telegram_message_template', message_template)
        set_setting('telegram_enabled', 'True')

        # Test Telegram connection with the saved credentials
        success, msg = test_telegram_connection()

        return jsonify({
            'success': success,
            'message': msg,
            'saved': True
        })

    except Exception as e:
        logging.error(f"Error in test_telegram_direct: {e}")
        return jsonify({'success': False, 'message': 'Internal error'}), 500


# ============== EXPORT/IMPORT ==============

@app.route('/settings/export')
@login_required
def export_config():
    """Export configuration to JSON."""
    peers = [p.to_dict() for p in Peer.query.all()]

    # Get all settings
    settings_dict = {s.key: s.value for s in Settings.query.all()}

    export_data = {
        'version': '1.0',
        'exported_at': datetime.utcnow().isoformat(),
        'settings': settings_dict,
        'peers': peers
    }

    buffer = io.BytesIO()
    buffer.write(json.dumps(export_data, indent=2).encode('utf-8'))
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'wireguard_backup_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json',
        mimetype='application/json'
    )


@app.route('/settings/import', methods=['POST'])
@login_required
def import_config():
    """Import configuration from JSON."""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('settings'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('settings'))

    try:
        data = json.load(file)

        from wggui.database import (
            validate_interface, validate_tunnel_name, validate_port,
            validate_network, validate_allowed_ips, validate_dns,
            validate_endpoint_host, validate_config_path,
            validate_peer_name, validate_timezone, set_setting
        )

        settings_validators = {
            'wg_interface': validate_interface,
            'wg_tunnel_name': validate_tunnel_name,
            'wg_listen_port': validate_port,
            'wg_network': validate_network,
            'wg_allowed_ips': validate_allowed_ips,
            'wg_dns': validate_dns,
            'wg_endpoint_host': validate_endpoint_host,
            'wg_endpoint_port': validate_port,
            'server_config_path': validate_config_path,
            'timezone': validate_timezone,
        }

        if 'settings' in data:
            for key, value in data['settings'].items():
                if key in settings_validators:
                    try:
                        validated_value = settings_validators[key](value)
                        set_setting(key, validated_value)
                    except ValueError as e:
                        flash(f'Invalid setting "{key}": {e}', 'error')
                        return redirect(url_for('settings'))
                else:
                    set_setting(key, value)

        if 'peers' in data:
            for peer_data in data['peers']:
                try:
                    name = validate_peer_name(peer_data.get('name', ''))
                    allowed_ips = peer_data.get('allowed_ips')
                    if allowed_ips:
                        allowed_ips = validate_allowed_ips(allowed_ips)
                    status = peer_data.get('status', 'enabled')
                    if status not in ('enabled', 'disabled'):
                        flash(f'Invalid peer status "{status}": must be "enabled" or "disabled"', 'error')
                        return redirect(url_for('settings'))
                except ValueError as e:
                    flash(f'Invalid peer data: {e}', 'error')
                    return redirect(url_for('settings'))

                peer = Peer(
                    name=name,
                    public_key=peer_data['public_key'],
                    pre_shared_key=peer_data.get('pre_shared_key'),
                    assigned_ip=peer_data['assigned_ip'],
                    allowed_ips=allowed_ips,
                    status=status
                )
                db.session.add(peer)

        db.session.commit()

        # Regenerar config del servidor después de importar
        success, msg, restarted = ConfigService.on_peer_change('create')
        if not success:
            flash(f'Configuration imported but error updating config: {msg}', 'warning')
        elif restarted:
            flash('Configuration imported and tunnel restarted automatically', 'success')
        else:
            flash('Configuration imported correctly. Configuration updated.', 'success')

    except Exception as e:
        logging.error(f"Error importing config: {e}")
        flash('Error importing configuration', 'error')

    return redirect(url_for('settings'))


# ============== API ==============

@app.route('/api/stats')
@login_required
def api_stats():
    """API endpoint for dashboard stats."""
    peers = Peer.query.all()
    connected = [p for p in peers if p.last_handshake and
                (datetime.utcnow() - p.last_handshake).total_seconds() < 120]

    return jsonify({
        'total_peers': len(peers),
        'enabled_peers': len([p for p in peers if p.status == 'enabled']),
        'connected_now': len(connected),
    })


@app.route('/api/telegram-vars')
@login_required
def api_telegram_vars():
    """API endpoint for Telegram template variables help."""
    return jsonify({'help': format_telegram_variables_help()})


@app.route('/api/notifications')
@login_required
def api_notifications():
    """API endpoint for recent notifications."""
    # Get recent connection history
    recent = ConnectionHistory.query.order_by(
        ConnectionHistory.timestamp.desc()
    ).limit(10).all()
    
    notifications = []
    for h in recent:
        peer = Peer.query.get(h.peer_id)
        notifications.append({
            'id': h.id,
            'type': h.event_type,
            'peer_name': peer.name if peer else 'Unknown',
            'timestamp': h.timestamp.isoformat(),
            'details': h.details,
            'time_ago': get_time_ago(h.timestamp)
        })
    
    # Count unread (last 24 hours)
    from datetime import timedelta
    unread_count = ConnectionHistory.query.filter(
        ConnectionHistory.timestamp > datetime.utcnow() - timedelta(hours=24)
    ).count()
    
    return jsonify({
        'notifications': notifications,
        'unread_count': unread_count
    })


def get_time_ago(dt):
    """Return a human-readable time ago string."""
    if not dt:
        return 'Unknown'
    
    now = datetime.utcnow()
    diff = now - dt
    
    seconds = diff.total_seconds()
    if seconds < 60:
        return 'Hace menos de un minuto'
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f'Hace {minutes} minuto{"s" if minutes > 1 else ""}'
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f'Hace {hours} hora{"s" if hours > 1 else ""}'
    else:
        days = int(seconds / 86400)
        return f'Hace {days} día{"s" if days > 1 else ""}'


# ============== EVENTS PAGE ==============

@app.route('/events')
@login_required
def events():
    """Events history page with date filtering."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')

    query = ConnectionHistory.query

    if date_from:
        try:
            from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(ConnectionHistory.timestamp >= from_dt)
        except ValueError:
            pass

    if date_to:
        try:
            to_dt = datetime.strptime(date_to, '%Y-%m-%d')
            to_dt = to_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(ConnectionHistory.timestamp <= to_dt)
        except ValueError:
            pass

    total = query.count()
    events_list = query.order_by(ConnectionHistory.timestamp.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return render_template('events.html',
                          events=events_list,
                          page=page,
                          per_page=per_page,
                          total=total,
                          date_from=date_from,
                          date_to=date_to)


# ============== STATS PAGE ==============

@app.route('/stats')
@login_required
def stats():
    """Statistics page with charts."""
    from datetime import timedelta

    today = datetime.utcnow().date()
    thirty_days_ago = today - timedelta(days=30)
    seven_days_ago = today - timedelta(days=7)

    # Histogram: unique users per day (last 30 days)
    histogram_labels = []
    histogram_values = []
    for i in range(29, -1, -1):
        day = today - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())

        peers_that_day = db.session.query(ConnectionHistory.peer_id).filter(
            ConnectionHistory.timestamp >= day_start,
            ConnectionHistory.timestamp <= day_end,
            ConnectionHistory.event_type == 'connection'
        ).distinct().count()

        histogram_labels.append(day.strftime('%m-%d'))
        histogram_values.append(peers_that_day)

    # Pie: IPs (last 7 days)
    seven_days_ago_dt = datetime.combine(seven_days_ago, datetime.min.time())
    ip_stats = db.session.query(
        ConnectionHistory.endpoint_ip,
        db.func.count(ConnectionHistory.id).label('count')
    ).filter(
        ConnectionHistory.timestamp >= seven_days_ago_dt,
        ConnectionHistory.endpoint_ip.isnot(None),
        ConnectionHistory.endpoint_ip != ''
    ).group_by(ConnectionHistory.endpoint_ip).order_by(db.desc('count')).limit(5).all()

    pie_labels = [ip or 'Unknown' for ip, _ in ip_stats]
    pie_values = [count for _, count in ip_stats]

    # Table: latest connections by IP-User
    connections_query = db.session.query(
        ConnectionHistory.endpoint_ip,
        ConnectionHistory.peer_id,
        db.func.max(ConnectionHistory.timestamp).label('last_connection')
    ).filter(
        ConnectionHistory.timestamp >= seven_days_ago_dt,
        ConnectionHistory.endpoint_ip.isnot(None),
        ConnectionHistory.endpoint_ip != ''
    ).group_by(
        ConnectionHistory.endpoint_ip,
        ConnectionHistory.peer_id
    ).order_by(
        db.desc('last_connection')
    ).limit(30).all()

    connections_table = []
    for endpoint_ip, peer_id, last_conn in connections_query:
        peer = Peer.query.get(peer_id)
        if peer:
            connections_table.append({
                'ip': endpoint_ip or 'Unknown',
                'peer_name': peer.name,
                'assigned_ip': peer.assigned_ip,
                'last_connection': last_conn
            })

    histogram_data = json.dumps({
        'labels': histogram_labels,
        'values': histogram_values
    })
    pie_data = json.dumps({
        'labels': pie_labels,
        'values': pie_values
    })

    return render_template('stats.html',
                          histogram_data=histogram_data,
                          pie_data=pie_data,
                          connections_table=connections_table)


# ============== ERROR HANDLERS ==============

@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
