"""
Servicio centralizado para gestión del archivo de configuración de WireGuard.

Este módulo se encarga de:
- Generar el archivo de configuración del servidor desde la BD
- Escribir el archivo a disco
- Reiniciar el túnel cuando hay cambios (si auto-restart está activado)
"""

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime

from .database import get_setting, db, Peer
from .wireguard import generate_server_config
from .system import is_wg_installed, is_wg_quick_installed, get_wg_install_instructions

logger = logging.getLogger(__name__)


class ConfigService:
    """Servicio centralizado para gestión del config de WireGuard."""
    
    _config_cache = None
    _cache_valid = False
    _last_config_hash = None
    
    @classmethod
    def mark_dirty(cls):
        """Invalidar cache cuando hay cambios."""
        cls._cache_valid = False
    
    @classmethod
    def generate_server_config(cls):
        """Generar contenido del config del servidor."""
        return generate_server_config()
    
    @classmethod
    def get_config_path(cls):
        """Obtener la ruta del archivo de configuración."""
        tunnel_name = get_setting('wg_tunnel_name', 'wg0')
        return get_setting('server_config_path', f'/etc/wireguard/{tunnel_name}.conf')
    
    @classmethod
    def backup_existing_config(cls):
        """Crear backup del archivo de configuración actual.
        
        Returns:
            tuple: (success: bool, backup_path: str or None, message: str)
        """
        config_path = cls.get_config_path()
        
        # Verificar si existe el archivo actual
        if not os.path.exists(config_path):
            return True, None, "No existe archivo actual, no se requiere backup"
        
        try:
            # Crear ruta de la carpeta de backups
            config_dir = os.path.dirname(config_path)
            backup_dir = os.path.join(config_dir, 'backups')
            
            # Crear directorio de backups si no existe
            os.makedirs(backup_dir, exist_ok=True)
            
            # Generar nombre del archivo de backup con timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            config_filename = os.path.basename(config_path)
            backup_filename = f"{config_filename}.{timestamp}.backup"
            backup_path = os.path.join(backup_dir, backup_filename)
            
            # Copiar archivo existente a backup
            shutil.copy2(config_path, backup_path)
            
            logger.info(f"Backup creado: {backup_path}")
            return True, backup_path, f"Backup creado: {backup_path}"
            
        except PermissionError as e:
            error_msg = f"Error de permisos al crear backup: {e}"
            logger.error(error_msg)
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Error al crear backup: {e}"
            logger.error(error_msg)
            return False, None, error_msg
    
    @classmethod
    def write_config_file(cls, config_content=None):
        """Escribir el archivo de configuración a disco.
        
        Args:
            config_content: Contenido del config. Si es None, se genera automáticamente.
            
        Returns:
            tuple: (success: bool, message: str)
        """
        if config_content is None:
            config_content = cls.generate_server_config()
        
        config_path = cls.get_config_path()

        try:
            from .database import validate_config_path
            validate_config_path(config_path)
        except ValueError as e:
            error_msg = f"Config path validation failed: {e}"
            logger.error(error_msg)
            return False, error_msg

        try:
            # CREAR BACKUP DEL ARCHIVO EXISTENTE ANTES DE SOBRESCRIBIR
            backup_success, _, backup_msg = cls.backup_existing_config()
            if not backup_success:
                logger.warning(f"No se pudo crear backup: {backup_msg}")
            else:
                logger.info(backup_msg)
            
            # Crear directorio si no existe
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            # Escribir archivo temporal primero
            temp_path = f"{config_path}.tmp.{int(time.time())}"
            with open(temp_path, 'w') as f:
                f.write(config_content)
            
            # Renombrar a archivo final (operación atómica en POSIX)
            os.rename(temp_path, config_path)
            
            logger.info(f"Archivo de configuración escrito: {config_path}")
            return True, f"Configuración escrita en {config_path}"
            
        except PermissionError as e:
            error_msg = f"Error de permisos al escribir config: {e}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Error al escribir config: {e}"
            logger.error(error_msg)
            return False, error_msg
    
    @classmethod
    def generate_and_write_config(cls):
        """Generar el config del servidor y escribirlo a disco.
        
        Returns:
            tuple: (success: bool, message: str)
        """
        config_content = cls.generate_server_config()
        return cls.write_config_file(config_content)
    
    @classmethod
    def restart_tunnel(cls):
        """Reiniciar el túnel WireGuard usando wg-quick.

        Returns:
            tuple: (success: bool, message: str)
        """
        if not is_wg_quick_installed():
            return False, get_wg_install_instructions()

        tunnel_name = get_setting('wg_tunnel_name', 'wg0')
        
        # Primero regenerar el config
        success, msg = cls.generate_and_write_config()
        if not success:
            return False, f"Error generando config: {msg}"
        
        # Bring down the tunnel
        logger.info(f"Deteniendo túnel {tunnel_name}...")
        down_result = subprocess.run(
            ['wg-quick', 'down', tunnel_name],
            capture_output=True,
            text=True
        )
        
        # Wait a moment
        time.sleep(1)
        
        # Bring up the tunnel
        logger.info(f"Iniciando túnel {tunnel_name}...")
        up_result = subprocess.run(
            ['wg-quick', 'up', tunnel_name],
            capture_output=True,
            text=True
        )
        
        if up_result.returncode != 0:
            error_msg = f"Error iniciando túnel: {up_result.stderr}"
            logger.error(error_msg)
            return False, error_msg
        
        msg = f"Túnel {tunnel_name} reiniciado correctamente"
        logger.info(msg)
        return True, msg
    
    @classmethod
    def should_auto_restart(cls):
        """Verificar si auto-restart está activado."""
        return get_setting('auto_restart_tunnel', 'False') == 'True'
    
    @classmethod
    def on_peer_change(cls, action='modify', restart_tunnel_if_needed=True):
        """Called cuando hay cambios en peers.
        
        Args:
            action: 'create', 'modify', 'delete'
            restart_tunnel_if_needed: Si True, reinicia el túnel según configuración
            
        Returns:
            tuple: (success: bool, message: str, restarted: bool)
        """
        logger.info(f"Peer {action}: regenerando configuración...")
        
        # Invalidar cache
        cls.mark_dirty()
        
        # Generar y escribir config
        success, msg = cls.generate_and_write_config()
        
        if not success:
            return False, msg, False
        
        # Reiniciar túnel si está configurado
        restarted = False
        if restart_tunnel_if_needed and cls.should_auto_restart():
            logger.info("Auto-restart activado, reiniciando túnel...")
            success, msg = cls.restart_tunnel()
            if success:
                restarted = True
        
        return True, msg, restarted
    
    @classmethod
    def check_tunnel_status(cls):
        """Verificar si el túnel WireGuard está activo.

        Returns:
            tuple: (active: bool, message: str)
        """
        if not is_wg_installed():
            return False, get_wg_install_instructions()

        tunnel_name = get_setting('wg_tunnel_name', 'wg0')
        
        result = subprocess.run(
            ['wg', 'show', tunnel_name],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True, "Tunnel is active"
        else:
            return False, f"Tunnel {tunnel_name} not found or not active"
    
    @classmethod
    def apply_config_to_running_tunnel(cls):
        """Aplicar cambios al túnel en ejecución sin reiniciar.
        
        Utiliza 'wg sync' para aplicar cambios sin downtime.
        Si falla, hace restart completo.
        
        Returns:
            tuple: (success: bool, message: str)
        """
        tunnel_name = get_setting('wg_tunnel_name', 'wg0')
        config_path = cls.get_config_path()
        
        # Intentar sync primero (más rápido, sin downtime)
        try:
            result = subprocess.run(
                ['wg', 'sync', 'all', config_path],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info("Config sincronizada con éxito (sync)")
                return True, "Configuración sincronizada"
            
            # Si sync falla, intentar método alternativo
            logger.warning(f"sync failed, trying add: {result.stderr}")
            
        except Exception as e:
            logger.warning(f"sync not available: {e}")
        
        # Fallback a restart completo
        return cls.restart_tunnel()
