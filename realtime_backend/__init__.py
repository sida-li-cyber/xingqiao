"""
Realtime Simulation Backend

一个基于FastAPI和WebSocket的实时仿真后端服务，支持：
- 实时状态推送：接收仿真核心的状态数据并广播给前端客户端
- 命令转发：接收前端命令并转发给仿真核心
- 跨域支持：支持来自任意域名的前端连接
- 灵活配置：通过YAML配置文件或环境变量配置服务参数

主要模块：
- main.py: FastAPI应用和WebSocket端点
- core.py: 连接管理和消息转发逻辑
- config.py: 配置管理
- schemas.py: 消息数据结构定义

使用方式：
    python -m realtime_backend.run --host 0.0.0.0 --port 8000
"""

__version__ = "0.1.0"
__author__ = "Simulation Team"
