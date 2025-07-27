"""
统一API端点
支持通过自定义key调用不同的AI服务，自动进行格式转换
"""
import json
import time
from typing import Dict, Any, Optional, List
import httpx
from fastapi import APIRouter, HTTPException, Request, Response, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from channels.channel_manager import channel_manager, ChannelInfo
from formats.converter_factory import ConverterFactory, convert_request, convert_response, convert_streaming_chunk
from formats.base_converter import ConversionResult
from utils.security import mask_api_key, safe_log_request, safe_log_response
from src.utils.logger import setup_logger
from src.utils.exceptions import ChannelNotFoundError, ConversionError, APIError, TimeoutError

logger = setup_logger("unified_api")

router = APIRouter()


def extract_openai_api_key(authorization: Optional[str] = Header(None)) -> str:
    """从OpenAI格式的Authorization header中提取API key"""
    logger.debug(f"OpenAI auth - Received authorization header: {mask_api_key(authorization) if authorization else 'None'}")
    
    if not authorization:
        logger.error("Missing Authorization header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        logger.error(f"Invalid Authorization header format: {mask_api_key(authorization)}")
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    api_key = authorization[7:]  # 移除 "Bearer " 前缀
    logger.debug(f"Extracted OpenAI API key: {mask_api_key(api_key)}")
    return api_key


def extract_anthropic_api_key(x_api_key: Optional[str] = Header(None, alias="x-api-key")) -> str:
    """从Anthropic格式的x-api-key header中提取API key"""
    logger.debug(f"Anthropic auth - Received x-api-key header: {mask_api_key(x_api_key) if x_api_key else 'None'}")
    
    if not x_api_key:
        logger.error("Missing x-api-key header")
        raise HTTPException(status_code=401, detail="Missing x-api-key header")
    
    logger.debug(f"Extracted Anthropic API key: {mask_api_key(x_api_key)}")
    return x_api_key


def extract_gemini_api_key(request: Request) -> str:
    """从Gemini格式的URL参数或header中提取API key"""
    logger.info(f"Gemini auth - Request URL: {request.url}")
    logger.info(f"Gemini auth - Query params: {dict(request.query_params)}")
    logger.info(f"Gemini auth - Headers: {dict(request.headers)}")
    
    # Gemini API支持多种认证方式，按优先级检查：
    # 1. URL参数 ?key=your_api_key
    api_key = request.query_params.get("key")
    if api_key:
        logger.debug(f"Gemini auth - Extracted API key from URL parameter: {mask_api_key(api_key)}")
        return api_key
    
    # 2. Google官方SDK使用的 x-goog-api-key header
    x_goog_api_key = request.headers.get("x-goog-api-key")
    if x_goog_api_key:
        logger.debug(f"Gemini auth - Extracted API key from x-goog-api-key header: {mask_api_key(x_goog_api_key)}")
        return x_goog_api_key
    
    # 3. 标准的Authorization Bearer header
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
        logger.debug(f"Gemini auth - Extracted API key from Authorization header: {mask_api_key(api_key)}")
        return api_key
    
    logger.error("Missing API key in URL parameter, x-goog-api-key header, and Authorization header")
    raise HTTPException(status_code=401, detail="Missing API key")


async def forward_request_to_channel(
    channel: ChannelInfo,
    request_data: Dict[str, Any],
    source_format: str,
    headers: Optional[Dict[str, str]] = None
):
    """转发请求到目标渠道（统一处理流式和非流式）"""
    # 1. 检查是否为同格式透传 - 但对于Anthropic需要特殊处理图片排序
    if source_format == channel.provider:
        # 对于Anthropic格式，即使是透传也需要应用图片优先的最佳实践
        if channel.provider == "anthropic":
            # 检查是否包含图片内容
            has_images = False
            messages = request_data.get("messages", [])
            for message in messages:
                if isinstance(message.get("content"), list):
                    for content in message["content"]:
                        if content.get("type") == "image":
                            has_images = True
                            break
                if has_images:
                    break
            
            if has_images:
                logger.info(f"Anthropic-to-Anthropic with images detected, applying image ordering best practice")
                # 强制进行转换以应用图片排序最佳实践
                conversion_result = convert_request(source_format, channel.provider, request_data, headers)
            else:
                logger.info(f"Anthropic-to-Anthropic without images, using passthrough")
                conversion_result = ConversionResult(success=True, data=request_data)
        else:
            logger.info(f"Same format detected, skipping request conversion: {source_format} -> {channel.provider}")
            conversion_result = ConversionResult(success=True, data=request_data)
    else:
        # 转换请求格式
        conversion_result = convert_request(source_format, channel.provider, request_data, headers)
        
        if not conversion_result.success:
            raise ConversionError(f"Request conversion failed: {conversion_result.error}")
    
    # 2. 统一构建目标API的URL和headers（支持所有功能组合）
    target_headers = {"Content-Type": "application/json"}
    is_streaming = request_data.get("stream", False)
    
    if channel.provider == "openai":
        url = f"{channel.base_url.rstrip('/')}/chat/completions"
        target_headers["Authorization"] = f"Bearer {channel.api_key}"
    elif channel.provider == "anthropic":
        url = f"{channel.base_url.rstrip('/')}/v1/messages"
        target_headers["x-api-key"] = channel.api_key
        target_headers["anthropic-version"] = "2023-06-01"
    elif channel.provider == "gemini":
        model = request_data.get("model")
        if not model:
            raise ValueError("Model is required for Gemini API requests")
        
        # Gemini根据流式参数选择不同端点
        if is_streaming:
            url = f"{channel.base_url.rstrip('/')}/models/{model}:streamGenerateContent?alt=sse&key={channel.api_key}"
            target_headers["Accept"] = "text/event-stream"
        else:
            url = f"{channel.base_url.rstrip('/')}/models/{model}:generateContent?key={channel.api_key}"
    else:
        raise ValueError(f"Unsupported provider: {channel.provider}")
    
    # 3. 统一请求处理
    try:
        logger.debug(f"Sending {'streaming' if is_streaming else 'non-streaming'} request to {channel.provider}: {url}")
        logger.debug(f"Request data: {safe_log_request(conversion_result.data)}")
        
        if is_streaming:
            # 流式请求处理 - 创建独立的生成器函数
            async def stream_generator():
                try:
                    async with httpx.AsyncClient(timeout=channel.timeout) as client:
                        async with client.stream(
                            "POST",
                            url=url,
                            json=conversion_result.data,
                            headers=target_headers
                        ) as response:
                            async for chunk in handle_streaming_response(response, channel, request_data, source_format):
                                yield chunk
                except httpx.TimeoutException:
                    logger.error(f"Streaming request timeout after {channel.timeout} seconds")
                    raise TimeoutError(f"Streaming request timeout after {channel.timeout} seconds")
                except Exception as e:
                    error_msg = f"Streaming request failed: {str(e) if e else 'Unknown error'}"
                    logger.error(error_msg)
                    logger.exception("Streaming request exception details:")
                    raise APIError(error_msg)
            
            return stream_generator()
        else:
            # 统一处理非流式请求：发送转换后的请求到目标渠道
            logger.debug(f"Sending non-streaming request to {channel.provider}: {url}")
            async with httpx.AsyncClient(timeout=channel.timeout) as client:
                response = await client.post(
                    url=url,
                    json=conversion_result.data,
                    headers=target_headers
                )
                result = handle_non_streaming_response(response, channel, request_data, source_format)
                return result
                    
    except httpx.TimeoutException:
        logger.error(f"Non-streaming request timeout after {channel.timeout} seconds")
        raise TimeoutError(f"Non-streaming request timeout after {channel.timeout} seconds")
    except Exception as e:
        logger.error(f"Non-streaming request failed: {e}")
        raise APIError(f"Non-streaming request failed: {e}")


async def handle_streaming_response(response, channel, request_data, source_format):
    """处理流式响应"""
    logger.debug(f"Received streaming response from {channel.provider}: status={response.status_code}")
    
    if response.status_code == 200:
        # 流式处理响应
        logger.debug("Starting to process streaming response")
        chunk_count = 0
        
        # 根据客户端期望的格式选择合适的结束标记
        if source_format == "openai":
            end_marker = "data: [DONE]\n\n"
        elif source_format == "gemini":
            # Gemini不需要特殊的结束标记，最后一个chunk包含finishReason即可
            end_marker = ""
        elif source_format == "anthropic":
            # Anthropic使用event: message_stop
            end_marker = "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
        else:
            end_marker = "data: [DONE]\n\n"

        # For same-format passthrough, we need to preserve the complete SSE structure
        if channel.provider == source_format:
            logger.info(f"PASSTHROUGH MODE ACTIVATED: {channel.provider} -> {source_format}")
            logger.info(f"PASSTHROUGH: Response status = {response.status_code}")
            logger.info(f"PASSTHROUGH: Response headers = {dict(response.headers)}")
            
            # Direct passthrough of complete SSE lines including event: lines
            # 完全按原样传输，不做任何修改
            line_count = 0
            try:
                async for line in response.aiter_lines():
                    line_count += 1
                    if line_count <= 5 or line_count % 10 == 0 or line.strip() == "data: [DONE]":
                        logger.info(f"PASSTHROUGH Line {line_count}: '{line}'")
                    
                    # 完全按原样传输每一行
                    yield f"{line}\n"
                            
                logger.info(f"PASSTHROUGH COMPLETED: Total lines processed: {line_count}")
            except Exception as e:
                logger.error(f"PASSTHROUGH ERROR: {e}")
                raise
            return

        async for line in response.aiter_lines():
            # 记录所有接收到的行用于调试
            logger.debug(f"Received SSE line: '{line}'")
            
            # 只处理以 "data: " 开头的行，其余 SSE 行（如 event: keep-alive）直接忽略
            if not line.startswith("data: "):
                # 记录被忽略的行，特别关注思考模型可能的特殊格式
                if line.strip():  # 只记录非空行
                    logger.debug(f"Ignored non-data SSE line: '{line}'")
                continue

            data_content = line[6:]  # 移除 "data: " 前缀
            chunk_count += 1
            logger.debug(f"RAW CHUNK {chunk_count}: '{data_content}'")  # 详细记录原始数据

            # 处理结束哨兵或空数据 - 必须在JSON解析之前检查
            if data_content.strip() in ("[DONE]", ""):
                logger.info(f"Stream ended with marker: '{data_content.strip()}'")
                logger.info(f"Sending end_marker to client: '{end_marker}'")
                if end_marker:  # 只有非空的end_marker才发送
                    yield end_marker
                break
            
            try:
                # 解析JSON数据
                chunk_data = json.loads(data_content)
                logger.debug(f"Parsed chunk data: {chunk_data}")
                
                # 通用的chunk处理逻辑：检查是否有内容和结束标记
                # 这里不应该假设特定的格式结构，让转换器来处理格式差异
                has_content = False
                is_finish_chunk = False
                
                # 简单检测是否包含内容或结束标记，具体格式由转换器处理
                if chunk_data:
                    # 检测不同格式的结束标记
                    if isinstance(chunk_data, dict):
                        # OpenAI格式检测
                        if "choices" in chunk_data and chunk_data["choices"]:
                            choice = chunk_data["choices"][0]
                            if choice.get("finish_reason"):
                                is_finish_chunk = True
                            if choice.get("delta", {}).get("content"):
                                has_content = True
                            elif choice.get("delta", {}).get("tool_calls"):
                                # 检查tool_calls是否有效，避免undefined错误
                                tool_calls = choice.get("delta", {}).get("tool_calls", [])
                                if tool_calls and any(tc and tc.get("function") for tc in tool_calls):
                                    has_content = True  # 工具调用也算作有内容
                        
                        # Gemini格式检测 - 修复：允许同时有内容和结束标记
                        elif "candidates" in chunk_data and chunk_data["candidates"]:
                            candidate = chunk_data["candidates"][0]
                            # 检测是否有内容（文本或工具调用）
                            if candidate.get("content", {}).get("parts"):
                                parts = candidate["content"]["parts"]
                                # 检查是否有文本内容或工具调用
                                if any("text" in part or "functionCall" in part for part in parts):
                                    has_content = True
                            # 检测是否是结束chunk
                            if candidate.get("finishReason"):
                                is_finish_chunk = True
                        
                        # Anthropic格式检测 - 精确匹配需要处理的事件类型
                        elif chunk_data.get("type") == "content_block_delta":
                            # 文本或工具参数增量，需要处理
                            has_content = True
                        elif chunk_data.get("type") == "content_block_start":
                            # content_block_start标志内容块开始，包含文本或工具调用
                            content_block = chunk_data.get("content_block", {})
                            block_type = content_block.get("type")
                            if block_type in ["tool_use", "text"]:
                                has_content = True
                        elif chunk_data.get("type") == "content_block_stop":
                            # content_block_stop标志工具调用完成，需要处理
                            has_content = True
                        elif chunk_data.get("type") == "message_delta":
                            # message_delta包含stop_reason等结束信息
                            delta = chunk_data.get("delta", {})
                            if "stop_reason" in delta:
                                has_content = True
                        elif chunk_data.get("type") == "message_stop":
                            # message_stop标志流结束
                            is_finish_chunk = True
                        # 明确排除不需要处理的事件类型
                        elif chunk_data.get("type") in ["message_start"]:
                            # message_start只是流开始标记，不包含实际内容
                            has_content = False
                        # 其他未知类型默认不处理
                
                logger.debug(f"Chunk {chunk_count} analysis: has_content={has_content}, is_finish_chunk={is_finish_chunk}")
                
                # 如果有内容，转换并发送内容chunk（不管是否也是结束chunk）
                if has_content:
                    original_model = request_data.get("model")
                    # Fix parameter order: source_format=provider, target_format=client_format
                    logger.debug(f"Calling convert_streaming_chunk: source={channel.provider}, target={source_format}")
                    try:
                        response_conversion = convert_streaming_chunk(channel.provider, source_format, chunk_data, original_model)
                        logger.debug(f"Content chunk conversion result: success={response_conversion.success if response_conversion else 'None'}")
                    except Exception as e:
                        logger.error(f"Error in convert_streaming_chunk for content chunk: {e}")
                        logger.error(f"Parameters: provider={channel.provider}, source={source_format}, chunk={chunk_data}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        # 发送原始数据作为后备，然后继续处理下一个chunk
                        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                        continue
                    
                    if response_conversion and response_conversion.success:
                        converted_data = response_conversion.data
                        
                        if isinstance(converted_data, str):
                            # 如果是SSE格式字符串（Anthropic），直接输出
                            if converted_data.strip():  # 只有非空字符串才输出
                                logger.debug(f"Sending SSE chunk {chunk_count}: {converted_data[:100]}...")
                                yield converted_data
                        elif isinstance(converted_data, list):
                            # 多个事件，逐个发送保持事件边界
                            for ev in converted_data:
                                if ev.strip():
                                    logger.debug(f"Sending SSE chunk {chunk_count}: {ev[:100]}...")
                                    yield ev
                        else:
                            # 如果是JSON对象（OpenAI/Gemini），包装成data字段
                            logger.debug(f"Sending JSON chunk {chunk_count} to client: {json.dumps(converted_data, ensure_ascii=False)}")
                            yield f"data: {json.dumps(converted_data, ensure_ascii=False)}\n\n"
                    else:
                        # 如果转换失败，返回原始数据
                        logger.warning(f"Conversion failed: {response_conversion.error}")
                        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                
                # 检查是否是结束chunk（各种格式的结束标记）
                # 注意：如果chunk既有内容又是结束，避免重复处理（内容处理时已经处理了结束逻辑）
                if is_finish_chunk:
                    if has_content:
                        logger.debug(f"Stream ending with content+finish chunk - already processed by content handler")
                    else:
                        logger.debug(f"Stream ending with finish-only chunk: {chunk_data}")
                
                if is_finish_chunk and not has_content:
                    # 转换并发送结束chunk（可能包含最后的内容和结束事件）
                    original_model = request_data.get("model")
                    # Fix parameter order for finish event conversion as well
                    logger.debug(f"Calling convert_streaming_chunk for finish: source={channel.provider}, target={source_format}")
                    try:
                        response_conversion = convert_streaming_chunk(channel.provider, source_format, chunk_data, original_model)
                        logger.debug(f"Finish chunk conversion result: success={response_conversion.success if response_conversion else 'None'}")
                    except Exception as e:
                        logger.error(f"Error in convert_streaming_chunk for finish chunk: {e}")
                        logger.error(f"Parameters: provider={channel.provider}, source={source_format}, chunk={chunk_data}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        # 发送原始数据作为后备
                        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                        if end_marker:  # 只有非空的end_marker才发送
                            yield end_marker
                        break
                    
                    if response_conversion and response_conversion.success:
                        converted_data = response_conversion.data
                        if isinstance(converted_data, list):
                            # 如果是事件列表（Anthropic），逐个发送每个完整事件
                            for event in converted_data:
                                if event.strip():
                                    logger.debug(f"Sending finish event: {event[:100]}...")
                                    yield event
                        elif isinstance(converted_data, str):
                            if converted_data.strip():
                                logger.debug(f"Sending finish chunk: {converted_data[:100]}...")
                                yield converted_data
                        else:
                            logger.debug(f"Sending finish chunk to client: {json.dumps(converted_data, ensure_ascii=False)}")
                            yield f"data: {json.dumps(converted_data, ensure_ascii=False)}\n\n"
                    
                    # 发送结束标记
                    if end_marker:  # 只有非空的end_marker才发送
                        yield end_marker
                    break
                    
            except json.JSONDecodeError as e:
                # 详细记录JSON解析错误信息用于调试
                logger.error(f"JSON decode error in streaming response:")
                logger.error(f"  - Error: {e}")
                logger.error(f"  - Data content: '{data_content}'")
                logger.error(f"  - Data length: {len(data_content)}")
                logger.error(f"  - Channel provider: {channel.provider}")
                logger.error(f"  - Source format: {source_format}")
                logger.error(f"  - Chunk count: {chunk_count}")
                
                # 特殊处理：如果数据内容看起来像[DONE]但被其他字符包围
                if "[DONE]" in data_content:
                    logger.warning(f"Found [DONE] in malformed chunk: '{data_content}', sending end marker")
                    yield end_marker
                    break
                
                # 对于其他非法JSON，尝试透传（保持连接）
                logger.warning(f"Attempting to pass through malformed chunk as-is")
                yield f"data: {data_content}\n\n"
                continue
        
        logger.debug(f"Streaming completed. Total chunks processed: {chunk_count}")
        
        # 如果没有处理任何chunks，发送错误响应
        if chunk_count == 0:
            logger.warning("No chunks received from streaming response")
            # 发送一个错误响应
            error_chunk = {
                "id": "chatcmpl-error",
                "object": "chat.completion.chunk",
                "created": int(__import__('time').time()),
                "model": request_data.get("model", "unknown"),
                "choices": [{
                    "index": 0,
                    "delta": {"content": "Error: No response received from AI service."},
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
            if end_marker:  # 只有非空的end_marker才发送
                yield end_marker
    else:
        error_detail = f"Streaming request failed with status {response.status_code}: {await response.aread()}"
        logger.error(error_detail)
        raise APIError(error_detail)


def handle_non_streaming_response(response, channel, request_data, source_format):
    """处理非流式响应"""
    logger.info(f"Received response from {channel.provider}: status={response.status_code}")
    
    # 处理非流式响应
    if response.status_code == 200:
        response_data = response.json()
        logger.debug(f"Received response from {channel.provider}: {safe_log_response(response_data)}")
        
        # 检查是否为同格式透传
        if channel.provider == source_format:
            logger.debug(f"Same format passthrough for non-streaming response: {channel.provider} -> {source_format}")
            # 同格式直接返回原始数据
            return response_data
        else:
            # 转换响应格式
            converter = ConverterFactory().get_converter(source_format)
            
            # 设置原始模型名称
            original_model = request_data.get("model")
            if hasattr(converter, 'set_original_model') and original_model:
                converter.set_original_model(original_model)
            
            conversion_result = converter.convert_response(
                response_data, 
                channel.provider, 
                source_format
            )
            
            if not conversion_result.success:
                raise ConversionError(f"Response conversion failed: {conversion_result.error}")
            
            logger.debug(f"Converted response: {safe_log_response(conversion_result.data)}")
            return conversion_result.data
    
    # 处理 429 限流错误，返回带重试建议的响应
    elif response.status_code == 429:
        error_data = response.json() if response.content else {}
        retry_after = "20"  # OpenAI 默认建议 20 秒
        
        # 尝试从错误消息中提取具体等待时间
        if "error" in error_data and "message" in error_data["error"]:
            import re
            match = re.search(r'try again in (\d+)s', error_data["error"]["message"])
            if match:
                retry_after = match.group(1)
        
        # 抛出 HTTPException 由上层统一处理
        raise HTTPException(status_code=429, detail="Rate limit exceeded", headers={"Retry-After": retry_after})
    
    else:
        error_text = response.text
        logger.error(f"Target API request failed with status {response.status_code}: {error_text}")
        raise APIError(f"Target API request failed with status {response.status_code}: {error_text}")


# --------------------------------------------------------------------


# 统一的OpenAI兼容端点
@router.post("/v1/chat/completions")
async def unified_openai_format_endpoint(
    request: Request,
    api_key: str = Depends(extract_openai_api_key)
):
    """OpenAI格式统一端点（使用标准OpenAI认证）"""
    return await handle_unified_request(request, api_key, source_format="openai")


@router.post("/v1/models")
async def list_models(api_key: str = Depends(extract_openai_api_key)):
    """列出可用模型"""
    try:
        channel = channel_manager.get_channel_by_custom_key(api_key)
        if not channel:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # 返回OpenAI格式的模型列表
        models = []
        if channel.models_mapping:
            for openai_model, target_model in channel.models_mapping.items():
                models.append({
                    "id": openai_model,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": channel.provider
                })
        else:
            # 默认模型映射
            default_models = {
                "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-3.5-turbo"],
                "anthropic": ["gpt-4o", "gpt-4", "gpt-3.5-turbo"],  # 映射到OpenAI模型名
                "gemini": ["gpt-4o", "gpt-4", "gpt-3.5-turbo"]
            }
            
            for model_name in default_models.get(channel.provider, []):
                models.append({
                    "id": model_name,
                    "object": "model", 
                    "created": int(time.time()),
                    "owned_by": channel.provider
                })
        
        return {
            "object": "list",
            "data": models
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List models failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 统一端点：基于路径自动识别格式，基于key识别目标渠道
@router.post("/v1/messages")
async def unified_anthropic_format_endpoint(
    request: Request,
    api_key: str = Depends(extract_anthropic_api_key)
):
    """Anthropic格式统一端点（使用标准Anthropic认证）"""
    return await handle_unified_request(request, api_key, source_format="anthropic")


@router.post("/v1beta/models/{model_id}:generateContent")
@router.post("/v1beta/models/{model_id}:streamGenerateContent") 
async def unified_gemini_format_endpoint(
    request: Request,
    model_id: str,
    api_key: str = Depends(extract_gemini_api_key)
):
    """Gemini格式统一端点（使用标准Gemini认证）"""
    # 检测是否为流式请求 (Gemini API特有的流式检测方式)
    is_streaming = False
    original_url = str(request.url)
    
    # Gemini API流式请求标准: 必须同时满足两个条件
    # 1. URL路径包含 :streamGenerateContent  
    # 2. URL参数包含 alt=sse
    if ":streamGenerateContent" in original_url and "alt=sse" in original_url:
        is_streaming = True
        logger.debug("Detected Gemini streaming request: :streamGenerateContent + alt=sse")
    
    # 清理模型ID，移除可能的后缀
    clean_model_id = model_id
    if ':generateContent' in model_id:
        clean_model_id = model_id.replace(':generateContent', '')
        logger.debug(f"Cleaned model ID: {model_id} -> {clean_model_id}")
    elif ':streamGenerateContent' in model_id:
        clean_model_id = model_id.replace(':streamGenerateContent', '')
        logger.debug(f"Cleaned model ID: {model_id} -> {clean_model_id}")
    
    # 将清理后的模型ID和流式标识添加到请求数据中
    request_data = await request.json()
    request_data["model"] = clean_model_id
    
    # 对于Gemini格式，如果检测到流式请求，设置stream参数
    if is_streaming:
        request_data["stream"] = True
        logger.debug("Added stream=True to request data for Gemini streaming")
    
    # 重新构建请求对象
    class ModifiedRequest:
        def __init__(self, original_request, modified_data):
            self.headers = original_request.headers
            self._json_data = modified_data
        
        async def json(self):
            return self._json_data
    
    modified_request = ModifiedRequest(request, request_data)
    return await handle_unified_request(modified_request, api_key, source_format="gemini")


@router.post("/v1beta/models/{model_id}:countTokens")
async def unified_gemini_count_tokens_endpoint(
    request: Request,
    model_id: str,
    api_key: str = Depends(extract_gemini_api_key)
):
    """Gemini格式countTokens端点（用于计算token数量）"""
    logger.info(f"Gemini countTokens request for model: {model_id}")
    
    try:
        # 清理模型ID，移除可能的countTokens后缀
        clean_model_id = model_id
        if ':countTokens' in model_id:
            clean_model_id = model_id.replace(':countTokens', '')
            logger.info(f"Cleaned model ID: {model_id} -> {clean_model_id}")
        
        # 获取请求数据
        request_data = await request.json()
        
        # 对于countTokens，只需要contents字段
        count_request_data = {
            "model": clean_model_id,
            "contents": request_data.get("contents", [])
        }
        
        logger.debug(f"Count tokens request data: {safe_log_request(count_request_data)}")
        
        # 直接调用渠道进行token计数
        # 这里需要找到合适的渠道来处理请求
        logger.debug(f"Looking for channel with custom_key: {mask_api_key(api_key)}")
        channel = channel_manager.get_channel_by_custom_key(api_key)
        if not channel:
            logger.error(f"No available channel found for API key: {mask_api_key(api_key)}")
            # 列出所有可用的渠道用于调试
            all_channels = channel_manager.get_all_channels()
            logger.info(f"Available channels: {[(ch.custom_key, ch.provider) for ch in all_channels]}")
            raise HTTPException(status_code=503, detail="No available channels")
        
        logger.info(f"Found channel: {channel.name} (provider: {channel.provider}, custom_key: {channel.custom_key})")
        
        # 根据渠道provider类型处理countTokens请求
        if channel.provider == "gemini":
            # Gemini渠道：直接转发countTokens请求
            return await handle_gemini_count_tokens(channel, clean_model_id, count_request_data)
        elif channel.provider == "openai":
            # OpenAI渠道：转换为OpenAI格式并使用tiktoken计算
            return await handle_openai_count_tokens_for_gemini(channel, clean_model_id, count_request_data)
        elif channel.provider == "anthropic":
            # Anthropic渠道：转换为Anthropic格式并估算token数量
            return await handle_anthropic_count_tokens_for_gemini(channel, clean_model_id, count_request_data)
        else:
            logger.error(f"Channel provider {channel.provider} does not support countTokens")
            raise HTTPException(status_code=400, detail=f"Channel provider {channel.provider} does not support countTokens")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Gemini countTokens request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


async def handle_unified_request(request, api_key: str, source_format: str):
    """统一请求处理逻辑"""
    try:
        logger.debug(f"Processing request: source_format={source_format}, api_key={mask_api_key(api_key)}")
        
        # 1. 根据key识别目标渠道
        channel = channel_manager.get_channel_by_custom_key(api_key)
        if not channel:
            logger.error(f"No channel found for api_key: {mask_api_key(api_key)}")
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # 2. 获取请求数据
        request_data = await request.json()
        
        # 3. 验证必须字段
        if not request_data.get("model"):
            raise HTTPException(status_code=400, detail="Model name is required")
        
        # Anthropic格式需要max_tokens字段
        if source_format == "anthropic" and not request_data.get("max_tokens"):
            raise HTTPException(status_code=400, detail="max_tokens is required for Anthropic format")
        
        logger.debug(f"Unified API: source_format={source_format}, key={mask_api_key(api_key)}, target_provider={channel.provider}")
        logger.debug(f"Request stream parameter: {request_data.get('stream', False)}")
        
        # 4. 根据流式参数选择处理方式
        is_streaming = request_data.get("stream", False)
        
        if is_streaming:
            # 流式请求
            logger.debug("Processing streaming request")
            stream_generator = await forward_request_to_channel(
                channel=channel,
                request_data=request_data,
                source_format=source_format,
                headers=dict(request.headers)
            )
            
            return StreamingResponse(
                stream_generator,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "X-Accel-Buffering": "no"  # 禁用Nginx缓冲，确保实时流式传输
                }
            )
        else:
            # 非流式请求
            logger.debug("Processing non-streaming request")
            response_data = await forward_request_to_channel(
                channel=channel,
                request_data=request_data,
                source_format=source_format,
                headers=dict(request.headers)
            )
            
            logger.debug(f"Final response data type: {type(response_data)}")
            logger.debug(f"Final response data: {safe_log_response(response_data)}")
            
            # 使用JSONResponse确保正确的Content-Type和编码
            return JSONResponse(
                content=response_data,
                status_code=200,
                headers={
                    "Content-Type": "application/json; charset=utf-8"
                }
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unified {source_format} API request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


async def handle_gemini_count_tokens(channel: ChannelInfo, model_id: str, request_data: dict):
    """处理Gemini渠道的countTokens请求"""
    logger.info(f"Handling Gemini countTokens for model: {model_id}")
    
    # 构建countTokens的URL和请求
    count_tokens_url = f"{channel.base_url.rstrip('/')}/models/{model_id}:countTokens"
    logger.info(f"Sending request to: {count_tokens_url}")
    logger.info(f"Channel base_url: {channel.base_url}")
    logger.debug(f"Using API key: {mask_api_key(channel.api_key)}")
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # 添加API key到URL参数
    if "?" in count_tokens_url:
        count_tokens_url += f"&key={channel.api_key}"
    else:
        count_tokens_url += f"?key={channel.api_key}"
        
    logger.info(f"Final URL with API key: {count_tokens_url}")
    
    # 发送请求到目标渠道
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            count_tokens_url,
            json=request_data,
            headers=headers
        )
        
        if response.status_code != 200:
            logger.error(f"Gemini count tokens request failed: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Count tokens request failed: {response.text}"
            )
        
        result = response.json()
        logger.info(f"Gemini count tokens response: {result}")
        
        return JSONResponse(
            content=result,
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )


async def handle_openai_count_tokens_for_gemini(channel: ChannelInfo, model_id: str, request_data: dict):
    """处理OpenAI渠道的countTokens请求，转换为Gemini格式响应"""
    logger.info(f"Handling OpenAI countTokens for Gemini format request, model: {model_id}")
    
    try:
        # 从Gemini格式的contents提取文本用于token计数
        contents = request_data.get("contents", [])
        text_to_count = ""
        
        for content in contents:
            if isinstance(content, dict):
                parts = content.get("parts", [])
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        text_to_count += part["text"] + "\n"
        
        logger.info(f"Extracted text for token counting: {text_to_count[:200]}...")
        
        # 使用tiktoken计算token数量
        import tiktoken
        
        # 根据模型选择正确的编码
        if "gpt-4" in model_id.lower():
            encoding = tiktoken.encoding_for_model("gpt-4")
        elif "gpt-3.5" in model_id.lower():
            encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        else:
            # 默认使用cl100k_base编码（适用于大多数现代模型）
            encoding = tiktoken.get_encoding("cl100k_base")
        
        # 计算token数量
        token_count = len(encoding.encode(text_to_count))
        logger.info(f"Calculated token count: {token_count}")
        
        # 构建Gemini格式的响应
        gemini_response = {
            "totalTokens": token_count
        }
        
        return JSONResponse(
            content=gemini_response,
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        
    except ImportError:
        # 如果tiktoken不可用，回退到简单的字符数估算
        logger.warning("tiktoken not available, using character-based estimation")
        
        contents = request_data.get("contents", [])
        text_to_count = ""
        
        for content in contents:
            if isinstance(content, dict):
                parts = content.get("parts", [])
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        text_to_count += part["text"] + "\n"
        
        # 简单估算：平均4个字符=1个token
        estimated_tokens = len(text_to_count) // 4
        logger.info(f"Estimated token count (character-based): {estimated_tokens}")
        
        gemini_response = {
            "totalTokens": estimated_tokens
        }
        
        return JSONResponse(
            content=gemini_response,
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
    
    except Exception as e:
        logger.error(f"OpenAI countTokens conversion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Token counting failed: {e}")


async def handle_anthropic_count_tokens_for_gemini(channel: ChannelInfo, model_id: str, request_data: dict):
    """处理Anthropic渠道的countTokens请求，转换为Gemini格式响应"""
    logger.info(f"Handling Anthropic countTokens for Gemini format request, model: {model_id}")
    
    try:
        # 从Gemini格式的contents提取文本用于token计数
        contents = request_data.get("contents", [])
        text_to_count = ""
        
        for content in contents:
            if isinstance(content, dict):
                parts = content.get("parts", [])
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        text_to_count += part["text"] + "\n"
        
        logger.info(f"Extracted text for token counting (Anthropic): {text_to_count[:200]}...")
        
        # Anthropic API没有专门的token计数端点，我们使用估算方法
        # Anthropic的token计算大致是：1 token ≈ 3.5个字符（英文）
        char_count = len(text_to_count)
        estimated_tokens = max(1, int(char_count / 3.5))
        
        logger.info(f"Estimated token count for Anthropic (char-based): {estimated_tokens}")
        
        # 构建Gemini格式的响应
        gemini_response = {
            "totalTokens": estimated_tokens
        }
        
        return JSONResponse(
            content=gemini_response,
            status_code=200,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
    
    except Exception as e:
        logger.error(f"Anthropic countTokens conversion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Token counting failed: {e}")


# 健康检查端点
@router.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "version": "1.0.0"
    }


