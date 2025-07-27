# 使用官方Python运行时作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 复制requirements.txt文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建必要的目录
RUN mkdir -p data logs

# 设置环境变量
ENV PYTHONPATH=/app/src
ENV PORT=8000

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "web_server.py", "--host", "0.0.0.0", "--port", "8000"] 