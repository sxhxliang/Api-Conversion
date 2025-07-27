"""
Gemini格式转换器
处理Google Gemini API格式与其他格式之间的转换
"""
from typing import Dict, Any, Optional, List
import json
import copy

from .base_converter import BaseConverter, ConversionResult, ConversionError


class GeminiConverter(BaseConverter):
    """Gemini格式转换器"""
    
    def __init__(self):
        super().__init__()
        self.original_model = None
    
    def set_original_model(self, model: str):
        """设置原始模型名称"""
        self.original_model = model
    
    def reset_streaming_state(self):
        """重置所有流式相关的状态变量，避免状态污染"""
        streaming_attrs = [
            '_anthropic_stream_id', '_openai_sent_start', '_gemini_text_started',
            '_anthropic_to_gemini_state'
        ]
        for attr in streaming_attrs:
            if hasattr(self, attr):
                delattr(self, attr)
    
    def get_supported_formats(self) -> List[str]:
        """获取支持的格式列表"""
        return ["openai", "anthropic", "gemini"]
    
    def convert_request(
        self,
        data: Dict[str, Any],
        target_format: str,
        headers: Optional[Dict[str, str]] = None
    ) -> ConversionResult:
        """转换Gemini请求到目标格式"""
        try:
            if target_format == "gemini":
                return ConversionResult(success=True, data=data)
            elif target_format == "openai":
                return self._convert_to_openai_request(data)
            elif target_format == "anthropic":
                return self._convert_to_anthropic_request(data)
            else:
                return ConversionResult(
                    success=False,
                    error=f"Unsupported target format: {target_format}"
                )
        except Exception as e:
            self.logger.error(f"Failed to convert Gemini request to {target_format}: {e}")
            return ConversionResult(success=False, error=str(e))
    
    def convert_response(
        self,
        data: Dict[str, Any],
        source_format: str,
        target_format: str
    ) -> ConversionResult:
        """转换响应到Gemini格式"""
        try:
            if source_format == "gemini":
                return ConversionResult(success=True, data=data)
            elif source_format == "openai":
                return self._convert_from_openai_response(data)
            elif source_format == "anthropic":
                return self._convert_from_anthropic_response(data)
            else:
                return ConversionResult(
                    success=False,
                    error=f"Unsupported source format: {source_format}"
                )
        except Exception as e:
            self.logger.error(f"Failed to convert {source_format} response to Gemini: {e}")
            return ConversionResult(success=False, error=str(e))
    
    def _convert_to_openai_request(self, data: Dict[str, Any]) -> ConversionResult:
        """转换Gemini请求到OpenAI格式"""
        result_data = {}
        
        # 必须有原始模型名称，否则报错
        if not self.original_model:
            raise ValueError("Original model name is required for request conversion")
        
        result_data["model"] = self.original_model  # 使用原始模型名称
        
        # 初始化函数调用ID映射表，用于保持工具调用和工具结果的ID一致性
        # 先扫描整个对话历史，为每个functionCall和functionResponse建立映射关系
        self._function_call_mapping = self._build_function_call_mapping(data.get("contents", []))
        
        # 处理消息和系统消息
        messages = []
        
        # 添加系统消息 - 支持两种格式
        system_instruction_data = data.get("systemInstruction") or data.get("system_instruction")
        if system_instruction_data:
            system_parts = system_instruction_data.get("parts", [])
            system_text = ""
            for part in system_parts:
                if "text" in part:
                    system_text += part["text"]
            if system_text:
                messages.append(self._create_system_message(system_text))
        
        # 转换内容
        if "contents" in data:
            for content in data["contents"]:
                gemini_role = content.get("role", "user")
                parts = content.get("parts", [])
                
                # 处理不同角色的消息
                if gemini_role == "user":
                    # 检查是否包含 functionResponse（工具结果）
                    has_function_response = any("functionResponse" in part for part in parts)
                    if has_function_response:
                        # 转换为 OpenAI 的 tool 消息
                        for part in parts:
                            if "functionResponse" in part:
                                fr = part["functionResponse"]
                                func_name = fr.get("name", "")
                                response_content = fr.get("response", {})
                                
                                # 从响应内容中提取文本
                                if isinstance(response_content, dict):
                                    tool_result = response_content.get("content", json.dumps(response_content, ensure_ascii=False))
                                else:
                                    tool_result = str(response_content)
                                
                                # 使用预先建立的映射获取对应的tool_call_id
                                if not hasattr(self, '_current_response_sequence'):
                                    self._current_response_sequence = {}
                                
                                sequence = self._current_response_sequence.get(func_name, 0) + 1
                                self._current_response_sequence[func_name] = sequence
                                
                                tool_call_id = self._function_call_mapping.get(f"response_{func_name}_{sequence}")
                                if not tool_call_id:
                                    # 如果没有映射，直接使用对应的call ID
                                    tool_call_id = self._function_call_mapping.get(f"{func_name}_{sequence}")
                                    if not tool_call_id:
                                        # 最后的备选方案：生成新的ID
                                        tool_call_id = f"call_{func_name}_{sequence:04d}"
                                
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call_id,
                                    "content": tool_result
                                })
                    else:
                        # 普通用户消息
                        message_content = self._convert_content_from_gemini(parts)
                        messages.append({
                            "role": "user",
                            "content": message_content
                        })
                        
                elif gemini_role == "model":
                    # 助手消息，可能包含工具调用
                    message_content = self._convert_content_from_gemini(parts)
                    
                    if isinstance(message_content, dict) and message_content.get("type") == "tool_calls":
                        # 有工具调用的助手消息
                        message = {
                            "role": "assistant",
                            "content": message_content.get("content"),
                            "tool_calls": message_content["tool_calls"]
                        }
                        messages.append(message)
                    else:
                        # 普通助手消息
                        messages.append({
                            "role": "assistant",
                            "content": message_content
                        })
                        
                elif gemini_role == "tool":
                    # 工具角色的消息，处理functionResponse
                    for part in parts:
                        if "functionResponse" in part:
                            fr = part["functionResponse"]
                            func_name = fr.get("name", "")
                            response_content = fr.get("response", {})
                            
                            # 从响应内容中提取文本
                            if isinstance(response_content, dict):
                                tool_result = response_content.get("content", json.dumps(response_content, ensure_ascii=False))
                            else:
                                tool_result = str(response_content)
                            
                            # 使用预先建立的映射获取对应的tool_call_id
                            if not hasattr(self, '_current_response_sequence'):
                                self._current_response_sequence = {}
                            
                            sequence = self._current_response_sequence.get(func_name, 0) + 1
                            self._current_response_sequence[func_name] = sequence
                            
                            tool_call_id = self._function_call_mapping.get(f"response_{func_name}_{sequence}")
                            if not tool_call_id:
                                # 如果没有映射，直接使用对应的call ID
                                tool_call_id = self._function_call_mapping.get(f"{func_name}_{sequence}")
                                if not tool_call_id:
                                    # 最后的备选方案：生成新的ID
                                    tool_call_id = f"call_{func_name}_{sequence:04d}"
                            
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": tool_result
                            })
                else:
                    # 其他角色，默认转为assistant
                    message_content = self._convert_content_from_gemini(parts)
                    messages.append({
                        "role": "assistant",
                        "content": message_content
                    })
        
        result_data["messages"] = messages
        
        # 处理生成配置
        if "generationConfig" in data:
            config = data["generationConfig"]
            if "temperature" in config:
                result_data["temperature"] = config["temperature"]
            if "topP" in config:
                result_data["top_p"] = config["topP"]
            if "maxOutputTokens" in config:
                result_data["max_tokens"] = config["maxOutputTokens"]
            if "stopSequences" in config:
                result_data["stop"] = config["stopSequences"]
            
            # 处理结构化输出
            if config.get("response_mime_type") == "application/json":
                result_data["response_format"] = {"type": "json_object"}
                if "response_schema" in config:
                    result_data["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "response",
                            "strict": True,
                            "schema": config["response_schema"]
                        }
                    }
        
        # 处理工具调用
        if "tools" in data:
            openai_tools = []
            for tool in data["tools"]:
                # Gemini官方使用 snake_case: function_declarations
                func_key = None
                if "function_declarations" in tool:
                    func_key = "function_declarations"
                elif "functionDeclarations" in tool:  # 兼容旧写法
                    func_key = "functionDeclarations"
                if func_key:
                    for func_decl in tool[func_key]:
                        openai_tools.append({
                            "type": "function",
                            "function": {
                                "name": func_decl.get("name", ""),
                                "description": func_decl.get("description", ""),
                                "parameters": self._sanitize_schema_for_openai(func_decl.get("parameters", {}))
                            }
                        })
            if openai_tools:
                result_data["tools"] = openai_tools
                result_data["tool_choice"] = "auto"
        
        # 处理流式参数 - 关键修复！
        if "stream" in data:
            result_data["stream"] = data["stream"]
        
        return ConversionResult(success=True, data=result_data)
    
    def _convert_to_anthropic_request(self, data: Dict[str, Any]) -> ConversionResult:
        """转换Gemini请求到Anthropic格式"""
        result_data = {}
        
        # 必须有原始模型名称，否则报错
        if not self.original_model:
            raise ValueError("Original model name is required for request conversion")
        
        result_data["model"] = self.original_model  # 使用原始模型名称
        
        # 处理系统消息 - 支持两种格式
        system_instruction_data = data.get("systemInstruction") or data.get("system_instruction")
        if system_instruction_data:
            system_parts = system_instruction_data.get("parts", [])
            system_text = ""
            for part in system_parts:
                if "text" in part:
                    system_text += part["text"]
            if system_text:
                result_data["system"] = system_text
        
        # 转换消息格式
        if "contents" in data:
            # 建立工具调用ID映射表
            self._function_call_mapping = self._build_function_call_mapping(data["contents"])
            
            anthropic_messages = []
            for content in data["contents"]:
                role = content.get("role", "user")
                if role == "model":
                    role = "assistant"
                elif role == "tool":
                    # Gemini的tool角色（functionResponse）对应Anthropic的user角色
                    role = "user"
                
                message_content = self._convert_content_to_anthropic(content.get("parts", []))
                
                # 跳过空内容的消息，Anthropic不允许空内容
                if not message_content or (isinstance(message_content, str) and not message_content.strip()):
                    self.logger.warning(f"Skipping message with empty content for role '{role}'")
                    continue
                
                anthropic_messages.append({
                    "role": role,
                    "content": message_content
                })
            result_data["messages"] = anthropic_messages
        
        # 处理生成配置
        if "generationConfig" in data:
            config = data["generationConfig"]
            if "temperature" in config:
                result_data["temperature"] = config["temperature"]
            if "topP" in config:
                result_data["top_p"] = config["topP"]
            if "topK" in config:
                result_data["top_k"] = config["topK"]
            if "maxOutputTokens" in config:
                result_data["max_tokens"] = config["maxOutputTokens"]
            if "stopSequences" in config:
                result_data["stop_sequences"] = config["stopSequences"]
        
        # Anthropic 要求必须有 max_tokens，按优先级处理：
        # 1. Gemini generationConfig中的maxOutputTokens（最高优先级）
        # 2. 环境变量ANTHROPIC_MAX_TOKENS
        # 3. 基于模型的自动设置
        # 4. 如果都没有且是未知模型，则报错
        if "max_tokens" not in result_data:
            # 优先级2：检查环境变量ANTHROPIC_MAX_TOKENS
            import os
            env_max_tokens = os.environ.get("ANTHROPIC_MAX_TOKENS")
            if env_max_tokens:
                try:
                    max_tokens = int(env_max_tokens)
                    self.logger.info(f"Using ANTHROPIC_MAX_TOKENS from environment: {max_tokens}")
                    result_data["max_tokens"] = max_tokens
                except ValueError:
                    self.logger.warning(f"Invalid ANTHROPIC_MAX_TOKENS value '{env_max_tokens}', must be integer")
                    # 继续使用基于模型的自动设置
                    env_max_tokens = None
            
            if not env_max_tokens:
                # 优先级3：根据模型自动设置最大max_tokens
                model = result_data["model"]
                if "claude-opus-4" in model or "claude-4-opus" in model:
                    max_tokens = 32000
                elif "claude-sonnet-4" in model or "claude-4-sonnet" in model or "claude-sonnet-3.7" in model:
                    max_tokens = 64000
                elif "claude-neptune-v3" in model:
                    max_tokens = 8192
                elif "claude-sonnet-3.5" in model or "claude-haiku-3.5" in model:
                    max_tokens = 8192
                elif "claude-opus-3" in model or "claude-haiku-3" in model or "claude-3" in model:
                    max_tokens = 4096
                else:
                    # 未知模型，报错要求明确指定max_tokens
                    raise ValueError(f"Unknown Claude model '{model}'. Please specify max_tokens in generationConfig.maxOutputTokens or set ANTHROPIC_MAX_TOKENS environment variable.")
                
                result_data["max_tokens"] = max_tokens
        
        # 处理工具调用
        if "tools" in data:
            anthropic_tools = []
            for tool in data["tools"]:
                func_key = None
                if "function_declarations" in tool:
                    func_key = "function_declarations"
                elif "functionDeclarations" in tool:  # 兼容旧写法
                    func_key = "functionDeclarations"
                if func_key:
                    for func_decl in tool[func_key]:
                        anthropic_tools.append({
                            "name": func_decl.get("name", ""),
                            "description": func_decl.get("description", ""),
                            "input_schema": self._convert_schema_for_anthropic(func_decl.get("parameters", {}))
                        })
            if anthropic_tools:
                result_data["tools"] = anthropic_tools
        
        # 处理流式参数 - 关键修复！
        if "stream" in data:
            result_data["stream"] = data["stream"]
        
        return ConversionResult(success=True, data=result_data)
    
    def _convert_from_openai_response(self, data: Dict[str, Any]) -> ConversionResult:
        """转换OpenAI响应到Gemini格式"""
        result_data = {
            "candidates": [],
            "usageMetadata": {}
        }
        
        # 处理选择
        if "choices" in data and data["choices"] and data["choices"][0]:
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            
            # 构建 parts
            parts = []
            
            # 添加文本内容（如果有）
            if content:
                parts.append({"text": content})
            
            # 添加工具调用（如果有）
            if tool_calls:
                for tool_call in tool_calls:
                    if tool_call and tool_call.get("type") == "function" and "function" in tool_call:
                        func = tool_call["function"]
                        func_name = func.get("name", "")
                        # OpenAI 的 arguments 是 JSON 字符串，需要解析
                        args_str = func.get("arguments", "{}")
                        try:
                            func_args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            func_args = {}
                        
                        parts.append({
                            "functionCall": {
                                "name": func_name,
                                "args": func_args
                            }
                        })
            
            # 如果没有任何内容，添加空文本
            if not parts:
                parts = [{"text": ""}]
            
            candidate = {
                "content": {
                    "parts": parts,
                    "role": "model"
                },
                "finishReason": self._map_finish_reason(choice.get("finish_reason", "stop"), "openai", "gemini"),
                "index": 0
            }
            result_data["candidates"] = [candidate]
        
        # 处理使用情况
        if "usage" in data and data["usage"] is not None:
            usage = data["usage"]
            result_data["usageMetadata"] = {
                "promptTokenCount": usage.get("prompt_tokens", 0),
                "candidatesTokenCount": usage.get("completion_tokens", 0),
                "totalTokenCount": usage.get("total_tokens", 0)
            }
        
        return ConversionResult(success=True, data=result_data)
    
    def _convert_from_anthropic_response(self, data: Dict[str, Any]) -> ConversionResult:
        """转换Anthropic响应到Gemini格式"""
        result_data = {
            "candidates": [],
            "usageMetadata": {}
        }
        
        # 处理内容，包括文本、工具调用和思考内容
        parts = []
        if "content" in data and isinstance(data["content"], list):
            for item in data["content"]:
                item_type = item.get("type")
                
                # 处理文本内容
                if item_type == "text":
                    text_content = item.get("text", "")
                    if text_content.strip():  # 只添加非空文本
                        parts.append({"text": text_content})
                
                # 处理思考内容 (thinking → text with thought: true)
                elif item_type == "thinking":
                    thinking_content = item.get("thinking", "")
                    if thinking_content.strip():
                        parts.append({
                            "text": thinking_content,
                            "thought": True  # Gemini 2025格式的thinking标识
                        })
                
                # 处理工具调用 (tool_use → functionCall)
                elif item_type == "tool_use":
                    parts.append({
                        "functionCall": {
                            "name": item.get("name", ""),
                            "args": item.get("input", {})
                        }
                    })
        
        # 如果没有任何内容，添加空文本避免空parts数组
        if not parts:
            parts = [{"text": ""}]
        
        candidate = {
            "content": {
                "parts": parts,
                "role": "model"
            },
            "finishReason": self._map_finish_reason(data.get("stop_reason", "end_turn"), "anthropic", "gemini"),
            "index": 0
        }
        result_data["candidates"] = [candidate]
        
        # 处理使用情况
        if "usage" in data and data["usage"] is not None:
            usage = data["usage"]
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            result_data["usageMetadata"] = {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
                "totalTokenCount": input_tokens + output_tokens
            }
        
        return ConversionResult(success=True, data=result_data)
    
    def _convert_from_openai_streaming_chunk(self, data: Dict[str, Any]) -> ConversionResult:
        """转换OpenAI流式响应chunk到Gemini格式"""
        self.logger.info(f"OPENAI->GEMINI CHUNK: {data}")  # 记录输入数据
        
        # 为流式工具调用维护状态
        if not hasattr(self, '_streaming_tool_calls'):
            self._streaming_tool_calls = {}
        # 先处理增量内容和工具调用（收集状态）
        if "choices" in data and data["choices"] and data["choices"][0]:
            choice = data["choices"][0]
            delta = choice.get("delta", {})
            
            # 收集流式工具调用信息
            if "tool_calls" in delta:
                for tool_call in delta["tool_calls"]:
                    call_index = tool_call.get("index", 0)
                    call_id = tool_call.get("id", "")
                    call_type = tool_call.get("type", "function")
                    
                    # 初始化工具调用状态
                    if call_index not in self._streaming_tool_calls:
                        self._streaming_tool_calls[call_index] = {
                            "id": call_id,
                            "type": call_type,
                            "function": {
                                "name": "",
                                "arguments": ""
                            }
                        }
                    
                    # 更新工具调用信息
                    if "function" in tool_call:
                        func = tool_call["function"]
                        if "name" in func:
                            self._streaming_tool_calls[call_index]["function"]["name"] = func["name"]
                        if "arguments" in func:
                            self._streaming_tool_calls[call_index]["function"]["arguments"] += func["arguments"]
                    
                    self.logger.debug(f"Updated tool call {call_index}: {self._streaming_tool_calls[call_index]}")
        
        # 检查是否是完整的流式响应结束
        if "choices" in data and data["choices"] and data["choices"][0] and data["choices"][0].get("finish_reason"):
            choice = data["choices"][0]
            delta = choice.get("delta", {})
            content = delta.get("content", "")
            
            # 构建parts数组，可能包含内容和工具调用
            parts = []
            
            # 处理文本内容
            if content:
                parts.append({"text": content})
            
            # 处理收集到的工具调用
            if self._streaming_tool_calls:
                self.logger.debug(f"FINISH: Processing collected tool calls: {self._streaming_tool_calls}")
                for call_index, tool_call in self._streaming_tool_calls.items():
                    func = tool_call.get("function", {})
                    func_name = func.get("name", "")
                    func_args = func.get("arguments", "{}")
                    self.logger.debug(f"FINISH TOOL CALL - name: {func_name}, args: '{func_args}'")

                    # OpenAI 的 arguments 字段是 JSON 字符串，需要解析
                    if func_args.strip() == "[DONE]":
                        self.logger.warning(f"Found [DONE] in tool call arguments, skipping")
                        continue
                    try:
                        func_args_json = json.loads(func_args) if isinstance(func_args, str) else func_args
                    except json.JSONDecodeError as e:
                        self.logger.error(f"JSON decode error in tool args: {e}, func_args: '{func_args}'")
                        func_args_json = {}

                    parts.append({
                        "functionCall": {
                            "name": func_name,
                            "args": func_args_json
                        }
                    })
                
                # 清理工具调用状态，为下次请求做准备
                self._streaming_tool_calls = {}
            
            # 这是最后的chunk，包含finish_reason
            result_data = {
                "candidates": [{
                    "content": {
                        "parts": parts,
                        "role": "model"
                    },
                    "finishReason": self._map_finish_reason(choice.get("finish_reason", "stop"), "openai", "gemini"),
                    "index": 0
                }]
            }
            
            # 添加usage信息（如果有且不为None）
            if "usage" in data and data["usage"] is not None:
                usage = data["usage"]
                result_data["usageMetadata"] = {
                    "promptTokenCount": usage.get("prompt_tokens", 0),
                    "candidatesTokenCount": usage.get("completion_tokens", 0),
                    "totalTokenCount": usage.get("total_tokens", 0)
                }
            
            return ConversionResult(success=True, data=result_data)
            
        # 检查是否有增量内容（非finish chunk）
        elif "choices" in data and data["choices"] and data["choices"][0]:
            choice = data["choices"][0]
            delta = choice.get("delta", {})
            content = delta.get("content", "")
            tool_calls = delta.get("tool_calls", [])
            
            parts = []
            
            # 处理文本内容
            if content:
                parts.append({"text": content})
            
            # 对于工具调用chunks，我们已经在上面收集了，这里不需要再处理
            # 只有当有文本内容时才发送chunk给客户端
            if tool_calls:
                self.logger.debug(f"Skipping tool call chunk (already collected): {tool_calls}")
            
            # 只有在有文本内容时才创建chunk
            if parts:
                result_data = {
                    "candidates": [{
                        "content": {
                            "parts": parts,
                            "role": "model"
                        },
                        "index": 0
                    }]
                }
                
                return ConversionResult(success=True, data=result_data)
        
        # 如果没有内容也没有工具调用，则返回空 candidates，保持 SSE 连接
        result_data = {
            "candidates": [{
                "content": {
                    "parts": [],
                    "role": "model"
                },
                "index": 0
            }]
        }
        
        return ConversionResult(success=True, data=result_data)
    
    def _convert_from_anthropic_streaming_chunk(self, data: Dict[str, Any]) -> ConversionResult:
        """转换Anthropic流式响应chunk到Gemini格式"""
        import json
        import random
        
        # 初始化流状态变量
        if not hasattr(self, '_anthropic_to_gemini_state'):
            self._anthropic_to_gemini_state = {
                'current_text': '',
                'current_tool_calls': {},  # index -> tool_call_info
                'has_started': False
            }
        
        state = self._anthropic_to_gemini_state
        
        # 检查是否是message_delta类型的结束
        if data.get("type") == "message_delta" and "delta" in data and "stop_reason" in data["delta"]:
            # 这是最后的chunk，只包含工具调用和结束信息，不重复发送文本
            parts = []
            
            # 只添加工具调用（文本内容已经在之前的text_delta中发送过了）
            for tool_info in state['current_tool_calls'].values():
                if tool_info.get('name') and tool_info.get('complete_args'):
                    try:
                        args_obj = json.loads(tool_info['complete_args'])
                        parts.append({
                            "functionCall": {
                                "name": tool_info['name'],
                                "args": args_obj
                            }
                        })
                    except json.JSONDecodeError:
                        # 如果JSON解析失败，使用空参数
                        parts.append({
                            "functionCall": {
                                "name": tool_info['name'],
                                "args": {}
                            }
                        })
            
            # 确定finish reason
            stop_reason = data["delta"]["stop_reason"]
            if stop_reason == "tool_use":
                finish_reason = "STOP"  # Gemini中工具调用也用STOP
            else:
                finish_reason = self._map_finish_reason(stop_reason, "anthropic", "gemini")
            
            result_data = {
                "candidates": [{
                    "content": {
                        "parts": parts,
                        "role": "model"
                    },
                    "finishReason": finish_reason,
                    "index": 0
                }]
            }
            
            # 添加usage信息（如果有且不为None）
            if "usage" in data and data["usage"] is not None:
                usage = data["usage"]
                result_data["usageMetadata"] = {
                    "promptTokenCount": usage.get("input_tokens", 0),
                    "candidatesTokenCount": usage.get("output_tokens", 0),
                    "totalTokenCount": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                }
            
            # 清理状态
            if hasattr(self, '_anthropic_to_gemini_state'):
                delattr(self, '_anthropic_to_gemini_state')
            
            return ConversionResult(success=True, data=result_data)
        
        # 处理content_block_start - 工具调用开始
        elif data.get("type") == "content_block_start" and "content_block" in data:
            content_block = data["content_block"]
            index = data.get("index", 0)
            
            if content_block.get("type") == "tool_use":
                # 记录工具调用信息
                state['current_tool_calls'][index] = {
                    'id': content_block.get("id", ""),
                    'name': content_block.get("name", ""),
                    'complete_args': ''
                }
            
            # 对于content_block_start，不返回任何内容
            return ConversionResult(success=True, data={})
        
        # 处理content_block_delta - 增量内容
        elif data.get("type") == "content_block_delta" and "delta" in data:
            delta = data["delta"]
            index = data.get("index", 0)
            
            # 文本增量
            if delta.get("type") == "text_delta":
                text_content = delta.get("text", "")
                if text_content:
                    state['current_text'] += text_content
                    
                    # 实时返回文本增量
                    result_data = {
                        "candidates": [{
                            "content": {
                                "parts": [{"text": text_content}],
                                "role": "model"
                            },
                            "index": 0
                        }]
                    }
                    return ConversionResult(success=True, data=result_data)
            
            # 工具调用参数增量
            elif delta.get("type") == "input_json_delta":
                partial_json = delta.get("partial_json", "")
                if index in state['current_tool_calls']:
                    state['current_tool_calls'][index]['complete_args'] += partial_json
                
                # 对于工具参数增量，不返回实时内容
                return ConversionResult(success=True, data={})
        
        # 处理content_block_stop
        elif data.get("type") == "content_block_stop":
            # 对于content_block_stop，不返回任何内容
            return ConversionResult(success=True, data={})
        
        # 处理message_start
        elif data.get("type") == "message_start":
            # 强制重置状态，确保新流开始时状态干净
            self._anthropic_to_gemini_state = {
                'current_text': '',
                'current_tool_calls': {},  # index -> tool_call_info
                'has_started': True
            }
            # 对于message_start，不返回任何内容
            return ConversionResult(success=True, data={})
        
        # 处理message_stop
        elif data.get("type") == "message_stop":
            # 清理状态
            if hasattr(self, '_anthropic_to_gemini_state'):
                delattr(self, '_anthropic_to_gemini_state')
            return ConversionResult(success=True, data={})
        
        # 其他类型的事件，返回空结构
        return ConversionResult(success=True, data={})
    
    def _convert_from_gemini_streaming_chunk(self, data: Dict[str, Any]) -> ConversionResult:
        """转换Gemini流式响应chunk到目标格式"""
        # Gemini流式响应通常包含candidates数组
        if "candidates" in data and data["candidates"]:
            candidate = data["candidates"][0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            finish_reason = candidate.get("finishReason")
            
            # 检查是否包含工具调用
            has_function_call = any("functionCall" in part for part in parts)
            has_text = any("text" in part for part in parts)
            
            result_data = {
                "candidates": [{
                    "content": {
                        "parts": parts,
                        "role": "model"
                    },
                    "index": 0
                }]
            }
            
            # 添加finishReason（如果有）
            if finish_reason:
                result_data["candidates"][0]["finishReason"] = finish_reason
            
            # 处理usage信息
            if "usageMetadata" in data:
                result_data["usageMetadata"] = data["usageMetadata"]
            
            return ConversionResult(success=True, data=result_data)
        
        # 如果没有candidates，返回空的结构
        result_data = {
            "candidates": [{
                "content": {
                    "parts": [],
                    "role": "model"
                },
                "index": 0
            }]
        }
        
        return ConversionResult(success=True, data=result_data)
    
    def _convert_content_from_gemini(self, parts: List[Dict[str, Any]]) -> Any:
        """转换Gemini内容到通用格式"""
        if len(parts) == 1 and "text" in parts[0]:
            return parts[0]["text"]
        
        # 处理多模态内容和工具调用
        converted_content = []
        has_tool_calls = False
        tool_calls = []
        text_content = ""
        
        for part in parts:
            if "text" in part:
                text_content += part["text"]
                converted_content.append({
                    "type": "text",
                    "text": part["text"]
                })
            elif "inlineData" in part:
                # 转换图像格式
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/jpeg")
                data_part = inline_data.get("data", "")
                converted_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{data_part}"
                    }
                })
            elif "functionCall" in part:
                # 转换函数调用
                fc = part["functionCall"]
                func_name = fc.get("name", "")
                func_args = fc.get("args", {})
                
                # 使用预先建立的ID映射
                # 为每个函数调用使用序列号确保唯一性
                if not hasattr(self, '_current_call_sequence'):
                    self._current_call_sequence = {}
                
                sequence = self._current_call_sequence.get(func_name, 0) + 1
                self._current_call_sequence[func_name] = sequence
                
                tool_call_id = self._function_call_mapping.get(f"{func_name}_{sequence}")
                if not tool_call_id:
                    # 如果映射中没有找到，生成新的ID
                    tool_call_id = f"call_{func_name}_{sequence:04d}"
                
                tool_calls.append({
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "arguments": json.dumps(func_args, ensure_ascii=False)
                    }
                })
                has_tool_calls = True
            elif "functionResponse" in part:
                # 函数响应在这里标记，但实际处理需要在消息级别
                converted_content.append({
                    "type": "function_response",
                    "function_response": part["functionResponse"]
                })
        
        # 如果有工具调用，返回特殊格式标识
        if has_tool_calls:
            return {
                "type": "tool_calls",
                "content": text_content if text_content else None,
                "tool_calls": tool_calls
            }
        
        # 如果只有一个文本且没有其他内容，直接返回文本
        if len(converted_content) == 1 and converted_content[0].get("type") == "text":
            return converted_content[0]["text"]
        
        return converted_content if converted_content else ""
    
    def _convert_content_to_anthropic(self, parts: List[Dict[str, Any]]) -> Any:
        """转换Gemini内容到Anthropic格式"""
        # 处理多模态和工具调用内容
        anthropic_content = []
        
        for part in parts:
            if "text" in part:
                text_content = part["text"]
                if text_content.strip():  # 只添加非空文本
                    # 检查是否是thinking内容
                    if part.get("thought", False):
                        # Gemini thinking → Anthropic thinking
                        anthropic_content.append({
                            "type": "thinking",
                            "thinking": text_content
                        })
                    else:
                        # 普通文本内容
                        anthropic_content.append({
                            "type": "text",
                            "text": text_content
                        })
            elif "inlineData" in part:
                # 转换图像格式
                inline_data = part["inlineData"]
                anthropic_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": inline_data.get("mimeType", "image/jpeg"),
                        "data": inline_data.get("data", "")
                    }
                })
            elif "functionCall" in part:
                # 转换工具调用 (functionCall → tool_use)
                func_call = part["functionCall"]
                func_name = func_call.get("name", "")
                
                # 使用映射表获取一致的ID
                tool_id = None
                if hasattr(self, '_function_call_mapping') and self._function_call_mapping:
                    # 查找对应的ID
                    for key, mapped_id in self._function_call_mapping.items():
                        if key.startswith(func_name) and not key.startswith("response_"):
                            tool_id = mapped_id
                            break
                
                # 如果没有找到映射ID，生成一个
                if not tool_id:
                    tool_id = f"call_{func_name}_{hash(str(func_call.get('args', {}))) % 1000000000}"
                
                anthropic_content.append({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": func_name,
                    "input": func_call.get("args", {})
                })
            elif "functionResponse" in part:
                # 转换工具响应 (functionResponse → tool_result)
                func_response = part["functionResponse"]
                func_name = func_response.get("name", "")
                
                # 使用映射表获取对应的tool_use ID
                tool_id = None
                if hasattr(self, '_function_call_mapping') and self._function_call_mapping:
                    for key, mapped_id in self._function_call_mapping.items():
                        if key.startswith(f"response_{func_name}"):
                            tool_id = mapped_id
                            break
                
                # 如果没有找到映射ID，尝试从functionResponse中获取或生成
                if not tool_id:
                    # 检查functionResponse是否包含原始的tool_use_id
                    response_data = func_response.get("response", {})
                    if isinstance(response_data, dict) and "_tool_use_id" in response_data:
                        tool_id = response_data["_tool_use_id"]
                    else:
                        # 生成一个基于函数名的一致性ID
                        # 使用函数名作为种子来生成一致的hash
                        import hashlib
                        seed = f"{func_name}_seed"
                        hash_value = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16) % 1000000000
                        tool_id = f"call_{func_name}_{hash_value}"
                
                # 提取实际的工具结果内容
                result_content = func_response.get("response", {})
                if isinstance(result_content, dict):
                    # 如果包含_tool_use_id，移除它
                    result_content = {k: v for k, v in result_content.items() if k != "_tool_use_id"}
                    # 提取实际内容
                    actual_content = result_content.get("content", result_content)
                else:
                    actual_content = result_content
                    
                anthropic_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": str(actual_content)
                })
        
        # 如果没有任何有效内容，返回空字符串（会在调用处处理）
        if not anthropic_content:
            return ""
        
        # 如果只有一个文本内容，直接返回字符串
        if len(anthropic_content) == 1 and anthropic_content[0].get("type") == "text":
            return anthropic_content[0]["text"]
        
        # 返回内容块数组
        return anthropic_content
    
    def _sanitize_schema_for_openai(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """将Gemini格式的JSON Schema转换为OpenAI兼容的格式"""
        if not isinstance(schema, dict):
            return schema
        
        # 复制schema避免修改原始数据
        sanitized = copy.deepcopy(schema)
        
        # Gemini到OpenAI的类型映射
        type_mapping = {
            "STRING": "string",
            "NUMBER": "number", 
            "INTEGER": "integer",
            "BOOLEAN": "boolean",
            "ARRAY": "array",
            "OBJECT": "object"
        }
        
        def convert_types(obj):
            """递归转换schema中的类型"""
            if isinstance(obj, dict):
                # 转换type字段
                if "type" in obj and isinstance(obj["type"], str):
                    obj["type"] = type_mapping.get(obj["type"].upper(), obj["type"].lower())
                
                # 转换需要整数值的字段（将字符串转换为整数）
                integer_fields = ["minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength"]
                for field in integer_fields:
                    if field in obj and isinstance(obj[field], str) and obj[field].isdigit():
                        obj[field] = int(obj[field])
                
                # 递归处理所有字段（跳过已经处理过的标量字段）
                for key, value in obj.items():
                    if key not in ["type"] + integer_fields:  # 避免重复处理已转换的字段
                        obj[key] = convert_types(value)
                    
            elif isinstance(obj, list):
                # 处理数组中的每个元素
                return [convert_types(item) for item in obj]
            
            return obj
        
        return convert_types(sanitized)
    
    def _build_function_call_mapping(self, contents: List[Dict[str, Any]]) -> Dict[str, str]:
        """扫描整个对话历史，为functionCall和functionResponse建立ID映射"""
        mapping = {}
        function_call_sequence = {}  # {func_name: sequence_number}
        
        for content in contents:
            parts = content.get("parts", [])
            for part in parts:
                if "functionCall" in part:
                    func_name = part["functionCall"].get("name", "")
                    if func_name:
                        # 为每个函数调用生成唯一的sequence number
                        sequence = function_call_sequence.get(func_name, 0) + 1
                        function_call_sequence[func_name] = sequence
                        
                        # 生成一致的ID
                        tool_call_id = f"call_{func_name}_{sequence:04d}"
                        mapping[f"{func_name}_{sequence}"] = tool_call_id
                        
                elif "functionResponse" in part:
                    func_name = part["functionResponse"].get("name", "")
                    if func_name:
                        # 为functionResponse分配最近的functionCall的ID
                        current_sequence = function_call_sequence.get(func_name, 0)
                        if current_sequence > 0:
                            mapping[f"response_{func_name}_{current_sequence}"] = mapping.get(f"{func_name}_{current_sequence}")
        
        return mapping
    
    def _convert_schema_for_anthropic(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """将Gemini格式的JSON Schema转换为Anthropic兼容的格式"""
        if not isinstance(schema, dict):
            return schema
        
        # 复制schema避免修改原始数据
        converted = copy.deepcopy(schema)
        
        # Gemini到Anthropic的类型映射
        type_mapping = {
            "STRING": "string",
            "NUMBER": "number", 
            "INTEGER": "integer",
            "BOOLEAN": "boolean",
            "ARRAY": "array",
            "OBJECT": "object"
        }
        
        def convert_types(obj):
            """递归转换schema中的类型"""
            if isinstance(obj, dict):
                # 转换type字段
                if "type" in obj and isinstance(obj["type"], str):
                    obj["type"] = type_mapping.get(obj["type"].upper(), obj["type"].lower())
                
                # 转换需要整数值的字段（将字符串转换为整数）
                integer_fields = ["minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength"]
                for field in integer_fields:
                    if field in obj and isinstance(obj[field], str) and obj[field].isdigit():
                        obj[field] = int(obj[field])
                
                # 递归处理所有字段（跳过已经处理过的标量字段）
                for key, value in obj.items():
                    if key not in ["type"] + integer_fields:  # 避免重复处理已转换的字段
                        obj[key] = convert_types(value)
                    
            elif isinstance(obj, list):
                # 处理数组中的每个元素
                return [convert_types(item) for item in obj]
            
            return obj
        
        return convert_types(converted)

    def _map_finish_reason(self, reason: str, source_format: str, target_format: str) -> str:
        """映射结束原因"""
        reason_mappings = {
            "openai": {
                "gemini": {
                    "stop": "STOP",
                    "length": "MAX_TOKENS",
                    "content_filter": "SAFETY",
                    "tool_calls": "MODEL_REQUESTED_TOOL"
                }
            },
            "anthropic": {
                "gemini": {
                    "end_turn": "STOP",
                    "max_tokens": "MAX_TOKENS",
                    "stop_sequence": "STOP",
                    "tool_use": "STOP"
                }
            }
        }
        
        try:
            return reason_mappings[source_format][target_format].get(reason, "STOP")
        except KeyError:
            return "STOP"