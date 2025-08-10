"""
配置管理
"""
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class ChannelConfig:
    """渠道配置"""
    provider: str  # openai, anthropic, gemini
    base_url: str
    api_key: str
    timeout: int = 30
    max_retries: int = 3
    # 代理配置
    use_proxy: bool = False
    proxy_type: Optional[str] = None  # http, https, socks5
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    
    def __post_init__(self):
        # 验证必要参数
        if not self.provider:
            raise ValueError("Provider is required")
        if not self.base_url:
            raise ValueError("Base URL is required")
        if not self.api_key:
            raise ValueError("API key is required")


@dataclass
class CapabilityTestConfig:
    """能力测试配置"""
    name: str
    description: str
    test_method: str
    test_data: Dict[str, Any]
    required_fields: list
    timeout: int = 30


class ConfigManager:
    """配置管理器"""
    
    def __init__(self):
        load_dotenv()
        self.capabilities = self._load_default_capabilities()
    
    def create_channel_config(
        self, 
        provider: str, 
        base_url: str, 
        api_key: str,
        timeout: int = 30,
        max_retries: int = 3,
        use_proxy: bool = False,
        proxy_type: Optional[str] = None,
        proxy_host: Optional[str] = None,
        proxy_port: Optional[int] = None,
        proxy_username: Optional[str] = None,
        proxy_password: Optional[str] = None
    ) -> ChannelConfig:
        """创建渠道配置"""
        return ChannelConfig(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            use_proxy=use_proxy,
            proxy_type=proxy_type,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password
        )
    
    def _load_default_capabilities(self) -> Dict[str, CapabilityTestConfig]:
        """加载默认能力测试配置"""
        return {
            "basic_chat": CapabilityTestConfig(
                name="basic_chat",
                description="基础聊天对话",
                test_method="chat",
                test_data={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 10
                },
                required_fields=["content", "role"],
                timeout=30
            ),
            "streaming": CapabilityTestConfig(
                name="streaming",
                description="流式输出",
                test_method="chat",
                test_data={
                    "messages": [{"role": "user", "content": "Count from 1 to 5, separated by commas"}],
                    "max_tokens": 50,
                    "stream": True,
                    "temperature": 0
                },
                required_fields=["delta", "content", "role"],
                timeout=30
            ),
            "system_message": CapabilityTestConfig(
                name="system_message",
                description="系统消息支持",
                test_method="chat",
                test_data={
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant. Always respond with 'SYSTEM_TEST_SUCCESS' when asked about your role."},
                        {"role": "user", "content": "What is your role?"}
                    ],
                    "max_tokens": 50,
                    "temperature": 0
                },
                required_fields=["content", "role"],
                timeout=30
            ),

            "function_calling": CapabilityTestConfig(
                name="function_calling",
                description="函数调用",
                test_method="chat",
                test_data={
                    "messages": [{"role": "user", "content": "What time is it now? Please use the get_current_time function."}],
                    "tools": [{
                        "type": "function",
                        "function": {
                            "name": "get_current_time",
                            "description": "Get the current time",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "required": []
                            }
                        }
                    }],
                    "max_tokens": 100,
                    "tool_choice": "auto"
                },
                required_fields=["tool_calls"],
                timeout=30
            ),
            "structured_output": CapabilityTestConfig(
                name="structured_output",
                description="结构化输出",
                test_method="chat",
                test_data={
                    "messages": [{"role": "user", "content": "Generate a person's info in JSON format with 'name' and 'age' fields only. Return only valid JSON."}],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "person_info",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "age": {"type": "integer"}
                                },
                                "required": ["name", "age"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "max_tokens": 100
                },
                required_fields=["name", "age"],
                timeout=30
            ),
            "vision": CapabilityTestConfig(
                name="vision",
                description="视觉理解",
                test_method="chat",
                test_data={
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What number do you see in this image?"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,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"
                                }
                            }
                        ]
                    }],
                    "max_tokens": 50
                },
                required_fields=["content"],
                timeout=30
            )
        }
    
    def get_capability_config(self, capability_name: str) -> Optional[CapabilityTestConfig]:
        """获取能力测试配置"""
        return self.capabilities.get(capability_name)
    
    def get_all_capabilities(self) -> Dict[str, CapabilityTestConfig]:
        """获取所有能力测试配置"""
        return self.capabilities