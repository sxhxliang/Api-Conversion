"""
日志配置
统一日志管理，支持单文件和时间轮转
"""
import logging
import logging.handlers
import sys
import os
from pathlib import Path
from typing import Optional

# 全局日志器缓存，避免重复创建
_loggers = {}


def setup_logger(name: str, level: str = None) -> logging.Logger:
    """设置日志器"""
    # 如果已经创建过，直接返回
    if name in _loggers:
        return _loggers[name]

    # 导入环境配置
    try:
        from src.utils.env_config import env_config
        log_level = level or env_config.log_level
        log_file = env_config.log_file
        log_max_days = env_config.log_max_days
    except ImportError:
        # 如果无法导入环境配置，使用默认值
        log_level = level or "WARNING"
        log_file = "logs/app.log"
        log_max_days = 1

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # 清除现有处理器
    logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 创建文件处理器（时间轮转）
    try:
        # 确保日志目录存在
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 使用TimedRotatingFileHandler进行时间轮转
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_file,
            when='midnight',  # 每天午夜轮转
            interval=1,       # 间隔1天
            backupCount=log_max_days,  # 保留的备份文件数量
            encoding='utf-8',
            utc=False
        )

        # 设置轮转文件的命名格式
        file_handler.suffix = "%Y-%m-%d"

        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    except Exception as e:
        # 文件处理器失败时仅打印一次警告，不中断程序
        console_handler.emit(logging.LogRecord(
            name=name,
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg=f"Failed to initialize file log handler: {e}",
            args=(),
            exc_info=None
        ))

    # 防止日志传播到根日志器
    logger.propagate = False

    # 缓存日志器
    _loggers[name] = logger

    return logger


def get_logger(name: str) -> logging.Logger:
    """获取日志器"""
    return _loggers.get(name) or setup_logger(name)


def cleanup_old_logs():
    """清理旧的日志文件"""
    try:
        from src.utils.env_config import env_config
        log_file = env_config.log_file
        log_max_days = env_config.log_max_days

        log_path = Path(log_file)
        log_dir = log_path.parent

        if not log_dir.exists():
            return

        import time
        current_time = time.time()
        max_age = log_max_days * 24 * 60 * 60  # 转换为秒

        # 清理旧的日志文件
        for file_path in log_dir.glob(f"{log_path.stem}.*"):
            if file_path.stat().st_mtime < (current_time - max_age):
                try:
                    file_path.unlink()
                    print(f"Cleaned up old log file: {file_path}")
                except Exception as e:
                    print(f"Failed to clean up log file {file_path}: {e}")

    except Exception as e:
        print(f"Failed to cleanup old logs: {e}")


# 在模块加载时清理旧日志
cleanup_old_logs()