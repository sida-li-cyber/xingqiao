# Hypatia 增强版 LEO 卫星网络仿真框架 — 用户手册

---

## 1. 项目概述

本项目是 [Hypatia](https://github.com/snkas/hypatia) LEO（低地球轨道）卫星网络仿真框架的增强版。原始 Hypatia 由 ETH Zurich 的 Simon Kassing 等人开发，发表于 ACM IMC 2020，是一个用于生成 LEO 卫星星座、进行 ns-3 包级网络仿真和可视化的学术研究框架。

**增强内容**：在保留原始全部功能的基础上，新增了实时交互式 3D 可视化系统，支持通过 WebSocket 实时推送卫星位置、链路状态、带宽利用率等数据，并在浏览器中通过 CesiumJS 渲染交互式 3D 地球场景。

### 核心组件

| 组件 | 路径 | 功能 |
|---|---|---|
| 卫星网络生成 | `hypatia-master/satgenpy/` | 生成星座参数、计算动态路由状态 |
| ns-3 仿真引擎 | `hypatia-master/ns3-sat-sim/` | 包级网络仿真（需 OpenMPI） |
| 可视化前端 | `hypatia-master/satviz/` | CesiumJS 3D 可视化（实时 + 离线模式） |
| 实时后端 (新) | `realtime_backend/` | FastAPI + WebSocket 中继服务 |
| 论文复现 | `hypatia-master/paper/` | 论文实验与图表生成代码 |
| 集成测试 | `hypatia-master/integration_tests/` | 端到端集成测试 |

> `video_processor/` 和 `andrej-karpathy-skills-main/` 位于根目录，但与卫星仿真项目无关。

---

## 2. 架构与运行逻辑

### 2.1 数据流架构

```
┌─────────────────────┐                  ┌──────────────────────┐
│  仿真核心 (Core)      │  state_update    │  实时后端 (Backend)    │
│  demo_sim_core.py   │ ────────────────▶ │  realtime_backend/    │
│  (或 ns-3)          │  simulation_init  │  FastAPI + WebSocket  │
│                     │ ◀──────────────── │                       │
│                     │   command         │                       │
└─────────────────────┘                  └──────────┬───────────┘
                                                    │
                                      state_update  │  simulation_init
                                      (广播)        │  (广播)
                                                    │
                                          ┌─────────▼───────────┐
                                          │  浏览器客户端 (Client) │
                                          │  CesiumJS 3D 地球    │
                                          │  satviz/static_html/  │
                                          └─────────────────────┘
```

### 2.2 两类 WebSocket 端点

| 端点 | 路径 | 连接方 | 职责 |
|---|---|---|---|
| Core 端点 | `ws://host:port/ws/core` | 仿真核心（demo_sim_core.py 或 ns-3） | 发送 `state_update` / `simulation_init`，接收 `command` |
| Client 端点 | `ws://host:port/ws/client` | 浏览器前端 | 接收状态广播，发送控制命令 |

### 2.3 启动顺序

```
第1步: 启动实时后端 → 监听 ws://0.0.0.0:8000
第2步: 启动仿真核心 → 连接到 ws://localhost:8000/ws/core，发送 simulation_init
第3步: 打开浏览器   → 连接到 ws://localhost:8000/ws/client，接收状态更新
```

### 2.4 消息协议

**simulation_init** — 仿真核心连接后首先发送一次，包含星座元数据：
```json
{
  "message_type": "simulation_init",
  "payload": {
    "satellites": ["Sat-0-0", "Sat-0-1", ...],
    "ground_stations": {"Beijing": {"lat": 39.9, "lon": 116.4, ...}, ...},
    "duration": 600.0,
    "constellation": {"name": "Starlink", "shell_count": 5, "current_shell": 0}
  }
}
```

**state_update** — 仿真核心以 10Hz 频率持续发送（由后端广播给所有客户端）：
```json
{
  "message_type": "state_update",
  "payload": {
    "satellite_positions": {"Sat-0-0": {"lat": 45.0, "lon": 120.0, "alt": 550000.0}, ...},
    "ground_stations": {"Beijing": {"lat": 39.9, "lon": 116.4, "alt": 0.0}, ...},
    "link_status": {"Sat-0-0-Sat-0-1": {"is_active": true, "bandwidth_utilization": 0.45, ...}, ...},
    "routing": {"highlight_path": ["Sat-0-0", "Sat-0-1", ...]},
    "timestamp": 123.4
  }
}
```

**command** — 前端客户端发送给后端的控制指令（由后端转发给仿真核心）：

| action | 参数 | 说明 |
|---|---|---|
| `play` | — | 开始/恢复仿真播放 |
| `pause` | — | 暂停仿真 |
| `stop` | — | 停止并重置时间到 0 |
| `reset` | — | 重置时间到 0 |
| `speed` | `{"multiplier": 2.0}` | 调整播放速度 (0.1x–10x) |
| `timeline` | `{"timestamp": 300}` | 跳转到指定仿真时间 |
| `metrics` | `{"type": "bandwidth"}` | 切换指标显示模式 |
| `filter` | `{"satellites": [...], "stations": [...]}` | 筛选显示节点 |
| `scenario` | `{"scenario": "weather"}` | 切换仿真场景 |
| `switch_constellation` | `{"constellation": "Kuiper", "shell": 0}` | 切换星座和壳层 |
| `file_send` | `{"file_id": "…", "src": "UAV-01", "dst": "Beijing", "prio": 1, "rate_bps": 5000000}` | 触发已上传文件的仿真传输（`file_id` 须先经 `/api/files/upload` 上传） |
| `file_cancel` | `{"file_id": "…"}` | 取消传输 |

> 文件传输的完整数据面（HTTP 上传 / 下载 / SHA-256 校验）与遥测字段见 [docs/protocol-v3.2-file-transfer.md](docs/protocol-v3.2-file-transfer.md)。

### 2.5 支持的仿真场景

| 场景 | 丢包率基准 | 延迟抖动 | 说明 |
|---|---|---|---|
| `ideal` | 0.1% | 0.5–5ms | 理想晴空 |
| `commercial` | 1.0% | 5–40ms | 商用服务（默认） |
| `weather` | 2.0% | 10–60ms | 中等天气影响 |
| `handover` | 3.0% | 5–100ms | 频繁卫星切换 |
| `extreme` | 5.0% | 10–150ms | 极端条件 |

### 2.6 支持的星座

| 星座 | 壳层数 | 备注 |
|---|---|---|
| Starlink | 5 壳层 | 高度 550–1325 km |
| Kuiper | 3 壳层 | 高度 590–630 km |
| Telesat | 2 壳层 | 高度 1015–1325 km |

> 注：演示模式下每壳层缩放到约 100 颗卫星以保障实时渲染性能。全量参数已在代码中定义，可修改 `target` 参数调整。

---

## 3. 系统要求

### 3.1 最低要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux (Ubuntu 20.04+)、macOS 或 Windows |
| Python | 3.10+ |
| 内存 | 4 GB |
| 浏览器 | Chrome 90+, Firefox 90+, Edge 90+（需 WebSocket 支持） |

### 3.2 可选要求（原始 Hypatia 离线仿真需要）

| 项目 | 要求 |
|---|---|
| 编译工具 | gcc/g++ 9+, cmake 3.16+（编译 ns-3） |
| MPI | OpenMPI（ns-3 并行仿真需要的可选依赖） |
| Cesium Token | 从 [cesium.com/ion](https://cesium.com/ion) 免费获取（前端 3D 地图渲染所需） |

---

## 4. 下载

```bash
# 克隆仓库
git clone <repository-url> hypatia-enhanced
cd hypatia-enhanced
```

---

## 5. 安装

### 5.1 安装实时可视化系统依赖（必需）

**步骤1：创建 Python 虚拟环境（推荐使用 conda）**

```bash
conda create -n hypatia python=3.12
conda activate hypatia
```

**步骤2：安装原始 Hypatia 依赖**

```bash
cd hypatia-master
pip install -r requirements.txt
```

> 注：`requirements.txt` 包含两个 `git+https://` 依赖（exputilpy 和 networkload），需确保网络可访问 GitHub。

**步骤3：安装实时后端依赖**

```bash
cd ../realtime_backend
pip install -r requirements.txt
```

**步骤4：安装前端仿真核心依赖**

```bash
pip install websockets
```

此时实时可视化系统已可以运行。

### 5.2 安装完整 Hypatia 套件（可选，运行原始仿真需要）

```bash
cd hypatia-master

# 安装系统依赖（Ubuntu/Debian）
bash hypatia_install_dependencies.sh

# 编译各模块（satgenpy/ns3/paper）
bash hypatia_build.sh

# 验证安装
bash hypatia_run_tests.sh
```

> ns-3 编译可能需要较长时间（10–30 分钟），且需要 C++ 编译环境。

---

## 6. 运行

### 6.1 快速启动（3 个终端）

**终端 1：启动实时后端**

```bash
conda activate hypatia
cd hypatia-master
PYTHONPATH=$(pwd):$(pwd)/../realtime_backend python -m realtime_backend.run --port 8000
```

成功输出：
```
==================================================
Realtime Simulation Backend
==================================================
Server: 0.0.0.0:8000
Client WebSocket: ws://0.0.0.0:8000/ws/client
Core WebSocket: ws://0.0.0.0:8000/ws/core
Documentation: http://0.0.0.0:8000/docs
==================================================
```

**终端 2：启动演示仿真核心**

```bash
conda activate hypatia
cd hypatia-master/satviz
python demo_sim_core.py
```

可选参数：
```
python demo_sim_core.py --constellation Kuiper --shell 1
python demo_sim_core.py --constellation Telesat --shell 0
python demo_sim_core.py --port 9000  # 如果后端使用非默认端口
```

成功输出：
```
==================================================
  Demo Simulation Core
==================================================
  Constellation: Starlink shell 0
  Satellites: 108
  Ground Stations: 15
  Pre-computed Links: 110
  Duration: 600.0s
==================================================
Connecting to ws://localhost:8000/ws/core...
Connected to backend!
Sent simulation_init
```

**终端 3：启动前端 HTTP 服务**

```bash
cd hypatia-master/satviz/static_html
python -m http.server 8080
```

然后在浏览器中打开 **http://localhost:8080/index.html**。

### 6.2 运行选项说明

**前端控制面板功能**：
- **Play/Pause/Stop/Reset**：仿真播放控制
- **Speed 滑块** (0.1x–10x)：调节仿真速度
- **Timeline 滑块**：拖拽跳转到任意时间点
- **Metrics 下拉框**：切换显示指标（带宽利用率、延迟、丢包率、链路状态）
- **Scenario 下拉框**：切换仿真场景（影响链路丢包/抖动参数）
- **Constellation 下拉框**：切换星座（Starlink/Kuiper/Telesat）及壳层
- **Node Filter**：选择性显示/隐藏卫星和地面站
- **点击交互**：左键选中卫星/链路查看详情，右键取消选中

**命令行参数**：

| 组件 | 参数 | 默认值 | 说明 |
|---|---|---|---|
| 后端 | `--host` | `0.0.0.0` | 监听地址 |
| 后端 | `--port` | `8000` | 监听端口 |
| 后端 | `--log-level` | `info` | 日志级别 (debug/info/warning/error) |
| 后端 | `--reload` | 关闭 | 文件变更时自动重载（开发用） |
| 核心 | `--host` | `localhost` | 后端地址 |
| 核心 | `--port` | `8000` | 后端端口 |
| 核心 | `--constellation` | `Starlink` | 星座名称 |
| 核心 | `--shell` | `0` | 壳层索引（0-based） |

### 6.3 访问 API 文档

启动后端后，可通过浏览器访问自动生成的 API 文档：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 6.4 健康检查

```bash
curl http://localhost:8000/health
# {"status":"ok","clients_connected":1,"cores_connected":1}
```

### 6.5 WebSocket 测试页面

打开 `hypatia-master/satviz/static_html/wstest.html` 可进行 WebSocket 连接测试。

### 6.6 运行演示客户端（Python）

```bash
# 模拟一个前端客户端，连接到后端接收状态更新
cd realtime_backend
python demo.py
```

### 6.7 运行原始 Hypatia 离线仿真

参考 `hypatia-master/paper/README.md` 中的论文复现步骤。

### 6.8 自定义文件传输（v3.2）

把任意文件"放进"仿真网络，从无人机 / 船舶传至地面站，到达后下载还原（SHA-256 逐字节校验）。

**方式一：前端面板**——打开页面右侧「文件传输」面板，选择文件、源节点（UAV / 船）、目的端（地面站）、优先级与速率上限，点「上传并发送」。下方追踪器实时显示进度 / 吞吐 / ETA / 重传 / 状态，选中条目可在三维地球上高亮其传输路径；完成后点「下载」。

**方式二：命令行客户端**（无需前端）：

```bash
python tools/file_transfer_client.py <file> --src UAV-01 --dst Beijing --rate 5000000 --port 8000
```

它自动完成 上传 → `file_send` → 实时进度 → 下载 → SHA-256 校验，末行打印 `[OK]` / `[FAIL]`。

> 提示：若仿真已跑到 `duration` 上限自动暂停，`file_send` 会自动延长仿真时长并恢复播放，传输不会卡死；seek / stop / reset 等时间跳变会中断在途传输（后端标记为 `CANCELLED`）。详见 [docs/protocol-v3.2-file-transfer.md](docs/protocol-v3.2-file-transfer.md)。

---

## 7. 依赖项详细说明

### 7.1 实时可视化系统依赖

| 包名 | 版本 | 位置 | 用途 |
|---|---|---|---|
| fastapi | >=0.104.0 | realtime_backend/ | Web 框架 |
| uvicorn[standard] | >=0.24.0 | realtime_backend/ | ASGI 服务器 |
| PyYAML | >=6.0 | 两处都需要 | YAML 配置解析 |
| pydantic | >=2.0 | realtime_backend/ | 数据验证 |
| pydantic-settings | >=2.0 | realtime_backend/ | 配置管理 |
| websockets | >=10.0 | 两处都需要 | WebSocket 客户端/服务端 |

### 7.2 原始 Hypatia 依赖（仅离线仿真需要）

| 类别 | 关键包 | 用途 |
|---|---|---|
| 轨位计算 | astropy, ephem, sgp4 | TLE 解析、轨道传播 |
| 数学/科学计算 | numpy, scipy, statsmodels | 数据处理与统计 |
| 图论 | networkx | 路由路径计算 |
| 地理 | geopy, cartopy, pyproj, geographiclib | 地理位置与地图投影 |
| 可视化 | matplotlib, pillow | 图表生成 |
| 原始后端 | Flask, Flask-SocketIO, python-socketio | 原始 HTTP/WebSocket 服务 |
| 外部仓库 | exputilpy, networkload | 实验工具与负载生成 |

### 7.3 ns-3 仿真依赖（仅包级仿真需要）

- ns-3 3.47 源码（由 `build.sh` 自动下载编译）
- C++ 编译环境 (gcc/g++ 9+, cmake)
- OpenMPI（并行仿真可选）
- lcov, gnuplot（测试与绘图）

### 7.4 前端依赖（浏览器端，无需安装）

- CesiumJS 1.141（通过 CDN 加载）
- 原生 JavaScript 模块：WebSocketManager、CesiumManager、UIController、SatelliteVisualizationApp

---

## 8. 项目文件结构

```
hypatia-enhanced/                          # 项目根目录
├── README.md                              # 顶层说明
├── GUIDE.md                               # 本文档
│
├── hypatia-master/                        # Hypatia 主体
│   ├── README.md                          # 增强版说明
│   ├── requirements.txt                   # Python 依赖
│   ├── hypatia_install_dependencies.sh    # 系统依赖安装脚本
│   ├── hypatia_build.sh                   # 编译脚本
│   ├── hypatia_run_tests.sh               # 测试脚本
│   │
│   ├── satgenpy/                          # 星座生成 (Python)
│   │   ├── README.md
│   │   └── satgen/                        # 核心生成模块
│   │
│   ├── ns3-sat-sim/                       # ns-3 包级仿真 (C++/Python)
│   │   ├── README.md
│   │   └── simulator/                     # 自定义卫星模块
│   │
│   ├── satviz/                            # 可视化
│   │   ├── README.md                      # 可视化说明
│   │   ├── FRONTEND_README.md             # 前端详细文档
│   │   ├── demo_sim_core.py               # 演示仿真核心 ★
│   │   ├── packet_sim.py                  # 包级离散事件仿真引擎 ★
│   │   ├── test_packet_sim.py             # DES 单元校验（时延/拥塞对账）
│   │   ├── test_phase3.py                 # 切换丢包 / QoS 测试
│   │   ├── test_phase6.py                 # 守恒/吞吐/M-D-1 对账 + 压测
│   │   ├── test_integration_offline.py    # 全管线离线集成测试
│   │   ├── test_reconnect.py              # 断线重连健壮性测试
│   │   ├── static_html/
│   │   │   ├── index.html                 # 主前端页面 ★
│   │   │   └── wstest.html               # WebSocket 测试页
│   │   ├── js/                            # 前端 JS 模块 ★
│   │   │   ├── app.js                     #   主应用程序
│   │   │   ├── cesium-manager.js          #   3D 场景管理
│   │   │   ├── ui-controller.js           #   UI 控制
│   │   │   ├── websocket.js               #   WebSocket 通信
│   │   │   └── hypatia-adapter.js         #   数据适配器
│   │   ├── scripts/                       # 离线可视化生成脚本
│   │   └── viz_output/                    # 输出目录
│   │
│   ├── paper/                             # 论文复现代码
│   ├── integration_tests/                 # 集成测试
│   │   ├── test_realtime_integration.py   #   实时系统集成测试
│   │   └── test_live_demo.py             #   端到端实时测试
│   │
│   ├── hypatia-master/                    # 原始 Hypatia 备份 (gitignored)
│   └── backup_original/                   # 原始 Hypatia 备份 (gitignored)
│
└── realtime_backend/                      # 实时后端服务
    ├── README.md                          # 后端说明
    ├── QUICKSTART.md                      # 快速入门
    ├── requirements.txt                   # 依赖
    ├── __init__.py
    ├── main.py                            # FastAPI 应用 + WebSocket 端点
    ├── core.py                            # ConnectionManager 连接管理
    ├── config.py                          # 配置管理 (YAML + 环境变量)
    ├── config.yaml                        # 默认配置文件
    ├── schemas.py                         # Pydantic 数据模型
    ├── run.py                             # 命令行启动入口
    ├── run.sh / run.bat                   # 便捷启动脚本
    └── demo.py                            # 测试/演示客户端
```

---

## 9. 配置参考

### 9.1 后端配置文件 (`realtime_backend/config.yaml`)

```yaml
host: "0.0.0.0"
port: 8000
reload: false
log_level: "info"

allowed_origins:
  - "*"

client_ws_path: "/ws/client"
core_ws_path: "/ws/core"

state_message_type: "state_update"
command_message_type: "command"
```

### 9.2 环境变量覆盖

所有配置项可通过 `APP_` 前缀的环境变量覆盖：

```bash
export APP_HOST=127.0.0.1
export APP_PORT=9000
export APP_LOG_LEVEL=debug
python -m realtime_backend.run
```

### 9.3 Cesium Token 配置

在浏览器中访问前端页面后，在右侧面板输入 Cesium Ion Access Token（可从 [cesium.com/ion](https://cesium.com/ion) 免费注册获取）。Token 会存储在浏览器 localStorage 中，下次访问无需重新输入。

---

## 10. 故障排除

### 常见问题

| 问题 | 可能原因 | 解决方法 |
|---|---|---|
| 端口被占用 | 已有进程在使用 8000 端口 | `lsof -i :8000` 查看占用进程，或使用 `--port 9000` 换端口 |
| 前端无显示 | Cesium Token 未配置 | 在控制面板输入 Cesium Ion Token |
| 前端显示 "Disconnected" | 后端未启动或端口不对 | 检查后端是否正常运行，确认端口匹配 |
| 核心连接失败 | 后端未启动或网络不通 | 确认后端先于核心启动 |
| `ModuleNotFoundError` | PYTHONPATH 未设置 | 使用文中完整命令设置 PYTHONPATH |
| websockets 未安装 | 缺少依赖 | `pip install websockets` |
| 前端 IPv6 连接问题 | 浏览器将 localhost 解析为 ::1 | 使用 `127.0.0.1` 代替 `localhost`，前端已默认使用 IPv4 |

---

## 11. 开发

### 后端开发模式（热重载）

```bash
python -m realtime_backend.run --reload --log-level debug
```

### 测试

**集成测试**：
```bash
cd hypatia-master
PYTHONPATH=$(pwd):$(pwd)/../realtime_backend python integration_tests/test_realtime_integration.py
```

**端到端实时测试**：
```bash
cd hypatia-master
PYTHONPATH=$(pwd):$(pwd)/../realtime_backend python integration_tests/test_live_demo.py
```

### 自定义仿真核心

可参考 [demo_sim_core.py](hypatia-master/satviz/demo_sim_core.py) 编写自定义仿真核心：

1. 创建类，实现 `get_init_message()` 返回 `simulation_init` 格式消息
2. 实现 `get_state_update()` 返回 `state_update` 格式消息
3. 实现 `handle_command(data)` 处理前端命令
4. 连接到 `ws://backend:8000/ws/core`，先发送 init，再循环发送 state_update
5. 接收并处理 command 消息

---

## 12. 许可证

- satgenpy: MIT
- satviz: MIT
- realtime_backend: MIT
- ns3-sat-sim: GNU GPLv2
- paper: MIT
