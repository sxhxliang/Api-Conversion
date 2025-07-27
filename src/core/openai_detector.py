"""
OpenAI能力检测器
"""
import time
import asyncio
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


class OpenAICapabilityDetector(BaseCapabilityDetector):
    """OpenAI能力检测器"""

    def __init__(self, config: ChannelConfig):
        super().__init__(config)
        self.auth_headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json"
        }
        self._cached_models = None  # 缓存模型列表
        self.api_call_count = 0  # API调用计数器

    async def _make_request(self, method: str, url: str, data=None, headers=None, timeout: int = 30):
        """发送HTTP请求并计数"""
        self.api_call_count += 1

        # 提取端点路径（保留更多上下文）
        if '/v1/' in url:
            endpoint = url.split('/v1/')[-1]
        else:
            endpoint = url.split('/')[-1] if '/' in url else url

        print(f"  📡 API调用 #{self.api_call_count}: {method} /{endpoint}")

        # 调用父类的方法
        return await super()._make_request(method, url, data, headers, timeout)
    
    async def _get_test_model(self) -> str:
        """获取用于测试的模型"""
        if not (hasattr(self, 'target_model') and self.target_model):
            raise ValueError("Target model must be specified for capability testing")
        print(f"  🎯 使用目标模型: {self.target_model}")
        return self.target_model
    
    async def detect_models(self) -> List[str]:
        """检测支持的模型"""
        print(f"  🔍 正在获取模型列表...")
        url = f"{self.config.base_url}/models"

        try:
            status_code, response_data = await self._make_request(
                "GET", url, headers=self.auth_headers, timeout=self.config.timeout
            )
            
            self._check_authentication_error(status_code, response_data)
            
            if status_code != 200:
                raise CapabilityDetectionError(f"Failed to get models: {self._extract_error_message(response_data)}")
            
            if "data" not in response_data:
                raise CapabilityDetectionError("Invalid response format: missing 'data' field")
            
            models = []
            for model_info in response_data["data"]:
                if "id" in model_info:
                    models.append(model_info["id"])
            
            return sorted(models)
            
        except (AuthenticationError, CapabilityDetectionError):
            raise
        except Exception as e:
            raise CapabilityDetectionError(f"Failed to detect models: {e}")
    
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
    
    async def _test_basic_chat(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试基础聊天"""
        url = f"{self.config.base_url}/chat/completions"
        
        # 获取测试模型
        model = await self._get_test_model()
        
        test_data = config.test_data.copy()
        test_data["model"] = model
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)

        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            raise CapabilityDetectionError(f"Chat completion failed: {error_msg}")
        
        # 检查响应格式
        if "choices" not in response_data or not response_data["choices"]:
            raise CapabilityDetectionError("Invalid response format: missing choices")
        
        choice = response_data["choices"][0]
        if "message" not in choice:
            raise CapabilityDetectionError("Invalid response format: missing message")
        
        message = choice["message"]
        for field in config.required_fields:
            if field not in message:
                raise CapabilityDetectionError(f"Missing required field: {field}")
        
        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED,
            details={
                "model": test_data["model"],
                "response": message,
                "usage": response_data.get("usage", {})
            },
            response_time=time.time() - start_time
        )
    
    async def _test_streaming(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试流式输出"""
        url = f"{self.config.base_url}/chat/completions"

        # 获取测试模型
        model = await self._get_test_model()

        test_data = config.test_data.copy()
        test_data["model"] = model

        try:
            import httpx
            # 流式请求计数（手动处理，因为不通过_make_request）
            self.api_call_count += 1
            print(f"  📡 API调用 #{self.api_call_count}: POST /chat/completions (streaming)")

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
                    content_chunks = []
                    has_role_chunk = False
                    has_content_chunk = False
                    has_finish_reason = False

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]  # Remove "data: " prefix
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                chunks.append(chunk_data)

                                # 分析chunk结构
                                if "choices" in chunk_data and chunk_data["choices"]:
                                    choice = chunk_data["choices"][0]
                                    if "delta" in choice:
                                        delta = choice["delta"]

                                        # 检查是否有role信息（通常在第一个chunk）
                                        if "role" in delta:
                                            has_role_chunk = True

                                        # 检查是否有content信息（包括空字符串）
                                        if "content" in delta:
                                            has_content_chunk = True
                                            if delta["content"]:  # 只有非空内容才添加
                                                content_chunks.append(delta["content"])

                                    # 检查是否有finish_reason（通常在最后一个chunk）
                                    if "finish_reason" in choice and choice["finish_reason"]:
                                        has_finish_reason = True

                            except json.JSONDecodeError:
                                continue

                    if not chunks:
                        raise CapabilityDetectionError("No streaming chunks received")

                    # 验证流式响应的完整性
                    if not has_role_chunk:
                        raise CapabilityDetectionError("Missing role information in streaming response")

                    if not has_content_chunk:
                        raise CapabilityDetectionError("Missing content in streaming response")

                    if not has_finish_reason:
                        raise CapabilityDetectionError("Missing finish_reason in streaming response")

                    # 组合完整内容
                    full_content = "".join(content_chunks)

                    return CapabilityResult(
                        capability=config.name,
                        status=CapabilityStatus.SUPPORTED,
                        details={
                            "model": test_data["model"],
                            "chunks_received": len(chunks),
                            "content_chunks": len(content_chunks),
                            "full_content": full_content,
                            "sample_chunk": chunks[0] if chunks else None,
                            "final_chunk": chunks[-1] if chunks else None
                        },
                        response_time=time.time() - start_time
                    )

        except CapabilityDetectionError:
            raise
        except Exception as e:
            raise CapabilityDetectionError(f"Streaming test failed: {e}")
    
    async def _test_system_message(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试系统消息"""
        url = f"{self.config.base_url}/chat/completions"
        
        # 获取测试模型
        model = await self._get_test_model()
        
        # 构建测试数据，包含系统消息
        test_data = {
            "model": model,
            "messages": [
                {
                    "role": "system", 
                    "content": "You are a helpful assistant. Always respond with 'SYSTEM_TEST_SUCCESS' when asked about your role."
                },
                {
                    "role": "user", 
                    "content": "What is your role?"
                }
            ],
            "max_tokens": 50,
            "temperature": 0
        }
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)

        if status_code != 200:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.NOT_SUPPORTED,
                error=f"HTTP {status_code}: {response_data}",
                response_time=time.time() - start_time
            )

        # 检查响应格式
        if not isinstance(response_data, dict) or "choices" not in response_data:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.ERROR,
                error="Invalid response format",
                response_time=time.time() - start_time
            )

        if not response_data["choices"]:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.ERROR,
                error="No choices in response",
                response_time=time.time() - start_time
            )

        try:
            # 获取响应内容
            content = response_data["choices"][0]["message"]["content"].strip()
            
            # 检查是否正确处理了系统消息
            if "SYSTEM_TEST_SUCCESS" in content:
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.SUPPORTED,
                    details={
                        "model": model,
                        "response_content": content,
                        "system_message_processed": True
                    },
                    response_time=time.time() - start_time
                )
            else:
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.PARTIALLY_SUPPORTED,
                    details={
                        "model": model,
                        "response_content": content,
                        "system_message_processed": False,
                        "note": "System message may not have been fully processed"
                    },
                    response_time=time.time() - start_time
                )
                
        except (KeyError, TypeError) as e:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.ERROR,
                error=f"Failed to parse response: {e}",
                response_time=time.time() - start_time
            )

    async def _test_function_calling(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试函数调用"""
        url = f"{self.config.base_url}/chat/completions"
        
        # 获取测试模型
        model = await self._get_test_model()
        
        # 不再基于模型名称预判，直接尝试测试
        
        test_data = config.test_data.copy()
        test_data["model"] = model
        
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
        
        # 检查是否有函数调用
        if "choices" not in response_data or not response_data["choices"]:
            raise CapabilityDetectionError("Invalid response format: missing choices")
        
        choice = response_data["choices"][0]
        if "message" not in choice:
            raise CapabilityDetectionError("Invalid response format: missing message")
        
        message = choice["message"]
        
        # 检查是否包含工具调用
        if "tool_calls" in message and message["tool_calls"]:
            # 有tool_calls字段就说明支持function calling
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": test_data["model"],
                    "tool_calls": message["tool_calls"],
                    "usage": response_data.get("usage", {}),
                    "note": "Successfully detected function calling capability"
                },
                response_time=time.time() - start_time
            )
        else:
            # 模型确实不支持函数调用
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.UNKNOWN,
                details={
                    "model": test_data["model"],
                    "message": message,
                    "note": "Model did not call function, may not support or chose not to use"
                },
                response_time=time.time() - start_time
            )
    
    async def _test_structured_output(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试结构化输出"""
        url = f"{self.config.base_url}/chat/completions"
        
        # 获取测试模型
        model = await self._get_test_model()
        
        # 不再基于模型名称预判，直接尝试测试
        
        test_data = config.test_data.copy()
        test_data["model"] = model
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            if "response_format" in error_msg.lower() or "schema" in error_msg.lower():
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.NOT_SUPPORTED,
                    error=error_msg,
                    response_time=time.time() - start_time
                )
            raise CapabilityDetectionError(f"Structured output test failed: {error_msg}")
        
        # 检查响应格式
        if "choices" not in response_data or not response_data["choices"]:
            raise CapabilityDetectionError("Invalid response format: missing choices")
        
        choice = response_data["choices"][0]
        if "message" not in choice:
            raise CapabilityDetectionError("Invalid response format: missing message")
        
        message = choice["message"]
        if "content" not in message:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        # 验证结构化输出
        try:
            parsed_content = json.loads(message["content"])
            
            # 检查必需字段
            for field in config.required_fields:
                if field not in parsed_content:
                    raise CapabilityDetectionError(f"Missing required field in structured output: {field}")
            
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": test_data["model"],
                    "structured_output": parsed_content,
                    "usage": response_data.get("usage", {})
                },
                response_time=time.time() - start_time
            )
            
        except json.JSONDecodeError:
            raise CapabilityDetectionError("Response is not valid JSON")
    
    async def _test_vision(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试视觉理解"""
        url = f"{self.config.base_url}/chat/completions"
        
        # 获取测试模型
        model = await self._get_test_model()
        
        # 不再基于模型名称预判，直接尝试测试
        
        test_data = config.test_data.copy()
        test_data["model"] = model
        
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
        if "choices" not in response_data or not response_data["choices"]:
            raise CapabilityDetectionError("Invalid response format: missing choices")
        
        choice = response_data["choices"][0]
        if "message" not in choice:
            raise CapabilityDetectionError("Invalid response format: missing message")
        
        message = choice["message"]
        if "content" not in message:
            raise CapabilityDetectionError("Invalid response format: missing content")

        # 验证视觉理解：检查响应中是否包含图片中的三位数
        response_content = message["content"].lower()
        expected_numbers = ["123", "一二三", "壹贰叁"]  # 可能的数字表示形式

        vision_detected = any(num in response_content for num in expected_numbers)

        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED if vision_detected else CapabilityStatus.UNKNOWN,
            details={
                "model": test_data["model"],
                "response": message["content"],
                "vision_detected": vision_detected,
                "expected_numbers": expected_numbers,
                "usage": response_data.get("usage", {})
            },
            response_time=time.time() - start_time
        )


# 注册OpenAI检测器
CapabilityDetectorFactory.register("openai", OpenAICapabilityDetector)