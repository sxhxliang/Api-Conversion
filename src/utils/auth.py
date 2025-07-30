"""
认证和授权管理
"""
import hashlib
import secrets
import os
from typing import Optional
from datetime import datetime, timedelta

from src.utils.database import db_manager
from src.utils.logger import setup_logger
from src.utils.env_config import env_config

logger = setup_logger("auth")


class AuthManager:
    """认证管理器"""
    
    def __init__(self):
        # 固定会话超时时间为1天
        self.session_timeout = timedelta(days=1)
        self._ensure_admin_password()
    
    def _ensure_admin_password(self):
        """确保管理员密码已设置"""
        # 环境变量优先：先读取环境变量中的密码
        env_password = env_config.admin_password
        stored_password_hash = db_manager.get_config("admin_password_hash")
        
        if not stored_password_hash:
            # 数据库中没有密码，将环境变量密码存入数据库（启动时不清除会话）
            self.set_admin_password(env_password, invalidate_sessions=False)
            password_prefix = env_password[:3] + "***" if len(env_password) >= 3 else "***"
            logger.info(f"Admin password initialized from environment config (prefix: {password_prefix})")
        else:
            # 数据库中有密码，检查是否与环境变量密码一致
            if self.verify_password(env_password, stored_password_hash):
                # 环境变量密码与数据库密码一致，不做任何操作
                logger.info("Environment password matches stored password")
            else:
                # 环境变量密码与数据库密码不一致，用环境变量密码更新数据库
                # 启动时如果密码不一致，也要清除会话（可能是环境配置被修改）
                self.set_admin_password(env_password, invalidate_sessions=True)
                password_prefix = env_password[:3] + "***" if len(env_password) >= 3 else "***"
                logger.info(f"Admin password updated from environment config (prefix: {password_prefix})")
    
    def hash_password(self, password: str) -> str:
        """对密码进行哈希"""
        # 使用随机盐值
        salt = secrets.token_hex(32)
        password_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return f"{salt}:{password_hash.hex()}"
    
    def verify_password(self, password: str, password_hash: str) -> bool:
        """验证密码"""
        try:
            salt, stored_hash = password_hash.split(':')
            password_hash_check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
            return stored_hash == password_hash_check.hex()
        except Exception:
            return False
    
    def set_admin_password(self, password: str, invalidate_sessions: bool = True):
        """设置管理员密码"""
        password_hash = self.hash_password(password)
        db_manager.set_config("admin_password_hash", password_hash)
        
        # 密码修改后，为安全起见，使所有现有会话失效
        if invalidate_sessions:
            invalidated_count = self.invalidate_all_sessions()
            logger.info(f"Admin password updated, invalidated {invalidated_count} sessions")
        else:
            logger.info("Admin password updated")
    
    def verify_admin_password(self, password: str) -> bool:
        """验证管理员密码"""
        stored_hash = db_manager.get_config("admin_password_hash")
        if not stored_hash:
            return False
        return self.verify_password(password, stored_hash)
    
    def generate_session_token(self) -> str:
        """生成会话令牌"""
        return secrets.token_urlsafe(32)
    
    def create_session(self, password: str) -> Optional[str]:
        """创建会话"""
        if not self.verify_admin_password(password):
            return None
        
        session_token = self.generate_session_token()
        expires_at = (datetime.now() + self.session_timeout).isoformat()
        
        # 存储会话信息
        db_manager.set_config(f"session:{session_token}", expires_at)
        
        logger.info("New admin session created")
        return session_token
    
    def verify_session(self, session_token: str) -> bool:
        """验证会话"""
        if not session_token:
            return False
        
        expires_at_str = db_manager.get_config(f"session:{session_token}")
        if not expires_at_str:
            return False
        
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now() > expires_at:
                # 会话已过期，删除
                self.delete_session(session_token)
                return False
            return True
        except Exception:
            return False
    
    def delete_session(self, session_token: str):
        """删除会话"""
        if not session_token:
            return
        
        session_key = f"session:{session_token}"
        deleted = db_manager.delete_config(session_key)
        
        from src.utils.security import mask_api_key
        if deleted:
            logger.info(f"Session {mask_api_key(session_token)} deleted successfully")
        else:
            logger.warning(f"Failed to delete session {mask_api_key(session_token)} - not found")
    
    def invalidate_all_sessions(self):
        """使所有会话失效 - 用于密码修改后的安全措施"""
        try:
            # 获取所有session配置
            session_configs = db_manager.get_configs_by_prefix("session:")
            deleted_count = 0
            
            for config in session_configs:
                session_key = config["key"]
                if db_manager.delete_config(session_key):
                    deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"Invalidated {deleted_count} sessions due to password change")
            else:
                logger.info("No active sessions to invalidate")
                
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to invalidate sessions: {e}")
            return 0
    
    def cleanup_expired_sessions(self):
        """清理过期会话"""
        try:
            # 获取所有session配置
            session_configs = db_manager.get_configs_by_prefix("session:")
            current_time = datetime.now()
            cleaned_count = 0
            
            for config in session_configs:
                session_key = config["key"]
                expires_at_str = config["value"]
                
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    if current_time > expires_at:
                        # 会话已过期，删除
                        if db_manager.delete_config(session_key):
                            cleaned_count += 1
                            session_token = session_key.replace("session:", "")
                            from src.utils.security import mask_api_key
                            logger.debug(f"Cleaned expired session: {mask_api_key(session_token)}")
                except Exception as e:
                    # 无效的时间格式，删除这个配置
                    logger.warning(f"Invalid session expiry format for {session_key}: {e}")
                    if db_manager.delete_config(session_key):
                        cleaned_count += 1
            
            logger.info(f"Session cleanup completed. Removed {cleaned_count} expired sessions")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Session cleanup failed: {e}")
            return 0


# 全局认证管理器实例
auth_manager = AuthManager()
