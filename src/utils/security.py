"""
安全工具函数
提供敏感信息掩码和安全日志功能
"""
import json
import re
from typing import Any, Dict, Union
from copy import deepcopy


def mask_api_key(api_key: str, prefix_length: int = 4) -> str:
    """
    安全地掩码API key，只显示前几个字符，其余用星号替代
    
    Args:
        api_key: 原始API key
        prefix_length: 显示的前缀长度，默认4个字符
    
    Returns:
        掩码后的API key，格式如: sk-1234****
    """
    if not api_key:
        return "***empty***"
    
    if len(api_key) <= prefix_length:
        return "*" * len(api_key)
    
    # 显示前几位和足够的星号
    masked_length = max(8, len(api_key) - prefix_length)  # 至少8个星号
    return f"{api_key[:prefix_length]}{'*' * masked_length}"


def mask_sensitive_data(data: Union[Dict[str, Any], str], sensitive_keys: list = None) -> Union[Dict[str, Any], str]:
    """
    递归地掩码数据中的敏感信息
    
    Args:
        data: 要处理的数据（字典或字符串）
        sensitive_keys: 敏感字段名列表
    
    Returns:
        掩码后的数据副本
    """
    if sensitive_keys is None:
        sensitive_keys = [
            'api_key', 'apikey', 'key', 'token', 'password', 'secret', 
            'authorization', 'auth', 'x-api-key', 'x-goog-api-key',
            'bearer', 'access_token', 'refresh_token'
        ]
    
    if isinstance(data, str):
        # 如果是字符串，尝试解析为JSON
        try:
            parsed = json.loads(data)
            masked = mask_sensitive_data(parsed, sensitive_keys)
            return json.dumps(masked, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            # 如果不是JSON，检查是否包含API key模式
            return _mask_string_patterns(data)
    
    elif isinstance(data, dict):
        # 深拷贝避免修改原始数据
        masked_data = deepcopy(data)
        
        for key, value in masked_data.items():
            key_lower = key.lower()
            
            # 检查是否是敏感字段
            if any(sensitive_key in key_lower for sensitive_key in sensitive_keys):
                if isinstance(value, str):
                    masked_data[key] = mask_api_key(value)
                else:
                    masked_data[key] = "***masked***"
            elif isinstance(value, (dict, list)):
                # 递归处理嵌套结构
                masked_data[key] = mask_sensitive_data(value, sensitive_keys)
        
        return masked_data
    
    elif isinstance(data, list):
        # 处理列表
        return [mask_sensitive_data(item, sensitive_keys) for item in data]
    
    else:
        # 其他类型直接返回
        return data


def _mask_string_patterns(text: str) -> str:
    """
    掩码字符串中的API key模式
    """
    # 常见的API key模式
    patterns = [
        # OpenAI格式: sk-开头
        (r'sk-[a-zA-Z0-9]{48}', lambda m: mask_api_key(m.group())),
        # Anthropic格式: sk-ant-开头
        (r'sk-ant-[a-zA-Z0-9\-_]{95}', lambda m: mask_api_key(m.group())),
        # Google格式: 39字符的字母数字
        (r'AIza[a-zA-Z0-9_\-]{35}', lambda m: mask_api_key(m.group())),
        # Bearer token
        (r'Bearer\s+[a-zA-Z0-9\-_\.]{20,}', lambda m: f"Bearer {mask_api_key(m.group()[7:])}"),
        # 通用长字符串（可能是key）
        (r'[a-zA-Z0-9\-_]{32,}', lambda m: mask_api_key(m.group()) if len(m.group()) > 20 else m.group()),
    ]
    
    result = text
    for pattern, replacer in patterns:
        result = re.sub(pattern, replacer, result)
    
    return result


def safe_log_data(data: Any, max_length: int = 1000) -> str:
    """
    安全地格式化数据用于日志记录
    
    Args:
        data: 要记录的数据
        max_length: 最大长度限制
    
    Returns:
        安全的日志字符串
    """
    try:
        # 先掩码敏感信息
        masked_data = mask_sensitive_data(data)
        
        # 转换为字符串
        if isinstance(masked_data, (dict, list)):
            log_str = json.dumps(masked_data, ensure_ascii=False, indent=2)
        else:
            log_str = str(masked_data)
        
        # 限制长度
        if len(log_str) > max_length:
            log_str = log_str[:max_length] + "...[truncated]"
        
        return log_str
    
    except Exception as e:
        return f"***log_error: {str(e)}***"


def safe_log_request(request_data: Dict[str, Any]) -> str:
    """
    安全地记录请求数据
    """
    return safe_log_data(request_data, max_length=2000)


def safe_log_response(response_data: Dict[str, Any]) -> str:
    """
    安全地记录响应数据
    """
    return safe_log_data(response_data, max_length=1500)