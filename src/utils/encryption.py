"""
API密钥加密工具
使用AES加密确保数据库中API密钥的安全性
"""
import os
import base64
import secrets
from typing import Optional, Tuple
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.utils.logger import setup_logger
from src.utils.env_config import env_config

logger = setup_logger("encryption")


class APIKeyEncryption:
    """API密钥加密管理器"""
    
    def __init__(self):
        self._fernet = None
        # 获取数据库类型
        from src.utils.env_config import env_config
        self.db_type = env_config.database_type
        self._init_encryption_key()
    
    def _init_encryption_key(self):
        """初始化加密密钥"""
        # 1. 优先使用环境变量中的密钥
        encryption_key = os.getenv('ENCRYPTION_KEY')
        if encryption_key:
            logger.info("Using ENCRYPTION_KEY from environment variables")
        else:
            # 2. 尝试从数据库获取或生成新密钥
            encryption_key = self._get_or_create_encryption_key()
        
        # 3. 验证密钥格式
        try:
            self._fernet = Fernet(encryption_key.encode())
            logger.info("Encryption system initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize encryption: {e}")
            raise ValueError("Invalid encryption key format")
    
    def _get_or_create_encryption_key(self) -> str:
        """从数据库获取密钥，如果不存在则创建新密钥"""
        try:
            # 尝试一次性完成所有数据库操作
            return self._database_key_operations()
        except Exception as db_error:
            logger.warning(f"Database operations failed: {db_error}")
            # 数据库操作失败时，生成内存密钥
            encryption_key = self._generate_encryption_key()
            logger.info("Generated new encryption key (using in-memory only)")
            logger.info("For better security, consider moving this to .env file:")
            logger.info(f"ENCRYPTION_KEY={encryption_key}")
            return encryption_key
    
    def _database_key_operations(self) -> str:
        """统一的数据库密钥操作（获取存储的密钥或创建新密钥）"""
        if self.db_type == "sqlite":
            return self._sqlite_key_operations()
        elif self.db_type == "mysql":
            return self._mysql_key_operations()
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")
    
    def _sqlite_key_operations(self) -> str:
        """SQLite密钥操作"""
        import sqlite3
        db_path = env_config.database_path
        
        # 确保数据库目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            
            # 创建配置表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 检查是否已有加密密钥
            cursor.execute('SELECT value FROM config WHERE key = ?', ('encryption_key',))
            row = cursor.fetchone()
            
            if row:
                logger.info("Using stored encryption key from database")
                return row[0]
            else:
                # 检查是否有加密数据
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='channels'")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("SELECT COUNT(*) FROM channels WHERE api_key LIKE 'encrypted:%'")
                    has_encrypted_data = cursor.fetchone()[0] > 0
                    
                    if has_encrypted_data:
                        raise ValueError("Found encrypted API keys but no encryption key stored")
                
                # 生成并存储新密钥
                encryption_key = self._generate_encryption_key()
                cursor.execute(
                    'INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
                    ('encryption_key', encryption_key)
                )
                conn.commit()
                logger.info("Generated new encryption key and stored in database")
                logger.info("For better security, consider moving this to .env file:")
                logger.info(f"ENCRYPTION_KEY={encryption_key}")
                return encryption_key
                
        finally:
            conn.close()
    
    def _mysql_key_operations(self) -> str:
        """MySQL密钥操作"""
        import pymysql
        
        connect_params = {
            'host': env_config.mysql_host,
            'port': env_config.mysql_port,
            'user': env_config.mysql_user,
            'password': env_config.mysql_password,
            'database': env_config.mysql_database,
            'charset': 'utf8mb4',
            'connect_timeout': 5,
            'read_timeout': 10,
            'write_timeout': 10,
            'ssl_disabled': False
        }
        
        if env_config.mysql_socket:
            connect_params['unix_socket'] = env_config.mysql_socket
            connect_params.pop('host', None)
            connect_params.pop('port', None)
        
        conn = pymysql.connect(**connect_params)
        try:
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            
            # 创建配置表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    `key` VARCHAR(255) PRIMARY KEY,
                    `value` TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            
            # 检查是否已有加密密钥
            cursor.execute('SELECT `value` FROM config WHERE `key` = %s', ('encryption_key',))
            row = cursor.fetchone()
            
            if row:
                logger.info("Using stored encryption key from database")
                return row['value']
            else:
                # 检查是否有加密数据
                cursor.execute("SHOW TABLES LIKE 'channels'")
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(*) as count FROM channels WHERE api_key LIKE %s", ("encrypted:%",))
                    has_encrypted_data = cursor.fetchone()['count'] > 0
                    
                    if has_encrypted_data:
                        raise ValueError("Found encrypted API keys but no encryption key stored")
                
                # 生成并存储新密钥
                encryption_key = self._generate_encryption_key()
                cursor.execute('''
                    INSERT INTO config (`key`, `value`, updated_at) 
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE
                    `value` = VALUES(`value`),
                    updated_at = VALUES(updated_at)
                ''', ('encryption_key', encryption_key))
                conn.commit()
                logger.info("Generated new encryption key and stored in database")
                logger.info("For better security, consider moving this to .env file:")
                logger.info(f"ENCRYPTION_KEY={encryption_key}")
                return encryption_key
                
        finally:
            conn.close()
    
    def _generate_encryption_key(self) -> str:
        """生成新的加密密钥"""
        # 生成32字节的随机密钥
        key = Fernet.generate_key()
        return key.decode()
    
    
    def encrypt_api_key(self, api_key: str) -> str:
        """加密API密钥"""
        if not api_key:
            return ""
        
        try:
            encrypted_data = self._fernet.encrypt(api_key.encode())
            # 返回base64编码的加密数据，添加前缀标识
            return f"encrypted:{base64.b64encode(encrypted_data).decode()}"
        except Exception as e:
            logger.error(f"Failed to encrypt API key: {e}")
            raise ValueError("Encryption failed")
    
    def decrypt_api_key(self, encrypted_api_key: str) -> str:
        """解密API密钥"""
        if not encrypted_api_key:
            return ""
        
        # 检查是否是加密格式
        if not encrypted_api_key.startswith("encrypted:"):
            # 兼容未加密的旧数据
            logger.warning("Found unencrypted API key, consider re-saving to encrypt it")
            return encrypted_api_key
        
        try:
            # 移除前缀并解码
            encrypted_data = encrypted_api_key[10:]  # 移除 "encrypted:" 前缀
            encrypted_bytes = base64.b64decode(encrypted_data.encode())
            
            # 解密
            decrypted_data = self._fernet.decrypt(encrypted_bytes)
            return decrypted_data.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt API key: {e}")
            raise ValueError("Decryption failed - possibly wrong encryption key")
    
    def is_encrypted(self, data: str) -> bool:
        """检查数据是否已加密"""
        return data.startswith("encrypted:") if data else False
    
    def rotate_encryption_key(self, new_key: str, old_encrypted_data: list) -> list:
        """
        轮换加密密钥（高级功能）
        重新加密所有数据使用新密钥
        """
        # 保存当前密钥
        old_fernet = self._fernet
        
        try:
            # 设置新密钥
            self._fernet = Fernet(new_key.encode())
            
            # 重新加密所有数据
            reencrypted_data = []
            for encrypted_item in old_encrypted_data:
                if self.is_encrypted(encrypted_item):
                    # 使用旧密钥解密
                    self._fernet = old_fernet
                    decrypted = self.decrypt_api_key(encrypted_item)
                    
                    # 使用新密钥加密
                    self._fernet = Fernet(new_key.encode())
                    reencrypted = self.encrypt_api_key(decrypted)
                    reencrypted_data.append(reencrypted)
                else:
                    reencrypted_data.append(encrypted_item)
            
            logger.info(f"Successfully rotated encryption key for {len(reencrypted_data)} items")
            return reencrypted_data
            
        except Exception as e:
            # 恢复旧密钥
            self._fernet = old_fernet
            logger.error(f"Key rotation failed: {e}")
            raise


# 全局加密管理器实例
encryption_manager = APIKeyEncryption()