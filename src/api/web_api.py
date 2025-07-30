"""
Web API for capability detection
"""
import asyncio
import json
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

from core.capability_detector import CapabilityDetectorFactory
from core.openai_detector import OpenAICapabilityDetector
from core.anthropic_detector import AnthropicCapabilityDetector
from core.gemini_detector import GeminiCapabilityDetector
from src.utils.config import ConfigManager, ChannelConfig
from src.utils.logger import setup_logger
from src.utils.auth import auth_manager
from src.utils.security import mask_api_key
from api.conversion_api import router as conversion_router
from api.unified_api import router as unified_router

app = FastAPI(title="AI API统一转换代理系统", version="1.0.0")
logger = setup_logger("web_api")

# 添加会话中间件
import os
import secrets
from datetime import datetime, timedelta

def get_session_secret_key():
    """获取会话密钥，如果未设置则生成并持久化随机密钥"""
    session_key = os.getenv("SESSION_SECRET_KEY")
    if not session_key:
        # 尝试从数据库获取已保存的密钥
        from src.utils.database import db_manager
        stored_key = db_manager.get_config("session_secret_key")
        if stored_key:
            session_key = stored_key
            logger.info("Using stored session secret key from database")
        else:
            # 生成新的随机密钥并存储
            session_key = secrets.token_hex(32)
            db_manager.set_config("session_secret_key", session_key)
            logger.warning("SESSION_SECRET_KEY not set, generated and stored new random key.")
            logger.info("Sessions will now persist across server restarts.")
    else:
        logger.info("Using SESSION_SECRET_KEY from environment variables")
    return session_key

secret_key = get_session_secret_key()
app.add_middleware(SessionMiddleware, secret_key=secret_key)

# 包含API路由
app.include_router(conversion_router, prefix="/api")  # 管理API
app.include_router(unified_router)  # 统一转换API（直接挂载到根路径）

# 注册检测器
CapabilityDetectorFactory.register("openai", OpenAICapabilityDetector)
CapabilityDetectorFactory.register("anthropic", AnthropicCapabilityDetector)
CapabilityDetectorFactory.register("gemini", GeminiCapabilityDetector)

# 静态文件服务
app.mount("/static", StaticFiles(directory="static"), name="static")

# 全局状态存储
detection_results: Dict[str, Any] = {}
detection_progress: Dict[str, Dict[str, Any]] = {}
task_timestamps: Dict[str, float] = {}
active_tasks: Dict[str, bool] = {}

# 简单的并发控制
MAX_CONCURRENT_TASKS = 10
TASK_EXPIRY_HOURS = 2
CLEANUP_INTERVAL_MINUTES = 30

import uuid
import time
import asyncio
from fastapi import BackgroundTasks

class DetectionRequest(BaseModel):
    """检测请求模型"""
    provider: str
    base_url: str
    api_key: str
    timeout: int = 30
    capabilities: Optional[List[str]] = None
    target_model: str

class DetectionResponse(BaseModel):
    """检测响应模型"""
    task_id: str
    message: str

class CapabilityResultModel(BaseModel):
    """能力检测结果模型"""
    capability: str
    status: str
    details: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    response_time: Optional[float] = None


class ChannelCapabilitiesModel(BaseModel):
    """渠道能力模型"""
    provider: str
    base_url: str
    models: List[str]
    capabilities: Dict[str, CapabilityResultModel]
    detection_time: str


class LoginRequest(BaseModel):
    """登录请求"""
    password: str


class LoginResponse(BaseModel):
    """登录响应"""
    success: bool
    session_token: Optional[str] = None
    message: str


def get_session_user(request: Request):
    """获取会话用户，验证是否已登录"""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="未登录")
    return True


def get_optional_session_user(request: Request):
    """可选的会话用户获取，用于页面渲染"""
    return request.session.get("authenticated", False)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """返回登录页面"""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI API统一转换代理系统 - 登录</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="login-container">
            <div class="login-form">
                <h1>AI API FORMAT CONVERSION</h1>
                <h2>管理员登录</h2>
                <form id="loginForm">
                    <div class="form-group">
                        <label for="password">管理员密码:</label>
                        <input type="password" id="password" name="password" required>
                    </div>
                    <button type="submit" class="btn-primary">登录</button>
                </form>
                <div id="error-message" class="error-message" style="display: none;"></div>
            </div>
        </div>

        <script>
            document.getElementById('loginForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const password = document.getElementById('password').value;
                const errorDiv = document.getElementById('error-message');

                try {
                    const response = await fetch('/api/login', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ password })
                    });

                    const result = await response.json();

                    if (result.success) {
                        // Cookie已自动设置，直接跳转
                        window.location.href = '/dashboard';
                    } else {
                        errorDiv.textContent = result.message;
                        errorDiv.style.display = 'block';
                    }
                } catch (error) {
                    errorDiv.textContent = '登录失败，请重试';
                    errorDiv.style.display = 'block';
                }
            });
        </script>
    </body>
    </html>
    """


@app.get("/")
async def index():
    """重定向到登录页面"""
    return RedirectResponse(url="/login", status_code=302)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """管理仪表板 - 需要认证"""
    # 检查会话认证状态
    if not request.session.get("authenticated"):
        # 认证失败，重定向到登录页
        return RedirectResponse(url="/login", status_code=302)
    
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI API统一转换代理系统</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <div class="header-bar">
                <h1>AI API FORMAT CONVERSION</h1>
                <button onclick="logout()" class="btn-secondary">注销</button>
            </div>

            <!-- 标签页导航 -->
            <div class="tab-navigation">
                <button class="tab-button active" data-tab="converter">API转换器</button>
                <button class="tab-button" data-tab="detector">能力检测器</button>
            </div>

            <!-- API转换器标签页 -->
            <div id="converter-tab" class="tab-content active">
                <!-- 渠道管理区域 -->
                <div class="channel-management">
                <h2>渠道管理</h2>
                <div class="channel-form">
                    <h3>添加新渠道</h3>
                    <form id="channelForm">
                        <div class="form-row">
                            <div class="form-group">
                                <label for="name">渠道名称:</label>
                                <input type="text" id="name" name="name" required>
                            </div>
                            <div class="form-group">
                                <label for="channel_provider">提供商:</label>
                                <select id="channel_provider" name="provider" required>
                                    <option value="">请选择</option>
                                </select>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label for="base_url">Base URL:</label>
                                <input type="url" id="base_url" name="base_url" required>
                            </div>
                            <div class="form-group">
                                <label for="api_key">API密钥:</label>
                                <input type="password" id="api_key" name="api_key" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label for="custom_key">自定义Key:</label>
                                <input type="text" id="custom_key" name="custom_key" placeholder="用户调用时使用的key，例如：my-key-123" required>
                                <small class="form-hint">用户调用API时使用此key进行身份验证</small>
                            </div>
                            <div class="form-group">
                                <!-- 占位，保持布局 -->
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label for="timeout">超时时间(秒):</label>
                                <input type="number" id="timeout" name="timeout" value="30" min="10" max="300">
                            </div>
                            <div class="form-group">
                                <label for="max_retries">重试次数:</label>
                                <input type="number" id="max_retries" name="max_retries" value="3" min="1" max="10">
                            </div>
                        </div>
                        <button type="submit" class="btn-primary">添加渠道</button>
                    </form>
                </div>
                
                <div class="channel-list">
                    <h3>已配置渠道</h3>
                    <div id="channelsList">
                        <p>正在加载渠道列表...</p>
                    </div>
                </div>
            </div>
            
            <!-- API使用说明区域 -->
            <div class="api-usage">
                <h2>API使用说明</h2>
                <div class="usage-description">
                    <p><strong>核心功能：</strong>AI API格式统一转换代理系统，支持OpenAI、Anthropic、Gemini三种格式的相互转换</p>
                    <p><strong>智能路由：</strong>根据请求路径自动识别API格式，根据自定义key识别目标渠道，自动进行格式转换</p>
                </div>
                <div class="usage-notes">
                    <h4>工作原理</h4>
                    <ol>
                        <li><strong>格式识别：</strong>根据请求路径自动识别源API格式（OpenAI/Anthropic/Gemini）</li>
                        <li><strong>渠道路由：</strong>根据自定义key查找对应的目标渠道配置</li>
                        <li><strong>格式转换：</strong>自动将请求格式转换为目标渠道的API格式</li>
                        <li><strong>请求转发：</strong>调用真实的AI服务API</li>
                        <li><strong>响应转换：</strong>将响应格式转换回源格式并返回给客户端</li>
                    </ol>
                    <h4>支持的转换</h4>
                    <ul>
                        <li>OpenAI ↔ Anthropic ↔ Gemini（任意格式间相互转换）</li>
                        <li>流式和非流式请求</li>
                        <li>函数调用、视觉理解、结构化输出等高级功能</li>
                        <li>自动模型映射和参数适配</li>
                    </ul>
                </div>
            </div>
            </div>

            <!-- 能力检测器标签页 -->
            <div id="detector-tab" class="tab-content">
                <div class="detection-form">
                    <h2>能力检测</h2>
                    <form id="detectionForm">
                        <div class="form-group">
                            <label for="provider">AI提供商:</label>
                            <select id="provider" name="provider" required>
                                <option value="">请选择</option>
                                <option value="openai">OpenAI</option>
                                <option value="anthropic">Anthropic</option>
                                <option value="gemini">Google Gemini</option>
                            </select>
                        </div>

                        <div class="form-group">
                            <label for="detection_base_url">API基础URL:</label>
                            <input type="url" id="detection_base_url" name="base_url" required>
                        </div>

                        <div class="form-group">
                            <label for="detection_api_key">API密钥:</label>
                            <input type="password" id="detection_api_key" name="api_key" required>
                        </div>

                        <div class="form-group">
                            <label for="detection_timeout">超时时间(秒):</label>
                            <input type="number" id="detection_timeout" name="timeout" value="30" min="10" max="300">
                        </div>
                    
                    <div class="form-group">
                        <label for="target_model">指定模型 (必填):</label>
                        <div class="model-selection-container">
                            <select id="target_model_select" name="target_model_select" onchange="updateTargetModel()">
                                <option value="">请选择模型</option>
                            </select>
                            <button type="button" id="fetch_models_btn" class="btn-secondary" onclick="fetchModels()">获取模型列表</button>
                        </div>
                        <input type="text" id="target_model" name="target_model" placeholder="或手动输入模型名称，例如：gpt-4o-mini" style="margin-top: 10px;" required>
                        <small class="form-hint">必须指定要检测的模型</small>
                    </div>
                    
                    <div class="form-group">
                        <label>要检测的能力:</label>
                        <div class="capabilities-grid">
                            <label>
                                <input type="checkbox" name="capabilities" value="basic_chat">
                                <span class="capability-text">基础聊天</span>
                                <span class="capability-icon"></span>
                            </label>
                            <label>
                                <input type="checkbox" name="capabilities" value="streaming">
                                <span class="capability-text">流式输出</span>
                                <span class="capability-icon"></span>
                            </label>
                            <label>
                                <input type="checkbox" name="capabilities" value="system_message">
                                <span class="capability-text">系统消息</span>
                                <span class="capability-icon"></span>
                            </label>
                            <label>
                                <input type="checkbox" name="capabilities" value="function_calling">
                                <span class="capability-text">函数调用</span>
                                <span class="capability-icon"></span>
                            </label>
                            <label>
                                <input type="checkbox" name="capabilities" value="structured_output">
                                <span class="capability-text">结构化输出</span>
                                <span class="capability-icon"></span>
                            </label>
                            <label>
                                <input type="checkbox" name="capabilities" value="vision">
                                <span class="capability-text">视觉理解</span>
                                <span class="capability-icon"></span>
                            </label>
                        </div>
                        <div class="form-actions">
                            <button type="button" onclick="selectAllCapabilities()">全选</button>
                            <button type="button" onclick="clearAllCapabilities()">清空</button>
                        </div>
                    </div>
                    
                    <button type="submit" class="btn-primary">开始检测</button>
                </form>
            </div>
            
            <div id="progress" class="progress-section" style="display: none;">
                <h2>检测进度</h2>
                <div id="progress-bar" class="progress-bar">
                    <div id="progress-fill" class="progress-fill"></div>
                </div>
                <div id="progress-text">准备中...</div>
            </div>
            
                <div id="results" class="results-section" style="display: none;">
                    <h2>检测结果</h2>
                    <div id="results-content"></div>
                </div>
            </div>
        </div>

        <script>
            // 注销功能
            async function logout() {
                try {
                    const response = await fetch('/api/logout', {
                        method: 'POST'
                    });
                    if (response.ok) {
                        window.location.href = '/login';
                    }
                } catch (error) {
                    console.error('注销失败:', error);
                    window.location.href = '/login'; // 即使失败也跳转
                }
            }

            // 页面加载时初始化
            document.addEventListener('DOMContentLoaded', function() {
                console.log('Dashboard loaded, user authenticated by server');

                console.log('Token exists, loading script.js...');
                // 认证成功，加载主要功能脚本
                const script = document.createElement('script');
                script.src = '/static/script.js';
                script.onload = function() {
                    console.log('script.js loaded successfully');
                    console.log('apiConverter available:', typeof apiConverter !== 'undefined');
                };
                script.onerror = function() {
                    console.error('Failed to load script.js');
                };
                document.head.appendChild(script);
            });
        </script>
    </body>
    </html>
    """


@app.post("/api/login")
async def login(login_request: LoginRequest, request: Request):
    """管理员登录"""
    # 验证密码
    if auth_manager.verify_admin_password(login_request.password):
        # 设置会话
        request.session["authenticated"] = True
        request.session["login_time"] = datetime.now().isoformat()
        
        return JSONResponse(
            content={
                "success": True, 
                "message": "登录成功"
            }
        )
    else:
        return JSONResponse(
            content={
                "success": False,
                "message": "密码错误"
            },
            status_code=401
        )


@app.post("/api/logout")
async def logout(request: Request):
    """管理员注销"""
    # 清除会话
    request.session.clear()
    
    return JSONResponse(
        content={
            "success": True, 
            "message": "注销成功"
        }
    )




@app.get("/api/providers")
async def get_providers():
    """获取支持的提供商"""
    return {
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "description": "OpenAI (GPT-4o, GPT-3.5-turbo 等)",
                "default_url": "https://api.openai.com/v1"
            },
            {
                "id": "anthropic",
                "name": "Anthropic",
                "description": "Anthropic (Claude-3, Claude-3.5 等)",
                "default_url": "https://api.anthropic.com"
            },
            {
                "id": "gemini",
                "name": "Google Gemini",
                "description": "Google Gemini (Gemini-1.5, Gemini-2.0 等)",
                "default_url": "https://generativelanguage.googleapis.com/v1beta"
            }
        ]
    }


@app.get("/api/channels")
async def get_channels(_: bool = Depends(get_session_user)):
    """获取所有渠道"""

    try:
        from src.channels.channel_manager import ChannelManager
        manager = ChannelManager()
        channels = manager.get_all_channels()

        return {
            "success": True,
            "channels": [
                {
                    "id": channel.id,
                    "name": channel.name,
                    "provider": channel.provider,
                    "base_url": channel.base_url,
                    "api_key": mask_api_key(channel.api_key),
                    "custom_key": channel.custom_key,
                    "timeout": channel.timeout,
                    "max_retries": channel.max_retries,
                    "enabled": channel.enabled,
                    "models_mapping": channel.models_mapping,
                    "created_at": channel.created_at,
                    "updated_at": channel.updated_at
                }
                for channel in channels
            ]
        }
    except Exception as e:
        logger.error(f"Failed to get channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/channels")
async def create_channel(
    channel_data: dict,
    _: bool = Depends(get_session_user)
):
    """创建新渠道"""

    try:
        from src.channels.channel_manager import ChannelManager
        manager = ChannelManager()

        channel_id = manager.add_channel(
            name=channel_data["name"],
            provider=channel_data["provider"],
            base_url=channel_data["base_url"],
            api_key=channel_data["api_key"],
            custom_key=channel_data["custom_key"],
            timeout=channel_data.get("timeout", 30),
            max_retries=channel_data.get("max_retries", 3),
            models_mapping=channel_data.get("models_mapping")
        )

        return {
            "success": True,
            "channel_id": channel_id,
            "message": "渠道创建成功"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    channel_data: dict,
    _: bool = Depends(get_session_user)
):
    """更新渠道"""

    try:
        from src.channels.channel_manager import ChannelManager
        manager = ChannelManager()

        # 确保空的api_key不会被传递，使用None而不是空字符串
        api_key = channel_data.get("api_key")
        if api_key is not None and api_key.strip() == "":
            api_key = None
        
        success = manager.update_channel(
            channel_id=channel_id,
            name=channel_data.get("name"),
            base_url=channel_data.get("base_url"),
            api_key=api_key,
            custom_key=channel_data.get("custom_key"),
            timeout=channel_data.get("timeout"),
            max_retries=channel_data.get("max_retries"),
            enabled=channel_data.get("enabled"),
            models_mapping=channel_data.get("models_mapping")
        )

        if success:
            return {
                "success": True,
                "message": "渠道更新成功"
            }
        else:
            raise HTTPException(status_code=404, detail="渠道不存在")

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    _: bool = Depends(get_session_user)
):
    """删除渠道"""

    try:
        from src.channels.channel_manager import ChannelManager
        manager = ChannelManager()

        success = manager.delete_channel(channel_id)

        if success:
            return {
                "success": True,
                "message": "渠道删除成功"
            }
        else:
            raise HTTPException(status_code=404, detail="渠道不存在")

    except Exception as e:
        logger.error(f"Failed to delete channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/capabilities")
async def get_capabilities():
    """获取所有可检测的能力"""
    config_manager = ConfigManager()
    capabilities = config_manager.get_all_capabilities()
    
    return {
        "capabilities": [
            {
                "id": name,
                "name": name,
                "description": config.description
            }
            for name, config in capabilities.items()
        ]
    }






@app.post("/api/detect", response_model=DetectionResponse)
async def start_detection(request: DetectionRequest, background_tasks: BackgroundTasks):
    """开始能力检测"""
    
    # 简单的并发控制
    active_count = len([task for task, is_active in active_tasks.items() if is_active])
    if active_count >= MAX_CONCURRENT_TASKS:
        raise HTTPException(
            status_code=429, 
            detail=f"服务器繁忙，请稍后重试 ({active_count}/{MAX_CONCURRENT_TASKS})"
        )
    
    # 生成任务ID
    task_id = str(uuid.uuid4())
    
    # 创建配置
    try:
        config = ChannelConfig(
            provider=request.provider,
            base_url=request.base_url,
            api_key=request.api_key,
            timeout=request.timeout
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # 初始化进度和时间戳
    current_time = time.time()
    task_timestamps[task_id] = current_time
    active_tasks[task_id] = True
    
    detection_progress[task_id] = {
        "status": "starting",
        "progress": 0,
        "current_capability": None,
        "completed_capabilities": [],
        "total_capabilities": 0
    }
    
    # 后台执行检测
    logger.info(f"启动检测任务: {request.provider} - {request.target_model}")
    background_tasks.add_task(run_detection, task_id, config, request.capabilities, request.target_model)
    
    return DetectionResponse(
        task_id=task_id,
        message="检测任务已开始"
    )


@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    """获取检测进度"""
    if task_id not in detection_progress:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return detection_progress[task_id]


@app.get("/api/results/{task_id}")
async def get_results(task_id: str):
    """获取检测结果"""
    if task_id not in detection_results:
        raise HTTPException(status_code=404, detail="结果不存在")
    
    return detection_results[task_id]


@app.post("/api/fetch_models")
async def fetch_models(request: dict):
    """获取模型列表"""
    try:
        provider = request.get("provider")
        base_url = request.get("base_url")
        api_key = request.get("api_key")
        
        if not all([provider, base_url, api_key]):
            raise HTTPException(status_code=400, detail="缺少必要参数")
        
        # 创建配置
        config = ChannelConfig(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            timeout=30
        )
        
        # 创建检测器
        detector = CapabilityDetectorFactory.create(config)
        
        # 获取模型列表
        logger.info(f"正在获取 {provider} 的模型列表...")
        models = await detector.detect_models()
        logger.info(f"成功获取 {len(models)} 个模型")
        
        return {"models": models}
        
    except Exception as e:
        logger.error(f"模型获取失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def run_detection(task_id: str, config: ChannelConfig, selected_capabilities: Optional[List[str]], target_model: str):
    """运行能力检测"""
    try:
        # 更新进度
        detection_progress[task_id]["status"] = "running"
        
        # 创建检测器
        detector = CapabilityDetectorFactory.create(config)
        
        # 设置目标模型（现在是必填的）
        detector.target_model = target_model
        
        # 设置进度回调
        async def progress_callback(progress_data):
            """更新检测进度"""
            if task_id in detection_progress:
                detection_progress[task_id].update(progress_data)
        
        detector.progress_callback = progress_callback
        
        # 执行检测
        if selected_capabilities:
            results = await detector.detect_selected_capabilities(selected_capabilities)
        else:
            results = await detector.detect_all_capabilities()
        
        # 保存结果
        detection_results[task_id] = results.to_dict()
        
        # 更新进度
        if task_id in detection_progress:
            detection_progress[task_id]["status"] = "completed"
            detection_progress[task_id]["progress"] = 100
        
        logger.info(f"检测任务完成: {task_id}")
        
    except Exception as e:
        logger.error(f"检测任务失败: {e}")
        if task_id in detection_progress:
            detection_progress[task_id]["status"] = "error"
            detection_progress[task_id]["error"] = str(e)
    finally:
        # 标记任务为非活跃状态
        active_tasks[task_id] = False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)