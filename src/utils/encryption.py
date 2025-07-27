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
        self._init_encryption_key()
    
    def _init_encryption_key(self):
        """初始化加密密钥"""
        # 尝试从环境变量获取加密密钥
        encryption_key = os.getenv('ENCRYPTION_KEY')
        
        if not encryption_key:
            # 检查数据库中是否已存在加密数据
            has_encrypted_data = self._check_existing_encrypted_data()
            
            if has_encrypted_data:
                # 如果数据库中有加密数据但没有环境变量，这是严重错误
                logger.error("Found encrypted API keys in database but no ENCRYPTION_KEY in environment!")
                logger.error("Please set ENCRYPTION_KEY in your .env file to decrypt existing data.")
                raise ValueError("Missing ENCRYPTION_KEY - cannot decrypt existing encrypted data")
            else:
                # 没有加密数据，生成新密钥并提示用户保存
                encryption_key = self._generate_encryption_key()
                logger.warning("Generated new encryption key. Please add this to your .env file:")
                logger.warning(f"ENCRYPTION_KEY={encryption_key}")
                logger.warning("This key is required to decrypt API keys stored in the database.")
        
        try:
            # 验证密钥格式
            self._fernet = Fernet(encryption_key.encode())
            logger.info("Encryption system initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize encryption: {e}")
            raise ValueError("Invalid encryption key format")
    
    def _check_existing_encrypted_data(self) -> bool:
        """检查数据库中是否存在加密数据（避免循环导入）"""
        try:
            from src.utils.env_config import env_config
            import sqlite3
            
            db_path = env_config.database_path
            if not os.path.exists(db_path):
                return False
                
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM channels WHERE api_key LIKE 'encrypted:%'"
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            # 如果查询失败（表不存在等），假设没有加密数据
            return False
    
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