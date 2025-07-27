"""
基础转换器
定义格式转换的基础接口和通用功能
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from src.utils.logger import setup_logger

logger = setup_logger("base_converter")


@dataclass
class ConversionRequest:
    """转换请求"""
    source_format: str  # openai, anthropic, gemini
    target_format: str  # openai, anthropic, gemini
    data: Dict[str, Any]
    headers: Optional[Dict[str, str]] = None
    query_params: Optional[Dict[str, str]] = None


@dataclass
class ConversionResult:
    """转换结果"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    error: Optional[str] = None
    warning: Optional[str] = None


class BaseConverter(ABC):
    """基础转换器"""
    
    def __init__(self):
        self.logger = setup_logger(f"{self.__class__.__name__}")
    
    def reset_streaming_state(self):
        """重置所有流式相关的状态变量，避免状态污染 - 基类默认实现"""
        # 基类提供默认实现，子类可以重写或扩展
        pass
    
    @abstractmethod
    def convert_request(
        self,
        data: Dict[str, Any],
        target_format: str,
        headers: Optional[Dict[str, str]] = None
    ) -> ConversionResult:
        """转换请求格式"""
        pass
    
    @abstractmethod
    def convert_response(
        self,
        data: Dict[str, Any],
        source_format: str,
        target_format: str
    ) -> ConversionResult:
        """转换响应格式"""
        pass
    
    @abstractmethod
    def get_supported_formats(self) -> List[str]:
        """获取支持的格式列表"""
        pass
    
    def validate_format(self, format_name: str) -> bool:
        """验证格式是否支持"""
        return format_name in self.get_supported_formats()
    
    def _extract_system_message(self, messages: List[Dict[str, Any]]) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """提取系统消息"""
        system_message = None
        filtered_messages = []
        
        for message in messages:
            if message.get("role") == "system":
                system_message = message.get("content", "")
            else:
                filtered_messages.append(message)
        
        return system_message, filtered_messages
    
    def _create_system_message(self, content: str) -> Dict[str, Any]:
        """创建系统消息"""
        return {
            "role": "system",
            "content": content
        }
    
    def _safe_get(self, data: Dict[str, Any], key: str, default: Any = None) -> Any:
        """安全获取字典值"""
        return data.get(key, default)
    
    def _map_model_name(self, model: str, source_format: str, target_format: str) -> str:
        """映射模型名称（已废弃 - 现在直接使用原始模型名）"""
        self.logger.warning("_map_model_name is deprecated. Using original model name directly.")
        return model


class ConversionError(Exception):
    """转换错误"""
    pass