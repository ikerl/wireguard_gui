from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
from .database import Peer, ConnectionHistory, get_setting, db
from .telegram import notify_connected_peers, notify_disconnected_peers
from .wireguard import update_peer_handshakes, parse_wg_show_dump


# Global scheduler instance
_scheduler = None


def get_scheduler():
    """Get the global scheduler instance."""
    return _scheduler


def update_scheduler_interval(app, new_interval=None):
    """Update the scheduler interval dynamically.
    
    Args:
        app: Flask application instance
        new_interval: New interval in seconds. If None, reads from database.
    
    Returns:
        tuple: (success: bool, message: str)
    """
    global _scheduler
    
    if _scheduler is None:
        return False, "Scheduler not initialized"
    
    try:
        if new_interval is None:
            new_interval = int(get_setting('refresh_interval', '30'))
        
        # Remove existing job and add new one with updated interval
        _scheduler.remove_job('refresh_peer_statuses')
        _scheduler.add_job(
            func=lambda: refresh_peer_statuses(app),
            trigger='interval',
            seconds=new_interval,
            id='refresh_peer_statuses',
            replace_existing=True
        )
        
        print(f"[SCHEDULER] Interval updated to {new_interval} seconds")
        return True, f"Scheduler interval updated to {new_interval} seconds"
    
    except Exception as e:
        print(f"[SCHEDULER] Error updating interval: {e}")
        return False, f"Error updating scheduler: {e}"


def refresh_peer_statuses(app):
    """Background task to refresh peer statuses."""
    with app.app_context():
        try:
            # Get disconnect timeout setting
            disconnect_timeout = int(get_setting('disconnect_timeout', '600'))
            now = datetime.now(timezone.utc)
            
            # DEBUG: Compare timezones
            now_utc_naive = datetime.utcnow()
            print(f"[DEBUG] datetime.now(timezone.utc): {now}")
            print(f"[DEBUG] datetime.utcnow() (naive): {now_utc_naive}")
            print(f"[DEBUG] disconnect_timeout: {disconnect_timeout}")

            # Update handshakes from wg show all dump
            updated_peers, error = update_peer_handshakes()
            if error:
                print(f"Error updating peer handshakes: {error}")

            # Get all peers from database
            all_peers = Peer.query.all()

            # Parse current connection status from wg show
            peers_status, parse_error = parse_wg_show_dump()
            if parse_error:
                print(f"Error parsing wg show: {parse_error}")
                return

            # Determine which peers are currently connected based on handshake time
            currently_connected_keys = set()
            for peer_key, status in peers_status.items():
                try:
                    handshake_ts_raw = status['latest_handshake']
                    handshake_ts = int(handshake_ts_raw)
                    
                    # DEBUG: Log timestamp diagnosis
                    print(f"[DEBUG] Peer key: {peer_key[:20]}...")
                    print(f"[DEBUG] handshake_ts raw: {handshake_ts_raw} -> int: {handshake_ts}")
                    
                    if handshake_ts > 0:
                        # Use timezone-aware datetime for consistent comparison
                        handshake_dt = datetime.fromtimestamp(handshake_ts, tz=timezone.utc)
                        time_diff = (now - handshake_dt).total_seconds()
                        
                        print(f"[DEBUG] handshake_dt (fromtimestamp, tz-aware): {handshake_dt}")
                        print(f"[DEBUG] time_diff: {time_diff}")
                        print(f"[DEBUG] is_connected (time_diff < timeout): {time_diff < disconnect_timeout}")
                        
                        if time_diff < disconnect_timeout:
                            currently_connected_keys.add(peer_key)
                            print(f"[DEBUG] -> Added to connected keys")
                    else:
                        print(f"[DEBUG] handshake_ts is 0, peer not connected")
                except (ValueError, TypeError) as e:
                    print(f"[DEBUG] Error processing peer {peer_key[:20]}...: {e}")
                    continue

            # DEBUG: Log connected keys
            print(f"[DEBUG] Connected keys count: {len(currently_connected_keys)}")
            print(f"[DEBUG] Connected keys: {list(k[:20] + '...' for k in currently_connected_keys)}")

            # Track disconnections and connections
            disconnected_peers = []
            newly_connected = []
            for peer in all_peers:
                # Determine if peer is currently connected based on handshake time
                is_now_connected = peer.public_key in currently_connected_keys
                # Was connected = had connection_notified flag set
                was_connected = peer.connection_notified

                # DEBUG: Log each peer status with last_handshake value
                last_handshake_val = peer.last_handshake
                elapsed_since_handshake = (datetime.utcnow() - last_handshake_val).total_seconds() if last_handshake_val else None
                print(f"[DEBUG] Peer {peer.name}: was_connected={was_connected}, is_now_connected={is_now_connected}, last_handshake={last_handshake_val}, elapsed={elapsed_since_handshake}s")

                if was_connected and not is_now_connected:
                    # Peer disconnected - log the event
                    print(f"[DEBUG] Peer {peer.name} disconnected")
                    history = ConnectionHistory(
                        peer_id=peer.id,
                        event_type='disconnection',
                        details='Sesión expirada por inactividad',
                        endpoint_ip=peer.endpoint_ip
                    )
                    db.session.add(history)
                    peer.connection_notified = False
                    peer.last_disconnection = datetime.now(timezone.utc)
                    # Add to list for Telegram notification
                    disconnected_peers.append(peer)
                elif not was_connected and is_now_connected:
                    # Peer newly connected - log the event
                    print(f"[DEBUG] Peer {peer.name} newly connected")
                    history = ConnectionHistory(
                        peer_id=peer.id,
                        event_type='connection',
                        details='Sesión establecida',
                        endpoint_ip=peer.endpoint_ip
                    )
                    db.session.add(history)
                    # Update last_connection to persist the last connection date
                    peer.last_connection = datetime.now(timezone.utc)
                    # Set connection_notified for future comparison
                    peer.connection_notified = True
                    newly_connected.append(peer)
                elif was_connected and is_now_connected:
                    # Still connected, ensure flag is set
                    print(f"[DEBUG] Peer {peer.name} still connected")
                    peer.connection_notified = True
                elif not was_connected and not is_now_connected:
                    # Still disconnected, ensure flag is not set
                    peer.connection_notified = False

            db.session.commit()

            # DEBUG: Log notification status
            print(f"[DEBUG] Disconnected peers: {len(disconnected_peers)}")
            print(f"[DEBUG] Newly connected peers: {len(newly_connected)}")

            # Build peers_state dict for should_notify_peer
            peers_state = {peer.name: peer.connection_notified for peer in all_peers}

            # Send Telegram notifications for disconnected peers
            # DISABLED: User does not want disconnect notifications
            # if disconnected_peers:
            #     print(f"[DEBUG] Sending disconnect notifications for {len(disconnected_peers)} peers")
            #     notify_disconnected_peers(disconnected_peers)

            # Send notifications for newly connected peers
            if newly_connected:
                print(f"[DEBUG] Sending connect notifications for {len(newly_connected)} peers")
                notify_connected_peers(newly_connected, peers_state)

        except Exception as e:
            print(f"Error refreshing peer statuses: {e}")


def start_scheduler(app):
    """Start the background scheduler."""
    global _scheduler
    
    interval = int(get_setting('refresh_interval', '30'))
    print(f"[SCHEDULER] Starting scheduler with refresh_interval: {interval} seconds")

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        func=lambda: refresh_peer_statuses(app),
        trigger='interval',
        seconds=interval,
        id='refresh_peer_statuses',
        replace_existing=True
    )
    _scheduler.start()
    print(f"[SCHEDULER] Scheduler started successfully")

    return _scheduler


def trigger_manual_refresh(app):
    """Trigger a manual refresh of peer statuses."""
    refresh_peer_statuses(app)
    return True, "Refresh completed"
