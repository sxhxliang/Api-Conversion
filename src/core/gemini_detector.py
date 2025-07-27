"""
Gemini能力检测器
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


class GeminiCapabilityDetector(BaseCapabilityDetector):
    """Gemini能力检测器"""
    
    def __init__(self, config: ChannelConfig):
        super().__init__(config)
        # 设置正确的认证头
        self.auth_headers = {
            "x-goog-api-key": config.api_key,
            "Content-Type": "application/json"
        }
        # Gemini固定的模型列表
        self.known_models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro", 
            "gemini-2.0-flash-exp",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-1.5-pro",
            "gemini-1.0-pro"
        ]
    
    async def _get_test_model(self) -> str:
        """获取用于测试的模型"""
        if not self.target_model:
            raise ValueError("Target model must be specified for capability testing")
        return self.target_model
    
    async def detect_models(self) -> List[str]:
        """检测支持的模型"""
        url = f"{self.config.base_url}/models"
        
        try:
            status_code, response_data = await self._make_request(
                "GET", url, headers=self.auth_headers, timeout=self.config.timeout
            )
            
            if status_code == 401:
                raise AuthenticationError("Invalid API key")
            elif status_code == 403:
                raise AuthenticationError("Access forbidden - check API key permissions")
            elif status_code != 200:
                raise CapabilityDetectionError(f"Failed to get models: {self._extract_error_message(response_data)}")
            
            if "models" not in response_data:
                # 如果API调用失败，返回已知模型
                return self.known_models
            
            models = []
            for model_info in response_data["models"]:
                if "name" in model_info:
                    # 提取模型名称（去掉models/前缀）
                    model_name = model_info["name"]
                    if model_name.startswith("models/"):
                        model_name = model_name[7:]  # 去掉 "models/" 前缀
                    models.append(model_name)
            
            return sorted(models) if models else self.known_models
            
        except (AuthenticationError, CapabilityDetectionError):
            raise
        except Exception as e:
            # 如果检测失败，返回已知模型
            self.logger.warning(f"Failed to detect models: {e}")
            return self.known_models
    
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
    
    def _convert_to_gemini_format(self, openai_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """将OpenAI格式转换为Gemini格式"""
        system_instruction = None
        contents = []
        
        for msg in openai_messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                if isinstance(msg["content"], str):
                    contents.append({
                        "role": "user",
                        "parts": [{"text": msg["content"]}]
                    })
                elif isinstance(msg["content"], list):
                    # 多模态内容
                    parts = []
                    for content_item in msg["content"]:
                        if content_item["type"] == "text":
                            parts.append({"text": content_item["text"]})
                        elif content_item["type"] == "image_url":
                            # 处理图像
                            image_url = content_item["image_url"]["url"]
                            if image_url.startswith("data:image/"):
                                media_type, base64_data = image_url.split(",", 1)
                                media_type = media_type.split(":")[1].split(";")[0]
                                parts.append({
                                    "inlineData": {
                                        "mimeType": media_type,
                                        "data": base64_data
                                    }
                                })
                    contents.append({
                        "role": "user",
                        "parts": parts
                    })
            elif msg["role"] == "assistant":
                contents.append({
                    "role": "model",
                    "parts": [{"text": msg["content"]}]
                })
        
        result: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            # 基于2025年Gemini API文档格式，确保内容格式正确
            system_content = str(system_instruction).strip() if system_instruction else ""
            if system_content:
                result["system_instruction"] = {
                    "parts": [{"text": system_content}]
                }
        
        return result
    
    async def _test_basic_chat(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试基础聊天"""
        # 获取测试模型
        model = await self._get_test_model()
        
        # 转换为Gemini格式
        gemini_format = self._convert_to_gemini_format(config.test_data["messages"])
        
        # 添加生成配置
        test_data = {
            **gemini_format,
            "generationConfig": {
                "temperature": 0.7
            }
        }
        
        url = f"{self.config.base_url}/models/{model}:generateContent"
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            raise CapabilityDetectionError(f"Chat completion failed: {error_msg}")
        
        # 响应成功，继续处理
        
        # 检查响应格式
        if "candidates" not in response_data or not response_data["candidates"]:
            raise CapabilityDetectionError(f"Invalid response format: missing candidates. Full response: {response_data}")
        
        candidate = response_data["candidates"][0]
        if "content" not in candidate:
            raise CapabilityDetectionError(f"Invalid response format: missing content. Candidate: {candidate}")
        
        content = candidate["content"]
        if "parts" not in content or not content["parts"]:
            raise CapabilityDetectionError(f"Invalid response format: missing parts. Content: {content}")
        
        part = content["parts"][0]
        if "text" not in part:
            raise CapabilityDetectionError(f"Invalid response format: missing text. Part: {part}")
        
        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED,
            details={
                "model": model,
                "response": part["text"],
                "usage": response_data.get("usageMetadata", {})
            },
            response_time=time.time() - start_time
        )
    
    async def _test_streaming(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试流式输出"""
        # 获取测试模型
        model = await self._get_test_model()
        
        # 转换为Gemini格式
        gemini_format = self._convert_to_gemini_format(config.test_data["messages"])
        
        test_data = {
            **gemini_format,
            "generationConfig": {
                "temperature": 0.7
            }
        }
        
        url = f"{self.config.base_url}/models/{model}:streamGenerateContent?alt=sse"
        
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
                        line = line.strip()
                        if line.startswith('data: '):
                            data_str = line[6:]  # Remove "data: " prefix
                            # 检查是否是结束标记
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                chunks.append(chunk_data)
                            except json.JSONDecodeError:
                                continue
                        elif line and not line.startswith('data:'):
                            # 可能是直接的JSON
                            try:
                                chunk_data = json.loads(line)
                                chunks.append(chunk_data)
                            except json.JSONDecodeError:
                                continue
                    
                    if not chunks:
                        raise CapabilityDetectionError("No streaming chunks received")
                    
                    # 检查流式响应格式
                    valid_chunks = []
                    for chunk in chunks:
                        if "candidates" in chunk and chunk["candidates"]:
                            candidate = chunk["candidates"][0]
                            if "content" in candidate and "parts" in candidate["content"]:
                                valid_chunks.append(chunk)
                    
                    if valid_chunks:
                        return CapabilityResult(
                            capability=config.name,
                            status=CapabilityStatus.SUPPORTED,
                            details={
                                "model": model,
                                "chunks_received": len(valid_chunks),
                                "sample_chunk": valid_chunks[0] if valid_chunks else None
                            },
                            response_time=time.time() - start_time
                        )
                    
                    raise CapabilityDetectionError("No valid streaming chunks found")
                    
        except CapabilityDetectionError:
            raise
        except Exception as e:
            raise CapabilityDetectionError(f"Streaming test failed: {e}")
    
    async def _test_system_message(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试系统消息"""
        # 获取测试模型
        model = await self._get_test_model()
        
        # 使用配置文件中的消息内容
        gemini_format = self._convert_to_gemini_format(config.test_data["messages"])
        
        test_data = {
            **gemini_format,
            "generationConfig": {
                "temperature": 0
            }
        }
        
        url = f"{self.config.base_url}/models/{model}:generateContent"
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            raise CapabilityDetectionError(f"System message test failed: {error_msg}")
        
        # 检查响应格式
        if "candidates" not in response_data or not response_data["candidates"]:
            raise CapabilityDetectionError("Invalid response format: missing candidates")
        
        candidate = response_data["candidates"][0]
        
        # 检查响应内容
        if "content" not in candidate or "parts" not in candidate["content"]:
            finish_reason = candidate.get("finishReason", "UNKNOWN")
            raise CapabilityDetectionError(f"Invalid response format: {finish_reason}")
        
        # 获取响应文本
        response_text = ""
        for part in candidate["content"]["parts"]:
            if "text" in part:
                response_text += part["text"]
        
        # 检查是否包含期望的响应
        if "SYSTEM_TEST_SUCCESS" in response_text:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": model,
                    "response": response_text,
                    "has_system_instruction": "system_instruction" in test_data
                },
                response_time=time.time() - start_time
            )
        else:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.NOT_SUPPORTED,
                error="System message not working properly",
                response_time=time.time() - start_time
            )
    
    async def _test_function_calling(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试函数调用"""
        # 获取测试模型
        model = await self._get_test_model()
        
        # 使用配置文件中的正确消息内容
        function_calling_messages = config.test_data.get("messages", [])
        gemini_format = self._convert_to_gemini_format(function_calling_messages)
        
        # 转换工具定义格式
        tools = []
        for tool in config.test_data.get("tools", []):
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                gemini_tool = {
                    "functionDeclarations": [{
                        "name": func["name"],
                        "description": func["description"],
                        "parameters": func["parameters"]
                    }]
                }
                tools.append(gemini_tool)
        
        test_data = {
            **gemini_format,
            "tools": tools,
            "generationConfig": {
                "temperature": 0.7
            }
        }
        
        url = f"{self.config.base_url}/models/{model}:generateContent"
        
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
        
        # 检查响应格式
        if "candidates" not in response_data or not response_data["candidates"]:
            raise CapabilityDetectionError("Invalid response format: missing candidates")
        
        candidate = response_data["candidates"][0]
        
        # 检查响应内容
        if "content" not in candidate or "parts" not in candidate["content"]:
            finish_reason = candidate.get("finishReason", "UNKNOWN")
            raise CapabilityDetectionError(f"Invalid response format: {finish_reason}")
        
        content = candidate["content"]
        
        # 检查函数调用 - 使用Gemini特有的functionCall字段
        function_calls = []
        
        for part in content["parts"]:
            if "functionCall" in part:
                function_call = part["functionCall"]
                function_calls.append({
                    "name": function_call.get("name"),
                    "args": function_call.get("args", {})
                })
        
        if function_calls:
            # 有functionCall字段就说明支持function calling
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": model,
                    "function_calls": function_calls,
                    "usage": response_data.get("usageMetadata", {}),
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
                    "content": content,
                    "note": "Model did not call function, may not support or chose not to use"
                },
                response_time=time.time() - start_time
            )
    
    async def _test_structured_output(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试结构化输出"""
        # 获取测试模型
        model = await self._get_test_model()
        
        # 转换为Gemini格式
        gemini_format = self._convert_to_gemini_format(config.test_data["messages"])
        
        # 构建JSON schema
        json_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            },
            "required": ["name", "age"]
        }
        
        test_data = {
            **gemini_format,
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
                "responseSchema": json_schema
            }
        }
        
        url = f"{self.config.base_url}/models/{model}:generateContent"
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            if "responseMimeType" in error_msg.lower() or "responseSchema" in error_msg.lower():
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.NOT_SUPPORTED,
                    error="Structured output not supported",
                    response_time=time.time() - start_time
                )
            raise CapabilityDetectionError(f"Structured output test failed: {error_msg}")
        
        # 检查响应格式
        if "candidates" not in response_data or not response_data["candidates"]:
            raise CapabilityDetectionError("Invalid response format: missing candidates")
        
        candidate = response_data["candidates"][0]
        if "content" not in candidate or "parts" not in candidate["content"]:
            raise CapabilityDetectionError("Invalid response format: missing content parts")
        
        content = candidate["content"]
        part = content["parts"][0]
        
        # 对于Gemini的structured output，响应应该直接是有效的JSON
        if "text" not in part:
            raise CapabilityDetectionError("Invalid response format: missing text")
        
        try:
            # 由于设置了responseSchema，Gemini应该直接返回符合schema的JSON
            parsed_content = json.loads(part["text"])
            
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.SUPPORTED,
                details={
                    "model": model,
                    "structured_output": parsed_content,
                    "usage": response_data.get("usageMetadata", {}),
                    "schema_used": True,
                    "mime_type": "application/json"
                },
                response_time=time.time() - start_time
            )
            
        except json.JSONDecodeError:
            return CapabilityResult(
                capability=config.name,
                status=CapabilityStatus.NOT_SUPPORTED,
                error="Response is not valid JSON despite using responseSchema",
                details={"response": part["text"]},
                response_time=time.time() - start_time
            )
    
    async def _test_vision(self, config: CapabilityTestConfig, start_time: float) -> CapabilityResult:
        """测试视觉理解"""
        # 获取测试模型
        model = await self._get_test_model()
        
        # 转换为Gemini格式
        gemini_format = self._convert_to_gemini_format(config.test_data["messages"])
        
        test_data = {
            **gemini_format,
            "generationConfig": {
                "temperature": 0.7
            }
        }
        
        url = f"{self.config.base_url}/models/{model}:generateContent"
        
        status_code, response_data = await self._make_request(
            "POST", url, data=test_data, headers=self.auth_headers, timeout=config.timeout
        )
        
        self._check_authentication_error(status_code, response_data)
        
        if status_code != 200:
            error_msg = self._extract_error_message(response_data)
            error_lower = error_msg.lower()
            vision_related_errors = [
                "image", "vision", "multimodal", "visual", "unsupported media",
                "image parsing", "image format", "model does not support", "inline_data"
            ]
            
            if any(keyword in error_lower for keyword in vision_related_errors):
                return CapabilityResult(
                    capability=config.name,
                    status=CapabilityStatus.NOT_SUPPORTED,
                    error=f"Vision capability not supported: {error_msg}",
                    response_time=time.time() - start_time
                )
            
            raise CapabilityDetectionError(f"Vision test failed: {error_msg}")
        
        # 检查响应格式
        if "candidates" not in response_data or not response_data["candidates"]:
            raise CapabilityDetectionError("Invalid response format: missing candidates")
        
        candidate = response_data["candidates"][0]
        if "content" not in candidate:
            raise CapabilityDetectionError("Invalid response format: missing content")
        
        content = candidate["content"]
        if "parts" not in content or not content["parts"]:
            raise CapabilityDetectionError("Invalid response format: missing parts")
        
        part = content["parts"][0]
        if "text" not in part:
            raise CapabilityDetectionError("Invalid response format: missing text")
        
        return CapabilityResult(
            capability=config.name,
            status=CapabilityStatus.SUPPORTED,
            details={
                "model": model,
                "response": part["text"],
                "usage": response_data.get("usageMetadata", {})
            },
            response_time=time.time() - start_time
        )


# 注册Gemini检测器
CapabilityDetectorFactory.register("gemini", GeminiCapabilityDetector)