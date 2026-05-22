# 快速开始指南

## 1. 安装依赖

### 方式A: 使用启动脚本（推荐）

**Windows:**
```powershell
.\run.bat
```

**Linux/macOS:**
```bash
bash run.sh
```

### 方式B: 手动安装

```bash
# 创建虚拟环境（可选但推荐）
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

## 2. 启动后端服务

```bash
# 默认配置（localhost:8000）
python -m realtime_backend.run

# 自定义主机和端口
python -m realtime_backend.run --host 0.0.0.0 --port 8000

# 开发模式（自动重载）
python -m realtime_backend.run --reload --log-level debug
```

服务启动后会显示：
```
==================================================
Realtime Simulation Backend
==================================================
Server: 0.0.0.0:8000
Log Level: info
Auto-reload: False
Client WebSocket: ws://0.0.0.0:8000/ws/client
Core WebSocket: ws://0.0.0.0:8000/ws/core
Documentation: http://0.0.0.0:8000/docs
==================================================
```

## 3. 验证服务

```bash
# 健康检查
curl http://localhost:8000/health

# 获取状态
curl http://localhost:8000/status

# 查看 API 文档
# 在浏览器中打开：http://localhost:8000/docs
```

## 4. 测试客户端连接

```bash
# 方式A: 使用演示脚本
python demo.py client

# 方式B: 检查后端健康状态
python demo.py health

# 方式C: 同时测试客户端和核心
python demo.py concurrent
```

## 5. 配置调整

编辑 `config.yaml` 文件：

```yaml
host: "0.0.0.0"        # 监听地址
port: 8000             # 监听端口
log_level: "info"      # 日志级别

allowed_origins:
  - "*"                # 允许所有源（生产环境应该指定具体域名）

client_ws_path: "/ws/client"   # 前端客户端连接路径
core_ws_path: "/ws/core"       # 仿真核心连接路径
```

## 6. 前端集成示例

### HTML + JavaScript

```html
<!DOCTYPE html>
<html>
<head>
    <title>Realtime Simulation Frontend</title>
</head>
<body>
    <h1>Simulation Status</h1>
    <div id="status">Connecting...</div>
    <div id="data"></div>

    <script>
        const socket = new WebSocket("ws://localhost:8000/ws/client");

        socket.onopen = function() {
            document.getElementById("status").textContent = "Connected";
            
            // 发送命令示例
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
            const data = JSON.parse(event.data);
            if (data.message_type === "state_update") {
                const payload = data.payload;
                document.getElementById("data").innerHTML = `
                    <p>Timestamp: ${payload.timestamp}</p>
                    <p>Satellites: ${Object.keys(payload.satellite_positions).length}</p>
                    <p>Bandwidth: ${JSON.stringify(payload.bandwidth_utilization)}</p>
                `;
            }
        };

        socket.onerror = function(error) {
            document.getElementById("status").textContent = "Error: " + error;
        };

        socket.onclose = function() {
            document.getElementById("status").textContent = "Disconnected";
        };
    </script>
</body>
</html>
```

### Python 客户端

```python
import asyncio
import json
import websockets

async def main():
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
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            print(f"State: {data['payload']['timestamp']}")

asyncio.run(main())
```

## 7. 仿真核心集成示例

```python
import asyncio
import json
import websockets
from datetime import datetime

async def send_simulation_data():
    uri = "ws://localhost:8000/ws/core"
    async with websockets.connect(uri) as websocket:
        while True:
            # 构造状态数据
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
            
            # 接收并处理命令
            try:
                command = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                data = json.loads(command)
                print(f"Received: {data['payload']['action']}")
            except asyncio.TimeoutError:
                pass
            
            await asyncio.sleep(1)

asyncio.run(send_simulation_data())
```

## 常见问题

### Q: 如何改变监听端口？
A: 使用 `--port` 参数：
```bash
python -m realtime_backend.run --port 9000
```

### Q: 如何在生产环境部署？
A: 参考 README.md 中的 Docker 部署章节。

### Q: 如何只允许特定的源访问？
A: 在 `config.yaml` 中修改 `allowed_origins`：
```yaml
allowed_origins:
  - "http://localhost:3000"
  - "https://example.com"
```

### Q: 如何查看详细的调试信息？
A: 使用 debug 日志级别：
```bash
python -m realtime_backend.run --log-level debug
```

## 文件说明

- `run.py` - 启动脚本（支持命令行参数）
- `run.sh` - Linux/macOS 启动脚本
- `run.bat` - Windows 启动脚本
- `main.py` - FastAPI 应用和 WebSocket 端点
- `core.py` - 连接管理器
- `config.py` - 配置管理
- `schemas.py` - 数据模型
- `config.yaml` - 配置文件
- `requirements.txt` - Python 依赖
- `demo.py` - 演示客户端
- `README.md` - 完整文档
- `QUICKSTART.md` - 本文档

## 下一步

1. 查看 `README.md` 了解更多功能
2. 查看 API 文档：http://localhost:8000/docs
3. 测试演示脚本：`python demo.py`
4. 集成到你的前端或仿真核心

有任何问题，请参考 README.md 的"故障排除"章节。
