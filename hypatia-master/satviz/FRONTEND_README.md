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
│   ├── lab.html                # 教学实验台独立页面（E1~E4 沙箱实验）
│   ├── top.html                # 旧版模板（保留用于离线静态页生成）
│   └── bottom.html             # 旧版模板（保留用于离线静态页生成）
├── js/
│   ├── config.js               # SBConfig：WS/API 地址解析（支持 ?ws=host:port 覆盖）
│   ├── constants.js            # SBConstants：链路/节点类型标签与颜色单一来源
│   ├── websocket.js            # WebSocket通信模块（指数退避重连 + 消息队列上限）
│   ├── protocol31.js           # 协议 3.1 紧凑帧解码纯函数（node:test 可单测）
│   ├── cesium-manager.js       # Cesium 3D场景管理模块
│   ├── packet-flow.js          # 沿链路包流动动画（overlay 池）
│   ├── chart.js                # 时序图表面板
│   ├── experiment.js           # 教学实验目录元数据
│   ├── lab.js                  # 教学实验台（lab.html 专用）
│   ├── ui-controller.js        # UI交互控制模块
│   └── app.js                  # 主应用程序（组件编排 + 协议归一化）
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

> 历史说明：`js/hypatia-adapter.js`（Hypatia 数据适配器示例）已于前端 P3 清理中移除，
> 前端从未在运行时引用该文件。

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

#### 协议 3.1 — 千星级紧凑帧（阶段7）

为支持 440 / 1584 星预设（稳态单帧 < 100 KB），`simulation_init` 与
`state_update` 在 v3 基础上做了紧凑化（向后兼容的增量字段，旧字段语义不变）：

`simulation_init.payload` 新增：
- `sat_order`: 卫星 ID 数组，state 帧的 `sat_pos` 按此顺序对齐；
- `isl_topology`: 静态星间链路 `[ [idA, idB], ... ]`，前端可一次性绘出网格；
- `version`: `"3.1"`。

`state_update.payload` 紧凑化：
```json
{
  "timestamp": 123.4,
  "sat_pos": [[45.012, 120.334], ...],   // 按 sat_order 对齐，仅 lat/lon（高度恒定，取自 init 的 orbit.altitude_km）
  "positions": { "UAV-01": {"lat":..,"lon":..,"alt":..,"heading":..} },  // 仅动态节点（UAV/船）
  "links": { "Beijing--Sat-3-1": {"t":"gsl","u":0.0,"l":3.1,"d":0.0,"tx":0.0,"q":0,"p":3.1} },
  "links_removed": ["Sat-0-1--Sat-0-2"],  // 本帧消失的链路 key
  "links_full": false,                     // true = 全量重同步帧（每 25 tick 一次，首帧必为 true）
  "node_metrics": { "UAV-01": {...} },     // 仅本窗口有收/发/转发/丢包活动的节点
  "metrics_summary": { ... }
}
```

链路短键对照：`t`=类型，`u`=带宽利用率，`l`=时延(ms)，`d`=丢包率，
`tx`=吞吐(bps)，`q`=队列深度，`p`=传播时延(ms)。`capacity_bps` 由 init 的
`link_types[t].capacity_bps` 提供；空闲 ISL 不出现在 `links` 中（零开销）。

前端重构（`app.js` 归一化层，CesiumManager / UIController 无需感知短键）：
- `_rebuildPositions`: 由 `sat_pos` + `sat_order` + 恒定高度还原全量位置；
- `_mergeLinks` / `_expandLink`: 将短键 delta 合并进客户端 `linkCache`
  （`links_full` 重置、`links_removed` 剪枝），再展开为长格式交给 `syncLinks`；
- 上述三个解码逻辑已提取为纯函数模块 `js/protocol31.js`（无 DOM / Cesium 依赖），
  app.js 仅做委托，单测见 `tests/frontend/protocol31.test.js`；
- 小规模星座（≤200 星）在首帧后用 `isl_topology` 绘制静态 ISL 网格
  （`CesiumManager.setStaticISLMesh`，装饰性、不可拾取）；千星级仅渲染有流量的链路。

---

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
2. 连接后立即发送 `simulation_init` 消息（payload 含 `update_rate_hz`，当前为 5）
3. 按照 5 Hz 频率持续发送 `state_update` 消息（前端按墙钟插值平滑为逐帧连续运动）
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

1. **大数据集**: 阶段7 后已支持 1584 星预设（协议 3.1 紧凑帧 + 空闲链路零开销，
   核心 ≥30 ticks/s、稳态单帧 <50 KB）；更大规模需进一步削减可视化实体
2. **Cesium Token**: 未配置时默认使用 NaturalEarthII 底图（无需token也能运行）
3. **离线CZML**: 需要预先用 `scripts/extractor.py` 生成CZML文件
4. **CesiumJS CDN**: Cesium 从 cesium.com 加载，需联网（内网部署注意代理放行）

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
python test_phase7.py                # 千星级：生成健全性/网格等价/协议3.1结构/440 压测（--long 加 1584/600s）
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

### 前端单测（node:test）

协议解码纯函数位于 `tests/frontend/`，不依赖浏览器，用 Node 自带测试运行器执行：

```powershell
node --test tests/frontend/
```

仓库未假定全局安装 Node；无 Node 环境时可用便携版（解压后直接调用 node.exe）：

```powershell
tools\node-portable\node-v20.18.1-win-x64\node.exe --test tests\frontend\
```

---

## 渲染性能与 Primitive API 预研结论

已实施（P2）：
- 链路 / ISL 网格的 `CallbackProperty` positions 回调改用 per-entity scratch 数组，
  消除每帧 `Cartesian3` 临时对象分配；
- 统计面板新增渲染诊断行（实体总数、插值段数、流动 overlay 数）。

Primitive API（`PointPrimitiveCollection` / `PolylineCollection`）预研结论：
- 优势：把上千个点/线段合并进单个 draw call，绕开 Entity 层的属性跟踪与
  逐实体更新开销，是千星级渲染的常规解法；
- 代价：拾取、逐链路着色、标签、选中高亮等交互能力都需基于 Primitive 重建，
  迁移面大且与现有 Entity 交互代码耦合深；
- 结论：当前 72 星演示与 440 星预设下 Entity 方案 FPS 充足，**暂不引入**；
  若 1584 星全量渲染时帧耗成为瓶颈，优先策略仍是“只绘有流量链路 + 静态
  ISL 网格”，其次再评估混合方案（节点用 PointPrimitive，链路保留 Entity）。

---

## 版本历史

### 可选星座（Selectable Constellations）
- 星座预设：`demo72` / `demo440` / `starlink`（1584 星）/ `kuiper`
  （34×34，630 km / 51.9°）/ `telesat`（27×13，1015 km / 98.98°），
  CLI `--constellation` 指定；旧 `--scale 72|440|1584` 保持兼容
- 运行时热切换：前端播放栏新增星座下拉框（含自定义 Walker-δ 单壳层
  参数表单），经 `set_constellation` 命令原地重建星座并重发
  `simulation_init`，无需重启核心；TLE 模式下该命令被忽略
- `simulation_init` 新增 `constellation` 字段（name/label/sat_count/shells），
  前端据此回显选择器状态并回填自定义参数
- 新增 `tests/test_constellation.py`（预设参数 / 热切换 / 非法参数拒绝，10 例）

### 前端改进批次（P0–P3）
- **P0**：修复 renderFileList XSS（转义文件名）；多客户端播放状态同步
  （核心下发 `is_playing`，按钮由权威状态驱动）；详情/统计面板重叠修复；
  WS 离线消息队列 50 条上限（满时丢最旧）
- **P1**：断线全局遮罩 + 手动重连；alert() 统一换 toast；`SBConfig`
  WS/API 地址可配置（`?ws=host:port`）；键盘快捷键（空格/S/Esc）；小屏响应式
- **P2**：链路 positions 回调 scratch 数组复用；渲染诊断指标；
  Primitive API 预研结论（见上节，暂不引入）
- **P3**：删除死代码 `hypatia-adapter.js` 与 5 个遗留指令方法；链路/节点
  颜色常量收敛至 `constants.js`；协议 3.1 解码提取为 `protocol31.js` 纯函数
  并补 node:test 单测（9 例）；本文档同步更新（5 Hz 频率纠正等）

### v3.1 (Thousand-Satellite Scale / 阶段7)
- 千星级规模：多壳层 Walker-δ 生成器 + `--scale 72|440|1584` 预设
  （72 与旧版几何逐字节一致；ID 方案 `Sat-{plane}-{idx}`，多壳层 `Sat-{shell}-{plane}-{idx}`）
- 协议 3.1 紧凑帧：`sat_pos` 对齐数组 + 链路短键 delta（`links_removed` /
  `links_full`）+ 窗口活跃节点，1584 星稳态单帧 < 50 KB
- 核心性能：空间网格可见性预筛 + 1Hz 拓扑节流 + ISL 传播缓存 + 快照分组预计算，
  1584 星 ≥ 30 ticks/s（目标 ≥ 20）
- 前端归一化层（app.js）：短键展开 + delta 合并 + 小规模星座静态 ISL 网格 +
  空闲链路零开销；CesiumManager / UIController / 包流动动画无需改动
- 新增 `test_phase7.py`（生成健全性 / 网格等价性 / 协议结构 / 440 与 --long 1584 压测）

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
