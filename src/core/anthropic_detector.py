"""
Anthropic能力检测器
"""
import time
import json
from typing import Dict, List, Any, Optional

from .capability_detector import (
    BaseCapabilityDetector, 
    CapabilityResult, 
    CapabilityStatus,
    CapabilityDetectorFactory
)
from src.utils.config import ChannelConfig, CapabilityTestConfig
from src.utils.exceptions import CapabilityDetectionError, AuthenticationError


class AnthropicCapabilityDetector(BaseCapabilityDetector):
    """Anthropic能力检测器"""
    
    def __init__(self, config: ChannelConfig):
        super().__init__(config)
        self.auth_headers = {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01"
        }
        # Anthropic固定的模型列表
        self.known_models = [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307"
        ]
        # 缓存模型列表
        self._cached_models = None
    
    async def detect_models(self) -> List[str]:
        """检测支持的模型"""
        try:
            # 使用官方的models API端点
            url = f"{self.config.base_url}/v1/models"
            status_code, response_data = await self._make_request(
                "GET", url, headers=self.auth_headers, timeout=30
            )
            
            if status_code == 200 and "data" in response_data:
                # 提取模型ID列表
                available_models = []
                for model_info in response_data["data"]:
                    if "id" in model_info:
                        available_models.append(model_info["id"])
                
                if available_models:
                    self.logger.info(f"Successfully detected {len(available_models)} models from API")
                    return available_models
                else:
                    self.logger.warning("No models found in API response")
                    return []
            else:
                self.logger.error(f"Models API call failed: {status_code}, response: {response_data}")
                return []
                
        except Exception as e:
            self.logger.error(f"Failed to fetch models from API: {e}")
            return []
    
    async def _get_test_model(self) -> str:
        """获取用于测试的模型"""
        if not (hasattr(self, 'target_model') and self.target_model):
            raise ValueError("Target model must be specified for capability testing")
        self.logger.info(f"使用目标模型: {self.target_model}")
        return self.target_model
    
    async def test_capability(self, capability_config: CapabilityTestConfig) -> CapabilityResult:
        """测试单个能力"""
        start_time = time.time()
        
        try:
            if capability_config.name == "basic_chat":
                return await self._test_basic_chat(capability_config, start_time)
            elif capability_config.name == "streaming":
                return await self._test_streaming(capability_config, start_time)
            elif capability_config.name == "system_message":
                return await self._test_system_message(capability_config, start_time)
            elif capability_config.name == "function_calling":
                return await self._test_function_calling(capability_config, start_time)
            elif capability_config.name == "structured_output":
                return await self._test_structured_output(capability_config, start_time)
            elif capability_config.name == "vision":
                return await self._test_vision(capability_config, start_time)
            else:
                return CapabilityResult(
                    capability=capability_config.name,
                    status=CapabilityStatus.UNKNOWN,
                    error="Unsupported capability test",
                    response_time=time.time() - start_time
                )
                
        except Exception as e:
            return CapabilityResult(
                capability=capability_config.name,
                status=CapabilityStatus.ERROR,
                error=str(e),
                response_time=time.time() - start_time
            )
    
    def _convert_to_anthropic_format(self, openai_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """将OpenAI格式转换为Anthropic格式"""
        system_message = None
        user_messages = []
        
        for msg in openai_messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                user_messages.append(msg)
        
        result = {"messages": user_messages}
        if system_message:
            result["system"] = system_message
        
        return result
    
    async def _test_basic_chat(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试基础聊天"""
        url = f"{self.config.base_url}/v1/messages"
        
        model = await self._get_test_model()
        
        # 转换为Anthropic格式
        anthropic_format = self._convert_to_anthropic_format(config.test_data["messages"])
        
        test_data = {
            "model": model,
            "max_tokens": config.test_data.get("max_tokens", 10),
            **anthropic_format
        }
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            raise CapabilityDetectionError(f"Chat completion failed: {error_msg}")
        
        # 检查响应格式
        if "content" not in response_data or not response_data["content"]:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        content = response_data["content"][0]
        if "text" not in content:
            raise CapabilityDetectionError("Invalid response format: missing text")
        
        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED,
            details={
                "model": model,
                "response": content,
                "usage": response_data.get("usage", {})
            },
            response_time=time.time() - start_time
        )
    
    async def _test_streaming(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试流式输出"""
        url = f"{self.config.base_url}/v1/messages"
        
        model = await self._get_test_model()
        
        # 转换为Anthropic格式
        anthropic_format = self._convert_to_anthropic_format(config.test_data["messages"])
        
        test_data = {
            "model": model,
            "max_tokens": config.test_data.get("max_tokens", 50),
            "stream": True,
            **anthropic_format
        }
        
        try:
            import httpx
            async with httpx.AsyncClient(timeout=config.timeout) as client:
                async with client.stream(
                    "POST", url, json=test_data, headers=self.auth_headers
                ) as response:
                    if response.status_code != 200:
                        response_data = await response.aread()
                        try:
                            error_data = json.loads(response_data.decode())
                            error_msg = self._extract_error_message(error_data)
                        except:
                            error_msg = response_data.decode()
                        raise CapabilityDetectionError(f"Streaming failed: {error_msg}")
                    
                    # 读取流式响应
                    chunks = []
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]  # Remove "data: " prefix
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                chunks.append(chunk_data)
                            except json.JSONDecodeError:
                                continue
                    
                    if not chunks:
                        raise CapabilityDetectionError("No streaming chunks received")
                    
                    # 检查流式响应格式
                    for chunk in chunks:
                        if "delta" in chunk and "text" in chunk["delta"]:
                            # 找到了delta字段，流式响应正常
                            return CapabilityResult(
                                capability=config.name,
                                status=CapabilityStatus.SUPPORTED,
                                details={
                                    "model": model,
                                    "chunks_received": len(chunks),
                                    "sample_chunk": chunks[0] if chunks else None
                                },
                                response_time=time.time() - start_time
                            )
                    
                    raise CapabilityDetectionError("No valid streaming delta found")
                    
        except CapabilityDetectionError:
            raise
        except Exception as e:
            raise CapabilityDetectionError(f"Streaming test failed: {e}")
    
    async def _test_system_message(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试系统消息"""
        url = f"{self.config.base_url}/v1/messages"
        
        model = await self._get_test_model()
        
        # 转换为Anthropic格式
        anthropic_format = self._convert_to_anthropic_format(config.test_data["messages"])
        
        test_data = {
            "model": model,
            "max_tokens": config.test_data.get("max_tokens", 10),
            **anthropic_format
        }
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            raise CapabilityDetectionError(f"System message test failed: {error_msg}")
        
        # 检查响应格式
        if "content" not in response_data or not response_data["content"]:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED,
            details={
                "model": model,
                "response": response_data["content"][0],
                "usage": response_data.get("usage", {}),
                "system_message_used": "system" in test_data
            },
            response_time=time.time() - start_time
        )
    
    async def _test_function_calling(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试函数调用（工具使用）"""
        url = f"{self.config.base_url}/v1/messages"
        
        model = await self._get_test_model()
        
        # 转换为Anthropic工具格式
        anthropic_format = self._convert_to_anthropic_format(config.test_data["messages"])
        
        # 转换工具定义格式
        tools = []
        for tool in config.test_data.get("tools", []):
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                anthropic_tool = {
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"]
                }
                tools.append(anthropic_tool)
        
        test_data = {
            "model": model,
            "max_tokens": config.test_data.get("max_tokens", 100),
            "tools": tools,
            **anthropic_format
        }
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            if "tools" in error_msg.lower() or "function" in error_msg.lower():
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.NOT_SUPPORTED,
                    error=error_msg,
                    response_time=time.time() - start_time
                )
            raise CapabilityDetectionError(f"Function calling test failed: {error_msg}")
        
        # 检查是否有工具使用
        if "content" not in response_data or not response_data["content"]:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        # 检查工具使用 - 使用Anthropic特有的tool_use字段
        tool_uses = []
        
        for content_block in response_data["content"]:
            if content_block.get("type") == "tool_use":
                tool_uses.append({
                    "name": content_block.get("name"),
                    "input": content_block.get("input", {}),
                    "id": content_block.get("id")
                })
        
        if tool_uses:
            # 有tool_use字段就说明支持function calling
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": model,
                    "tool_uses": tool_uses,
                    "usage": response_data.get("usage", {}),
                    "note": "Successfully detected function calling capability"
                },
                response_time=time.time() - start_time
            )
        else:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.UNKNOWN,
                details={
                    "model": model,
                    "content": response_data["content"],
                    "note": "Model did not use tool, may not support or chose not to use"
                },
                response_time=time.time() - start_time
            )
    
    async def _test_structured_output(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试结构化输出"""
        # Anthropic没有原生的结构化输出支持，通过提示词实现
        url = f"{self.config.base_url}/v1/messages"
        
        model = await self._get_test_model()
        
        # 使用配置文件中的消息内容
        anthropic_format = self._convert_to_anthropic_format(config.test_data["messages"])
        
        test_data = {
            "model": model,
            "max_tokens": config.test_data.get("max_tokens", 100),
            **anthropic_format
        }
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            raise CapabilityDetectionError(f"Structured output test failed: {error_msg}")
        
        # 检查响应格式
        if "content" not in response_data or not response_data["content"]:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        content = response_data["content"][0]
        if "text" not in content:
            raise CapabilityDetectionError("Invalid response format: missing text")
        
        # 验证结构化输出
        try:
            parsed_content = json.loads(content["text"])
            
            # 检查必需字段
            for field in config.required_fields:
                if field not in parsed_content:
                    raise CapabilityDetectionError(f"Missing required field in structured output: {field}")
            
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": model,
                    "structured_output": parsed_content,
                    "usage": response_data.get("usage", {}),
                    "note": "Structured output via prompt engineering"
                },
                response_time=time.time() - start_time
            )
            
        except json.JSONDecodeError:
            # 尝试从响应中提取JSON
            text_content = content["text"]
            import re
            json_match = re.search(r'\{.*\}', text_content, re.DOTALL)
            if json_match:
                try:
                    parsed_content = json.loads(json_match.group())
                    return CapabilityResult(
                        capability=config.name,
                        status=CapabilityStatus.SUPPORTED,
                        details={
                            "model": model,
                            "structured_output": parsed_content,
                            "usage": response_data.get("usage", {}),
                            "note": "Structured output via prompt engineering (extracted from text)"
                        },
                        response_time=time.time() - start_time
                    )
                except json.JSONDecodeError:
                    pass
            
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.NOT_SUPPORTED,
                error="Response is not valid JSON",
                details={"response": text_content},
                response_time=time.time() - start_time
            )
    
    async def _test_vision(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试视觉理解"""
        url = f"{self.config.base_url}/v1/messages"
        
        # 获取测试模型
        model = await self._get_test_model()
        
        # 使用一个250x250像素的测试图像，包含数字"123"
        valid_test_image = "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"
        
        # 创建vision测试消息
        anthropic_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": valid_test_image
                        }
                    },
                    {
                        "type": "text",
                        "text": "What number do you see in this image?"
                    }
                ]
            }
        ]
        
        test_data = {
            "model": model,
            "max_tokens": 1000,
            "messages": anthropic_messages
        }
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            if "image" in error_msg.lower() or "vision" in error_msg.lower():
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.NOT_SUPPORTED,
                    error=error_msg,
                    response_time=time.time() - start_time
                )
            raise CapabilityDetectionError(f"Vision test failed: {error_msg}")
        
        # 检查响应格式
        if "content" not in response_data or not response_data["content"]:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        content = response_data["content"][0]
        if "text" not in content:
            raise CapabilityDetectionError("Invalid response format: missing text")
        
        # 验证视觉理解：检查响应中是否包含图片中的数字
        response_content = content["text"].lower()
        expected_numbers = ["123", "一二三", "壹贰叁"]  # 可能的数字表示形式
        
        vision_detected = any(num in response_content for num in expected_numbers)
        
        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED if vision_detected else CapabilityStatus.UNKNOWN,
            details={
                "model": model,
                "response": content["text"],
                "vision_detected": vision_detected,
                "expected_numbers": expected_numbers,
                "usage": response_data.get("usage", {})
            },
            response_time=time.time() - start_time
        )


# 注册Anthropic检测器
CapabilityDetectorFactory.register("anthropic", AnthropicCapabilityDetector)