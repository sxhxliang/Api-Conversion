# 多渠道AI API统一转换代理系统

## 项目概述

这是一个多渠道AI API统一转换代理系统，支持OpenAI、Anthropic Claude、Google Gemini三种API格式的相互转换，具备多渠道管理和全面能力检测功能。

## 核心功能

### 1. 全面能力检测
- **基础能力**：聊天对话、流式输出、系统消息、多轮对话
- **高级能力**：视觉理解、文件上传、结构化输出、JSON模式
- **工具能力**：函数调用、工具使用、代码执行
- **模型检测**：自动获取支持的模型列表
- **多平台支持**：OpenAI、Anthropic Claude、Google Gemini

### 2. 3D雕塑式Web界面（已实现）
- **高级3D视觉效果**：受Monolith Studio启发的雕塑式动画
- **交互式检测界面**：直观的Web UI进行能力检测
- **实时结果展示**：动态显示检测进度和结果
- **响应式设计**：支持桌面和移动设备

### 3. 多格式互转支持（规划中）
- **支持的API格式**：OpenAI、Anthropic Claude、Google Gemini
- **转换方向**：任意两种格式之间的双向转换（共6种转换路径）
- **格式兼容性**：保持原始API的所有功能特性

### 4. 多渠道管理（规划中）
- **渠道类型**：官方API、代理服务、自建服务
- **智能选择**：支持优先级配置和负载均衡
- **故障转移**：自动切换到备用渠道

## 快速开始

1. **安装依赖**
```bash
pip install -r requirements.txt
```

2. **启动Web服务**
```bash
python web_server.py
```

3. **访问Web界面**
- 打开浏览器访问：http://localhost:8000
- 选择AI提供商，输入API配置
- 一键检测所有能力，查看详细结果

## 部署指南

### Render 平台部署（推荐）

项目已配置好 `render.yaml`，支持一键部署：

1. **将代码推送到GitHub**
2. **连接Render平台**：https://dashboard.render.com
3. **自动部署**：Render会自动读取配置并部署

配置详情：
- **构建命令**：`pip install -r requirements.txt`
- **启动命令**：`python web_server.py --host 0.0.0.0 --port $PORT`
- **环境变量**：`PYTHONPATH=/opt/render/project/src`

### Docker 部署

```bash
# 构建镜像
docker build -t ai-api-detector .

# 运行容器  
docker run -p 8000:8000 ai-api-detector
```

### 本地开发

```bash
# 克隆项目
git clone <repository-url>
cd Api-Conversion

# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python web_server.py --debug
```

## 支持的能力检测

| 能力 | 描述 | OpenAI | Anthropic | Gemini |
|------|------|--------|-----------|--------|
| 基础聊天 | 基本对话功能 | ✅ | ✅ | ✅ |
| 流式输出 | 实时流式响应 | ✅ | ✅ | ✅ |
| 系统消息 | 系统指令支持 | ✅ | ✅ | ✅ |
| 函数调用 | 工具使用能力 | ✅ | ✅ | ✅ |
| 结构化输出 | JSON格式输出 | ✅ | ✅ | ✅ |
| 视觉理解 | 图像分析能力 | ✅ | ✅ | ✅ |

## 项目结构

```
Api-Conversion/
├── src/
│   ├── core/                    # 核心检测逻辑
│   │   ├── capability_detector.py  # 主检测器
│   │   ├── openai_detector.py      # OpenAI专用检测器
│   │   ├── anthropic_detector.py   # Anthropic专用检测器
│   │   └── gemini_detector.py      # Gemini专用检测器
│   ├── api/                     # Web API实现
│   │   └── web_api.py              # FastAPI服务
│   └── utils/                   # 工具函数
│       ├── config.py               # 配置管理
│       ├── logger.py               # 日志处理
│       └── exceptions.py           # 异常定义
├── static/                      # Web静态资源
│   ├── style.css                   # 3D雕塑CSS样式
│   └── script.js                   # 3D交互效果JS
├── web_server.py                # Web服务器启动脚本
├── requirements.txt             # Python依赖
├── render.yaml                  # Render部署配置
├── Dockerfile                   # Docker配置
└── README.md                    # 项目说明
```

## 开发状态

- [x] 项目基础架构
- [x] 核心能力检测器
- [x] 3D雕塑式Web界面
- [x] 云平台部署配置
- [ ] 格式转换引擎
- [ ] 多渠道管理
- [ ] API服务端点
- [ ] 测试套件

## Web界面特色功能

### 3D雕塑式动画系统
- **主雕塑**：中心大型3D元素，响应滚动和鼠标交互
- **辅助雕塑**：4个分布式小型雕塑，独立浮动动画
- **粒子系统**：20个动态粒子，创造流动视觉效果
- **滚动交互**：滚动进度驱动复杂3D变换
- **鼠标跟随**：实时3D旋转和投影效果
- **性能优化**：硬件加速和响应式设计

### 增强用户体验
- **动态形状变形**：实时色相循环和渐变效果
- **玻璃形态设计**：现代化的半透明界面元素
- **可访问性支持**：支持`prefers-reduced-motion`设置
- **移动端优化**：简化版本适配小屏设备

## 下一步计划

1. **格式转换引擎**：实现OpenAI ↔ Anthropic ↔ Gemini互转
2. **多渠道管理器**：支持多个API渠道负载均衡
3. **API服务端点**：提供统一的转换API服务
4. **完善测试套件**：单元测试、集成测试、端到端测试
5. **性能优化**：缓存机制、并发处理优化

## 常见问题

### 1. 依赖安装问题
```bash
# 如果pip安装失败，尝试使用conda
conda install fastapi uvicorn httpx

# 或升级pip
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. 启动问题
```bash
# 确保在项目根目录运行
cd Api-Conversion
python web_server.py

# 如果仍有导入错误，设置PYTHONPATH
export PYTHONPATH=$PWD/src:$PYTHONPATH
```

### 3. API密钥配置
- 在Web界面中输入API密钥时，支持`sk-`开头的OpenAI密钥
- Anthropic密钥通常以`ant-`开头
- Gemini密钥可以从Google AI Studio获取

## 贡献指南

请查看 [CLAUDE.md](./CLAUDE.md) 了解详细的开发指南和架构说明。

## 许可证

MIT License