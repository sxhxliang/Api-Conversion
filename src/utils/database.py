"""
SQLite数据库管理器
用于存储渠道信息和系统配置
"""
import sqlite3
import os
import json
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime
from contextlib import contextmanager

from src.utils.logger import setup_logger
from src.utils.env_config import env_config
from src.utils.encryption import encryption_manager

logger = setup_logger("database")


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or env_config.database_path
        self._ensure_data_dir()
        self._init_database()
    
    def _ensure_data_dir(self):
        """确保数据目录存在"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 使结果可以通过列名访问
        try:
            yield conn
        finally:
            conn.close()
    
    def _init_database(self):
        """初始化数据库表"""
        with self.get_connection() as conn:
            # 创建渠道表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    custom_key TEXT UNIQUE NOT NULL,
                    timeout INTEGER DEFAULT 30,
                    max_retries INTEGER DEFAULT 3,
                    enabled BOOLEAN DEFAULT 1,
                    models_mapping TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            # 创建系统配置表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    def add_channel(
        self,
        name: str,
        provider: str,
        base_url: str,
        api_key: str,
        custom_key: str,
        timeout: int = 30,
        max_retries: int = 3,
        models_mapping: Optional[Dict[str, str]] = None
    ) -> str:
        """添加新渠道"""
        channel_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        models_mapping_json = json.dumps(models_mapping) if models_mapping else None
        
        # 验证API密钥不是明显的JavaScript错误信息
        if api_key.startswith('script.js:') or 'Uncaught TypeError' in api_key:
            logger.error(f"Rejecting JavaScript error message as API key: {api_key[:50]}...")
            raise ValueError("Invalid API key: JavaScript error message detected")
        
        # 加密API密钥
        encrypted_api_key = encryption_manager.encrypt_api_key(api_key)
        
        with self.get_connection() as conn:
            try:
                conn.execute('''
                    INSERT INTO channels 
                    (id, name, provider, base_url, api_key, custom_key, timeout, max_retries, 
                     enabled, models_mapping, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    channel_id, name, provider, base_url, encrypted_api_key, custom_key,
                    timeout, max_retries, True, models_mapping_json, now, now
                ))
                conn.commit()
                logger.info(f"Added new channel: {name} ({provider}) with ID: {channel_id}")
                return channel_id
            except sqlite3.IntegrityError as e:
                if "custom_key" in str(e):
                    raise ValueError(f"Custom key '{custom_key}' already exists")
                raise ValueError(f"Database integrity error: {e}")
    
    def update_channel(
        self,
        channel_id: str,
        name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        custom_key: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        enabled: Optional[bool] = None,
        models_mapping: Optional[Dict[str, str]] = None
    ) -> bool:
        """更新渠道信息"""
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if base_url is not None:
            updates.append("base_url = ?")
            params.append(base_url)
        if api_key is not None:
            # 验证API密钥不是明显的JavaScript错误信息
            if api_key.startswith('script.js:') or 'Uncaught TypeError' in api_key:
                logger.error(f"Rejecting JavaScript error message as API key: {api_key[:50]}...")
                raise ValueError("Invalid API key: JavaScript error message detected")
            
            updates.append("api_key = ?")
            # 加密API密钥
            encrypted_api_key = encryption_manager.encrypt_api_key(api_key)
            params.append(encrypted_api_key)
        if custom_key is not None:
            updates.append("custom_key = ?")
            params.append(custom_key)
        if timeout is not None:
            updates.append("timeout = ?")
            params.append(timeout)
        if max_retries is not None:
            updates.append("max_retries = ?")
            params.append(max_retries)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(enabled)
        if models_mapping is not None:
            updates.append("models_mapping = ?")
            params.append(json.dumps(models_mapping))
        
        if not updates:
            return False
        
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(channel_id)
        
        with self.get_connection() as conn:
            try:
                cursor = conn.execute(f'''
                    UPDATE channels 
                    SET {", ".join(updates)}
                    WHERE id = ?
                ''', params)
                
                if cursor.rowcount == 0:
                    return False
                
                conn.commit()
                logger.info(f"Updated channel: {channel_id}")
                return True
            except sqlite3.IntegrityError as e:
                if "custom_key" in str(e):
                    raise ValueError(f"Custom key '{custom_key}' already exists")
                raise ValueError(f"Database integrity error: {e}")
    
    def delete_channel(self, channel_id: str) -> bool:
        """删除渠道"""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            if cursor.rowcount == 0:
                return False
            
            conn.commit()
            logger.info(f"Deleted channel: {channel_id}")
            return True
    
    def get_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """获取渠道信息"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
            row = cursor.fetchone()
            
            if row:
                channel = dict(row)
                if channel['models_mapping']:
                    channel['models_mapping'] = json.loads(channel['models_mapping'])
                # 解密API密钥
                if channel['api_key']:
                    channel['api_key'] = encryption_manager.decrypt_api_key(channel['api_key'])
                return channel
            return None
    
    def get_channel_by_custom_key(self, custom_key: str) -> Optional[Dict[str, Any]]:
        """根据自定义key获取渠道信息"""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM channels WHERE custom_key = ? AND enabled = 1", 
                (custom_key,)
            )
            row = cursor.fetchone()
            
            if row:
                channel = dict(row)
                if channel['models_mapping']:
                    channel['models_mapping'] = json.loads(channel['models_mapping'])
                # 解密API密钥
                if channel['api_key']:
                    channel['api_key'] = encryption_manager.decrypt_api_key(channel['api_key'])
                return channel
            return None
    
    def get_all_channels(self) -> List[Dict[str, Any]]:
        """获取所有渠道"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM channels ORDER BY created_at DESC")
            channels = []
            for row in cursor.fetchall():
                channel = dict(row)
                if channel['models_mapping']:
                    channel['models_mapping'] = json.loads(channel['models_mapping'])
                # 解密API密钥
                if channel['api_key']:
                    channel['api_key'] = encryption_manager.decrypt_api_key(channel['api_key'])
                channels.append(channel)
            return channels
    
    def get_enabled_channels(self) -> List[Dict[str, Any]]:
        """获取所有启用的渠道"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM channels WHERE enabled = 1 ORDER BY created_at DESC")
            channels = []
            for row in cursor.fetchall():
                channel = dict(row)
                if channel['models_mapping']:
                    channel['models_mapping'] = json.loads(channel['models_mapping'])
                # 解密API密钥
                if channel['api_key']:
                    channel['api_key'] = encryption_manager.decrypt_api_key(channel['api_key'])
                channels.append(channel)
            return channels
    
    def get_channels_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        """按提供商获取渠道列表"""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM channels WHERE provider = ? AND enabled = 1 ORDER BY created_at DESC", 
                (provider,)
            )
            channels = []
            for row in cursor.fetchall():
                channel = dict(row)
                if channel['models_mapping']:
                    channel['models_mapping'] = json.loads(channel['models_mapping'])
                # 解密API密钥
                if channel['api_key']:
                    channel['api_key'] = encryption_manager.decrypt_api_key(channel['api_key'])
                channels.append(channel)
            return channels
    
    def set_config(self, key: str, value: str):
        """设置系统配置"""
        now = datetime.now().isoformat()
        
        with self.get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO system_config (key, value, created_at, updated_at)
                VALUES (?, ?, 
                    COALESCE((SELECT created_at FROM system_config WHERE key = ?), ?),
                    ?)
            ''', (key, value, key, now, now))
            conn.commit()
    
    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """获取系统配置"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT value FROM system_config WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else default
    
    def delete_config(self, key: str) -> bool:
        """删除系统配置"""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM system_config WHERE key = ?", (key,))
            conn.commit()
            return cursor.rowcount > 0
    
    def get_configs_by_prefix(self, prefix: str) -> List[Dict[str, str]]:
        """获取指定前缀的所有配置"""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE ?", 
                (f"{prefix}%",)
            )
            results = cursor.fetchall()
            return [{"key": row["key"], "value": row["value"]} for row in results]
    
    def has_encrypted_api_keys(self) -> bool:
        """检查数据库中是否存在加密的API密钥"""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) as count FROM channels WHERE api_key LIKE 'encrypted:%'"
                )
                row = cursor.fetchone()
                return row['count'] > 0 if row else False
        except Exception:
            # 如果表不存在或查询失败，返回False
            return False
    



# 全局数据库管理器实例
db_manager = DatabaseManager()
