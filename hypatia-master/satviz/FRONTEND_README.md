# Satellite Network Visualization Frontend

现代化的卫星网络可视化前端，支持实时交互和离线可视化两种模式。

## 功能特性

### 实时交互模式 (Realtime Mode)
- **实时数据更新**: 通过WebSocket接收后端仿真状态，动态更新3D场景
- **播放/暂停控制**: 控制仿真的播放和暂停
- **速度调节**: 支持0.1x到10x的速度倍数调节
- **时间轴控制**: 支持拖拽跳转到指定时间点
- **指标选择**: 支持切换显示带宽利用率、延迟、丢包率、链路状态
- **节点筛选**: 可以选择性地显示或隐藏指定的卫星和地面站
- **动态链路颜色**: 根据带宽利用率平滑渐变 (绿色空闲 → 黄色中等 → 红色拥堵)
- **路径高亮**: 选中路径以辉光效果高亮展示
- **实体选取**: 点击卫星/链路可选中并高亮

### 离线模式 (Offline Mode)
- **CZML格式支持**: 从URL或本地文件加载CZML数据进行离线可视化
- **Token配置**: 支持配置和保存个人的Cesium Ion访问令牌
- **独立运行**: 无需后端连接即可运行

### 可视化特性
- **CesiumJS 1.141**: 最新版本的3D地球库
- **卫星表示**: 蓝色圆点表示卫星，实时更新位置
- **地面站表示**: 橙红色圆点表示地面站，带有标签
- **链路可视化**: 虚线表示卫星和地面站之间的链路，颜色表示利用率
- **交互选择**: 左键点击选中，右键取消选中
- **性能监控**: 实时显示FPS和仿真统计信息

---

## 快速开始

### 1. 启动后端服务
```bash
cd realtime_backend
pip install -r requirements.txt
python -m realtime_backend.run
```

### 2. 启动仿真核心（Demo）
```bash
cd satviz
pip install websockets
python demo_sim_core.py
```

### 3. 打开前端页面
直接用浏览器打开 `static_html/index.html`，或者通过本地HTTP服务器：
```bash
cd static_html
python -m http.server 8080
# 然后访问 http://localhost:8080/index.html
```

### 4. 测试完整流程
在浏览器打开 `index.html` 后：
- 确认连接状态显示 "Connected"（绿色）
- 仿真自动开始播放，3D场景中出现卫星和链路
- 使用右侧控制面板调整播放速率、切换指标、筛选节点

---

## 文件结构

```
satviz/
├── static_html/
│   ├── index.html              # 主前端页面（CesiumJS 1.141 + 完整UI）
│   ├── top.html                # 旧版模板（保留用于离线静态页生成）
│   └── bottom.html             # 旧版模板（保留用于离线静态页生成）
├── js/
│   ├── websocket.js            # WebSocket通信模块
│   ├── cesium-manager.js       # Cesium 3D场景管理模块
│   ├── ui-controller.js        # UI交互控制模块
│   ├── app.js                  # 主应用程序（组件编排）
│   └── hypatia-adapter.js      # Hypatia数据适配器示例
├── scripts/
│   ├── visualize_constellation.py   # 静态星座可视化生成
│   ├── visualize_path.py           # 静态路径可视化生成
│   ├── visualize_utilization.py    # 静态利用率可视化生成
│   ├── util.py                     # 工具函数
│   └── extractor.py               # CZML提取器
├── demo_sim_core.py            # 演示用仿真核心
├── FRONTEND_README.md          # 本文档
└── viz_output/                 # 输出目录
```

---

## WebSocket 消息协议

### 服务器推送消息

#### `simulation_init` — 仿真初始化（连接后首发）
```json
{
  "message_type": "simulation_init",
  "payload": {
    "satellites": ["Sat-0-0", "Sat-0-1", ...],
    "ground_stations": {
      "Beijing": {"lat": 39.9, "lon": 116.4, "alt": 0.0, "name": "Beijing"}
    },
    "duration": 600.0
  }
}
```

#### `state_update` — 状态更新（持续推送）
```json
{
  "message_type": "state_update",
  "payload": {
    "satellite_positions": {
      "Sat-0-0": {"lat": 45.0, "lon": 120.0, "alt": 550000.0}
    },
    "ground_stations": {
      "Beijing": {"lat": 39.9, "lon": 116.4, "alt": 0.0}
    },
    "link_status": {
      "Sat-0-0-Sat-0-1": {
        "is_active": true,
        "bandwidth_utilization": 0.65,
        "latency": 15.2,
        "loss_rate": 0.001
      }
    },
    "routing": {
      "highlight_path": ["Sat-0-0", "Sat-0-1", "Sat-1-1"]
    },
    "bandwidth_utilization": {
      "Sat-0-0-Sat-0-1": 0.65
    },
    "timestamp": 123.45
  }
}
```

### 客户端发送命令

| 命令 | action | params |
|------|--------|--------|
| 播放 | `play` | null |
| 暂停 | `pause` | null |
| 停止 | `stop` | null |
| 重置 | `reset` | null |
| 调速 | `speed` | `{"multiplier": 2.0}` |
| 跳转时间 | `timeline` | `{"timestamp": 300}` |
| 切换指标 | `metrics` | `{"type": "bandwidth"}` |
| 筛选节点 | `filter` | `{"satellites": [...], "stations": [...]}` |

---

## 后端对接指南

### 仿真核心需要实现

1. 连接到 `ws://<host>:<port>/ws/core`
2. 连接后立即发送 `simulation_init` 消息
3. 按照 ~10Hz 频率持续发送 `state_update` 消息
4. 监听来自后端的 `command` 消息并响应

### 参考实现

`demo_sim_core.py` 提供了完整的参考实现，模拟了一个 72 颗卫星的 Starlink-like 星座。

---

## 浏览器支持

| 浏览器 | 版本 | 支持 |
|------|------|------|
| Chrome | 90+ | ✓ |
| Firefox | 88+ | ✓ |
| Safari | 14+ | ✓ |
| Edge | 90+ | ✓ |

---

## 已知问题

1. **大数据集**: 超过 1000 个卫星或 10000 条链路时性能会下降
2. **Cesium Token**: 未配置时默认使用 NaturalEarthII 底图（无需token也能运行）
3. **离线CZML**: 需要预先用 `scripts/extractor.py` 生成CZML文件

---

## 技术栈

- **前端框架**: Vanilla JavaScript (无外部依赖)
- **3D库**: CesiumJS 1.141
- **通信**: WebSocket (JSON)
- **存储**: LocalStorage (配置保存)

---

## 集成测试

v3 测试套件位于 `hypatia-master/satviz/`（固定种子、可复现，取代 v2 的 `integration_tests/`）：

```bash
cd hypatia-master/satviz
python test_packet_sim.py            # DES 单元校验：轻载时延对账、拥塞排队/丢包
python test_phase3.py                # 切换丢包尖峰对账、QoS 严格优先
python test_phase6.py                # 守恒/吞吐/M-D-1 对账 + 长时与背压压测（--fast 跳过两个长时测试）
python test_integration_offline.py   # 全管线离线集成（真实 DemoSimCore，无需后端）
python test_reconnect.py             # 断线重连健壮性（自动起停 backend + sim_core 子进程）
```

测试覆盖：
- WebSocket 连接管理、断线重连（前端指数退避 + 后端 init 重放 + 核心自动重连）
- simulation_init 消息广播与重放
- state_update 实时推送
- 命令转发 (play/pause/stop/reset/speed/timeline/metrics/filter)
- 包级指标对账（时延/吞吐 vs 理论值、包守恒、M/D/1 排队）与长时压测
- 详见 `docs/phase6-validation.md`

---

## 版本历史

### v3.0 (Packet-Level)
- 包级仿真：指标全部由自研 DES (`packet_sim.py`) 中真实数据包涌现
- v3 极简悬浮 UI：图层/统计/详情面板 + 底部播放条
- 墙钟位置插值（5Hz 推送平滑为逐帧连续运动）
- 链路指标着色（吞吐/队列/时延/丢包）+ 时序图表面板 + 沿链路包流动动画
- 切换丢包、分域 QoS（UAV 高优先 / 船舶尽力）
- 阶段 6 校验加固：理论对账 + 长时压测 + 三层断线自愈

### v2.0 (Enhanced)
- 新增实时交互模式 (WebSocket + 10 Hz 状态更新)
- 新增播放控制 (播放/暂停/停止/重置/调速/时间跳转)
- 新增 JS 模块化架构 (app.js, cesium-manager.js, ui-controller.js, websocket.js)
- 新增动态链路颜色 (根据利用率渐变)
- 新增节点筛选和指标切换
- 新增 multi-client 支持
- 保留离线 CZML 模式 (与原版兼容)

### v1.0 (Original)
- 基于 Python 脚本生成静态 CesiumJS HTML
- Flask-SocketIO 服务器
- 离线 CZML 可视化
