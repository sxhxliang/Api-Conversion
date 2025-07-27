"""
格式转换API
提供API格式转换的核心路由和处理逻辑
"""
import asyncio
import json
import time
from typing import Dict, Any, Optional, List
import httpx
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from channels.channel_manager import channel_manager, ChannelInfo
from formats.converter_factory import ConverterFactory, convert_request, convert_response
from src.utils.logger import setup_logger
from src.utils.exceptions import ChannelNotFoundError, ConversionError, APIError, TimeoutError

logger = setup_logger("conversion_api")

router = APIRouter()


class ChannelCreateRequest(BaseModel):
    """创建渠道请求"""
    name: str = Field(..., description="渠道名称")
    provider: str = Field(..., description="提供商 (openai/anthropic/gemini)")
    base_url: str = Field(..., description="API基础URL")
    api_key: str = Field(..., description="API密钥")
    custom_key: str = Field(..., description="自定义key，用户调用时使用")
    timeout: int = Field(30, description="超时时间")
    max_retries: int = Field(3, description="最大重试次数")


class ChannelUpdateRequest(BaseModel):
    """更新渠道请求"""
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    custom_key: Optional[str] = None
    timeout: Optional[int] = None
    max_retries: Optional[int] = None
    enabled: Optional[bool] = None


async def detect_request_format(request_data: Dict[str, Any], path: str) -> str:
    """检测请求格式"""
    # 基于URL路径检测
    if "/openai/" in path or path.endswith("/chat/completions"):
        return "openai"
    elif "/anthropic/" in path or path.endswith("/messages"):
        return "anthropic"
    elif "/gemini/" in path or "generateContent" in path:
        return "gemini"
    
    # 基于请求数据结构检测
    if "messages" in request_data and "model" in request_data:
        if "system" in request_data:
            return "anthropic"
        else:
            return "openai"
    elif "contents" in request_data:
        return "gemini"
    
    # 默认返回openai格式
    return "openai"



async def forward_request(
    channel: ChannelInfo,
    converted_data: Dict[str, Any],
    headers: Dict[str, str],
    method: str = "POST"
) -> Dict[str, Any]:
    """转发请求到目标API"""
    # 构建请求URL
    if channel.provider == "openai":
        url = f"{channel.base_url.rstrip('/')}/chat/completions"
        headers["Authorization"] = f"Bearer {channel.api_key}"
    elif channel.provider == "anthropic":
        url = f"{channel.base_url.rstrip('/')}/v1/messages"
        headers["x-api-key"] = channel.api_key
        headers["anthropic-version"] = "2023-06-01"
    elif channel.provider == "gemini":
        # Gemini需要从converted_data中提取模型名称
        model = converted_data.get("model")
        if not model:
            raise ValueError("Model name is required for Gemini requests")
        url = f"{channel.base_url.rstrip('/')}/models/{model}:generateContent?key={channel.api_key}"
    else:
        raise ValueError(f"Unsupported provider: {channel.provider}")
    
    # 设置请求头
    headers["Content-Type"] = "application/json"
    
    try:
        async with httpx.AsyncClient(timeout=channel.timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                json=converted_data,
                headers=headers
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                error_detail = f"API request failed with status {response.status_code}: {response.text}"
                logger.error(error_detail)
                raise APIError(error_detail)
                
    except httpx.TimeoutException:
        raise TimeoutError(f"Request timeout after {channel.timeout} seconds")
    except Exception as e:
        logger.error(f"Request failed: {e}")
        raise APIError(f"Request failed: {e}")


# 渠道管理API
@router.post("/channels")
async def create_channel(request: ChannelCreateRequest):
    """创建新渠道"""
    try:
        channel_id = channel_manager.add_channel(
            name=request.name,
            provider=request.provider,
            base_url=request.base_url,
            api_key=request.api_key,
            custom_key=request.custom_key,
            timeout=request.timeout,
            max_retries=request.max_retries
        )
        
        return {
            "success": True,
            "channel_id": channel_id,
            "message": "Channel created successfully"
        }
    except Exception as e:
        logger.error(f"Failed to create channel: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/channels")
async def list_channels():
    """获取所有渠道"""
    try:
        channels = channel_manager.get_all_channels()
        return {
            "success": True,
            "channels": [
                {
                    "id": channel.id,
                    "name": channel.name,
                    "provider": channel.provider,
                    "base_url": channel.base_url,
                    "custom_key": channel.custom_key,
                    "enabled": channel.enabled,
                    "created_at": channel.created_at,
                    "updated_at": channel.updated_at
                }
                for channel in channels
            ]
        }
    except Exception as e:
        logger.error(f"Failed to list channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str):
    """获取特定渠道信息"""
    try:
        channel = channel_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        
        return {
            "success": True,
            "channel": {
                "id": channel.id,
                "name": channel.name,
                "provider": channel.provider,
                "base_url": channel.base_url,
                "custom_key": channel.custom_key,
                "timeout": channel.timeout,
                "max_retries": channel.max_retries,
                "enabled": channel.enabled,
                "created_at": channel.created_at,
                "updated_at": channel.updated_at
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/channels/{channel_id}")
async def update_channel(channel_id: str, request: ChannelUpdateRequest):
    """更新渠道信息"""
    try:
        success = channel_manager.update_channel(
            channel_id=channel_id,
            name=request.name,
            base_url=request.base_url,
            api_key=request.api_key,
            custom_key=request.custom_key,
            timeout=request.timeout,
            max_retries=request.max_retries,
            enabled=request.enabled
        )
        
        if success:
            return {
                "success": True,
                "message": "Channel updated successfully"
            }
        else:
            raise HTTPException(status_code=404, detail="Channel not found")
            
    except ChannelNotFoundError:
        raise HTTPException(status_code=404, detail="Channel not found")
    except Exception as e:
        logger.error(f"Failed to update channel: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """删除渠道"""
    try:
        success = channel_manager.delete_channel(channel_id)
        if success:
            return {
                "success": True,
                "message": "Channel deleted successfully"
            }
        else:
            raise HTTPException(status_code=404, detail="Channel not found")
            
    except ChannelNotFoundError:
        raise HTTPException(status_code=404, detail="Channel not found")
    except Exception as e:
        logger.error(f"Failed to delete channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/channels/{channel_id}/test")
async def test_channel(channel_id: str):
    """测试渠道连接"""
    try:
        result = channel_manager.test_channel_connection(channel_id)
        return {
            "success": True,
            "test_result": result
        }
    except ChannelNotFoundError:
        raise HTTPException(status_code=404, detail="Channel not found")
    except Exception as e:
        logger.error(f"Failed to test channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 格式转换API
@router.post("/openai/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI格式API端点"""
    return await handle_conversion_request(request, "openai")


@router.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic格式API端点"""
    return await handle_conversion_request(request, "anthropic")


@router.post("/gemini/v1beta/generateContent")
async def gemini_generate_content(request: Request):
    """Gemini格式API端点"""
    return await handle_conversion_request(request, "gemini")


async def handle_conversion_request(request: Request, target_format: str):
    """处理转换请求的通用逻辑"""
    try:
        # 获取请求数据
        request_data = await request.json()
        headers = dict(request.headers)
        
        # 检测源格式
        source_format = await detect_request_format(request_data, str(request.url.path))
        
        logger.info(f"Request URL path: {request.url.path}")
        logger.info(f"Detected source_format: {source_format}, target_format: {target_format}")
        logger.info(f"Converting request from {source_format} to {target_format}")
        
        # 如果源格式和目标格式相同，直接转发
        if source_format == target_format:
            # 根据目标格式找到合适的渠道
            channels = channel_manager.get_channels_by_provider(target_format)
            if not channels:
                raise HTTPException(
                    status_code=503,
                    detail=f"No available {target_format} channels configured"
                )
            
            # 选择第一个可用渠道
            channel = channels[0]
            
            # 直接转发请求
            response_data = await forward_request(channel, request_data, headers)
            return response_data
        
        # 格式转换
        conversion_result = convert_request(source_format, target_format, request_data, headers)
        
        if not conversion_result.success:
            raise HTTPException(
                status_code=400,
                detail=f"Request conversion failed: {conversion_result.error}"
            )
        
        # 找到目标格式的渠道
        channels = channel_manager.get_channels_by_provider(target_format)
        if not channels:
            raise HTTPException(
                status_code=503,
                detail=f"No available {target_format} channels configured"
            )
        
        # 选择第一个可用渠道
        channel = channels[0]
        
        # 转发请求
        response_data = await forward_request(channel, conversion_result.data, headers)
        
        # 转换响应格式
        response_conversion_result = convert_response(source_format, target_format, response_data)
        
        if not response_conversion_result.success:
            logger.warning(f"Response conversion failed: {response_conversion_result.error}")
            # 如果响应转换失败，返回原始响应
            return response_data
        
        return response_conversion_result.data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Conversion request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.get("/conversion/formats")
async def get_supported_formats():
    """获取支持的格式列表"""
    return {
        "success": True,
        "formats": ConverterFactory.get_supported_formats()
    }


@router.get("/conversion/statistics")
async def get_conversion_statistics():
    """获取转换统计信息"""
    try:
        channel_stats = channel_manager.get_channel_statistics()
        return {
            "success": True,
            "statistics": {
                "channels": channel_stats,
                "supported_formats": ConverterFactory.get_supported_formats()
            }
        }
    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))