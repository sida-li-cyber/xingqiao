# 仿真实时后端服务 (Realtime Simulation Backend)

一个基于 **FastAPI** 和 **WebSocket** 的实时仿真可视化后端服务，用于支持前端可视化与仿真核心的实时交互。

> **Hypatia Integration**: This service is part of the enhanced Hypatia LEO satellite network simulation framework. It sits between the simulation core (e.g., `demo_sim_core.py` or ns-3) and the CesiumJS frontend (`satviz/`), relaying real-time state updates and control commands via WebSocket.

## 功能特性

### 1. 仿真状态实时推送 📊
- 后端接收仿真核心发来的实时状态数据
- 支持以下数据类型：
  - **卫星位置** (Satellite Positions)
  - **链路状态** (Link Status)
  - **路由信息** (Routing Information)
  - **带宽利用率** (Bandwidth Utilization)
- 通过 WebSocket 将状态实时广播给所有连接的前端客户端
- 自动过滤断开的连接

### 2. 前端命令接收与转发 🎮
- 接收前端发来的控制命令
- 支持的命令类型：
  - `pause`: 暂停仿真
  - `resume`: 继续仿真
  - `speed`: 调整仿真速度
  - `metrics`: 切换要展示的指标
  - 自定义命令
- 将命令转发给仿真核心
- 返回命令执行确认

### 3. 跨域支持 🌐
- 默认允许来自所有域名的请求
- 支持 OPTIONS 预检请求
- 可通过配置文件自定义允许的域名列表

### 4. 灵活的配置管理 ⚙️
- 通过 YAML 配置文件配置服务参数
- 支持环境变量覆盖配置
- 可配置的选项：
  - 服务器主机和端口
  - WebSocket 端点路径
  - CORS 允许的源
  - 日志级别

## 快速开始

### 系统要求
- Python 3.10+
- pip 或 conda

### 安装依赖

```bash
# 方式1: 使用启动脚本 (推荐)
# Linux/macOS:
bash run.sh

# Windows:
run.bat

# 方式2: 手动安装
pip install -r requirements.txt
```

### 启动服务

```bash
# 方式1: 使用启动脚本
# Linux/macOS:
bash run.sh

# Windows:
run.bat

# 方式2: 直接运行
python -m realtime_backend.run --host 0.0.0.0 --port 8000

# 方式3: 使用自定义参数
python -m realtime_backend.run --host 127.0.0.1 --port 9000 --log-level debug

# 方式4: 开发模式（自动重载）
python -m realtime_backend.run --reload
```

### 命令行参数

```
usage: python -m realtime_backend.run [-h] [--host HOST] [--port PORT] 
                                       [--log-level {critical,error,warning,info,debug}] 
                                       [--reload]

optional arguments:
  -h, --help            show this help message and exit
  --host HOST           Host to bind the server to (default: 0.0.0.0)
  --port PORT           Port to bind the server to (default: 8000)
  --log-level {critical,error,warning,info,debug}
                        Log level (default: info)
  --reload              Enable auto-reload on file changes (for development)
```

### 环境变量配置

```bash
export APP_HOST=0.0.0.0
export APP_PORT=8000
export APP_LOG_LEVEL=info

python -m realtime_backend.run
```

## 配置文件

编辑 `config.yaml` 文件来配置服务：

```yaml
# 服务器配置
host: "0.0.0.0"        # 监听主机地址
port: 8000             # 监听端口
reload: false          # 是否启用自动重载
log_level: "info"      # 日志级别

# CORS配置
allowed_origins:
  - "*"                # 允许的源（"*" 表示允许所有）
  # - "http://localhost:3000"
  # - "https://example.com"

# WebSocket路径配置
client_ws_path: "/ws/client"   # 客户端WebSocket端点
core_ws_path: "/ws/core"       # 核心WebSocket端点

# 消息类型配置
state_message_type: "state_update"    # 状态更新消息类型
command_message_type: "command"       # 命令消息类型
```

## API 文档

### 健康检查

```http
GET /health HTTP/1.1
Host: localhost:8000

HTTP/1.1 200 OK
{
  "status": "ok",
  "clients_connected": 2,
  "cores_connected": 1
}
```

### 服务器状态

```http
GET /status HTTP/1.1
Host: localhost:8000

HTTP/1.1 200 OK
{
  "status": "running",
  "clients_connected": 2,
  "cores_connected": 1,
  "config": {
    "host": "0.0.0.0",
    "port": 8000,
    "client_ws_path": "/ws/client",
    "core_ws_path": "/ws/core"
  }
}
```

### WebSocket 端点

#### 客户端连接 (`ws://localhost:8000/ws/client`)

用于前端客户端连接，用来接收状态更新和发送命令。

**发送命令**:
```json
{
  "message_type": "command",
  "payload": {
    "action": "pause",
    "params": {
      "reason": "user_request"
    }
  }
}
```

**接收状态**:
```json
{
  "message_type": "state_update",
  "payload": {
    "satellite_positions": {
      "sat_1": {"lat": 45.0, "lon": 120.0, "alt": 500000}
    },
    "link_status": {
      "link_1": {"status": "active", "capacity": 1000}
    },
    "routing": {
      "route_1": ["sat_1", "sat_2", "sat_3"]
    },
    "bandwidth_utilization": {
      "sat_1": 0.75
    },
    "timestamp": "2024-01-01T12:00:00.000000"
  }
}
```

#### 核心连接 (`ws://localhost:8000/ws/core`)

用于仿真核心连接，用来发送状态更新和接收命令。

**发送状态**:
```json
{
  "message_type": "state_update",
  "payload": {
    "satellite_positions": {...},
    "link_status": {...},
    "routing": {...},
    "bandwidth_utilization": {...},
    "timestamp": "2024-01-01T12:00:00.000000"
  }
}
```

**接收命令**:
```json
{
  "message_type": "command",
  "payload": {
    "action": "speed",
    "params": {
      "speed_factor": 2.0
    }
  }
}
```

## 消息格式

### 状态更新消息 (State Update)

```python
{
    "message_type": "state_update",
    "payload": {
        "satellite_positions": dict,    # 卫星位置数据
        "link_status": dict,            # 链路状态数据
        "routing": dict,                # 路由信息
        "bandwidth_utilization": dict,  # 带宽利用率
        "timestamp": str                # ISO格式时间戳
    }
}
```

### 命令消息 (Command Message)

```python
{
    "message_type": "command",
    "payload": {
        "action": str,                  # 命令类型
        "params": dict | null           # 可选参数
    }
}
```

### 确认消息 (Acknowledgment)

```python
{
    "message_type": "ack",
    "payload": {
        "status": str,                  # 状态 (forwarded/received)
        "action": str,                  # 原始命令类型
        "params": dict | null,          # 原始参数
        "timestamp": str                # 处理时间戳
    }
}
```

### 错误消息 (Error)

```python
{
    "message_type": "error",
    "payload": {
        "error": str,                   # 错误说明
        "detail": str | null            # 详细信息
    }
}
```

## 使用示例

### Python 客户端

```python
import asyncio
import json
import websockets

async def client_example():
    uri = "ws://localhost:8000/ws/client"
    async with websockets.connect(uri) as websocket:
        # 发送命令
        command = {
            "message_type": "command",
            "payload": {
                "action": "speed",
                "params": {"speed_factor": 2.0}
            }
        }
        await websocket.send(json.dumps(command))
        
        # 接收状态更新
        response = await websocket.recv()
        print("Received:", json.loads(response))

asyncio.run(client_example())
```

### JavaScript 客户端

```javascript
const socket = new WebSocket("ws://localhost:8000/ws/client");

socket.onopen = function(event) {
    // 发送命令
    const command = {
        message_type: "command",
        payload: {
            action: "pause",
            params: {}
        }
    };
    socket.send(JSON.stringify(command));
};

socket.onmessage = function(event) {
    // 接收状态更新
    const data = JSON.parse(event.data);
    console.log("State update:", data.payload);
};

socket.onerror = function(error) {
    console.error("WebSocket error:", error);
};

socket.onclose = function(event) {
    console.log("WebSocket closed");
};
```

### 仿真核心连接示例 (Python)

```python
import asyncio
import json
import websockets
from datetime import datetime

async def core_example():
    uri = "ws://localhost:8000/ws/core"
    async with websockets.connect(uri) as websocket:
        while True:
            # 生成状态数据
            state = {
                "message_type": "state_update",
                "payload": {
                    "satellite_positions": {
                        "sat_1": {"lat": 45.0, "lon": 120.0, "alt": 500000}
                    },
                    "link_status": {
                        "link_1": {"status": "active", "capacity": 1000}
                    },
                    "routing": {
                        "route_1": ["sat_1", "sat_2"]
                    },
                    "bandwidth_utilization": {
                        "sat_1": 0.75
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
            
            # 发送状态
            await websocket.send(json.dumps(state))
            
            # 接收命令
            try:
                command = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                print("Received command:", json.loads(command))
            except asyncio.TimeoutError:
                pass
            
            await asyncio.sleep(1)

asyncio.run(core_example())
```

## 日志输出

服务会输出结构化的日志信息：

```
2024-01-01 12:00:00,000 - realtime_backend.main - INFO - Application starting...
2024-01-01 12:00:00,100 - realtime_backend.main - INFO - Server configuration - Host: 0.0.0.0, Port: 8000
2024-01-01 12:00:00,200 - realtime_backend.core - INFO - ConnectionManager initialized
2024-01-01 12:00:01,000 - realtime_backend.main - INFO - Client 123456 attempting to connect
2024-01-01 12:00:01,050 - realtime_backend.main - INFO - Client 123456 connected successfully. Total clients: 1
2024-01-01 12:00:02,000 - realtime_backend.main - INFO - Core 789012 attempting to connect
2024-01-01 12:00:02,050 - realtime_backend.main - INFO - Core 789012 connected successfully. Total cores: 1
```

## 文件结构

```
realtime_backend/
├── __init__.py           # 包初始化文件
├── main.py              # FastAPI 应用和 WebSocket 端点
├── core.py              # 连接管理和消息转发
├── config.py            # 配置管理
├── schemas.py           # Pydantic 数据模型
├── run.py               # 启动脚本
├── run.sh               # Linux/macOS 启动脚本
├── run.bat              # Windows 启动脚本
├── config.yaml          # 配置文件
├── requirements.txt     # 依赖列表
├── demo.py              # 测试/演示客户端（前端与核心模拟）
├── QUICKSTART.md        # 中文快速入门指南
└── README.md            # 本文档
```

## 故障排除

### 问题：端口已被占用
```bash
# Linux/macOS: 查看占用端口的进程
lsof -i :8000

# Windows: 查看占用端口的进程
netstat -ano | findstr :8000

# 使用不同端口
python -m realtime_backend.run --port 9000
```

### 问题：客户端无法连接
- 检查防火墙设置
- 确保服务器正常运行 (`GET /health`)
- 检查 WebSocket URL 是否正确
- 查看服务器日志了解详细信息

### 问题：消息格式错误
- 确保发送的消息符合 JSON 格式
- 检查 `message_type` 字段是否正确
- 参考 API 文档中的消息格式示例

## 性能考虑

- 服务器支持同时多个客户端和核心连接
- 每个状态广播都会转发给所有连接的客户端
- 断开的连接会自动清理，避免内存泄漏
- 建议监控服务器资源使用情况

## 与 Hypatia 集成测试

完整集成测试套件位于 `hypatia-master/integration_tests/`：

```bash
# 1. 启动后端
PYTHONPATH=/path/to/hypatia-master:/path/to/realtime_backend \
  python -m realtime_backend.run --port 8000

# 2. 运行集成测试
cd /path/to/hypatia-master
PYTHONPATH=/path/to/hypatia-master:/path/to/realtime_backend \
  python integration_tests/test_realtime_integration.py

# 3. 运行端到端实时测试（需要 demo_sim_core）
PYTHONPATH=/path/to/hypatia-master:/path/to/realtime_backend \
  python integration_tests/test_live_demo.py
```

测试内容：
- 后端健康检查和状态端点
- WebSocket 连接管理 (客户端 / 核心)
- simulation_init 广播
- state_update 实时推送
- 命令转发 (play, pause, speed, timeline, metrics, filter)
- 多客户端支持
- 无核心时的错误处理
- demo_sim_core 兼容性
- 前端文件完整性

## 开发

### 本地开发
```bash
python -m realtime_backend.run --reload --log-level debug
```

### 生成 API 文档
启动服务后访问：
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 部署

### Docker 部署

项目暂未包含预置 Dockerfile，可参考以下示例自行构建：

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "-m", "realtime_backend.run", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t realtime-backend .
docker run -p 8000:8000 realtime-backend
```

## 许可证

该项目采用 MIT 许可证。

## 贡献

欢迎提交问题和拉取请求！
