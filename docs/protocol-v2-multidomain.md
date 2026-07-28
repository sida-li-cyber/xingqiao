# 多域节点扩展协议规范 v2.0

> 适用项目：空天海网络可视化实时交互仿真演示系统
> 日期：2026-07-28
> 状态：设计稿，待实施

---

## 1. 设计原则

- **后端最小改动**：realtime_backend 本质是 JSON 转发层，只需一处小修即可支持新协议
- **向后兼容**：原有卫星数据格式不变，新节点类型通过 `type` 字段区分
- **前端驱动渲染**：前端根据 `type` 字段决定视觉表现，后端不关心节点语义
- **模拟数据友好**：协议结构便于用参数方程生成，不依赖真实数据源

---

## 2. 现有协议回顾

当前 `state_update` payload 结构：

```json
{
  "satellite_positions": { "<id>": {"lat", "lon", "alt"} },
  "ground_stations": { "<id>": {"lat", "lon", "alt", "name"} },
  "link_status": { "<id>": {"is_active", "bandwidth_utilization", "latency", "loss_rate"} },
  "routing": { "highlight_path": [...] },
  "bandwidth_utilization": { "<link_id>": <float> },
  "timestamp": <number>
}
```

问题：
- `satellite_positions` 字段名暗示只放卫星，扩展性差
- `ground_stations` 是静态的，放在 init 里更合理
- 后端 Pydantic schema (`StatePayload`) 要求这四个字段全部存在，新增字段会被丢弃

---

## 3. 后端唯一需要的改动

当前后端 `main.py` 的 core 端点只广播 `state_update` 类型消息，`simulation_init` 不会被转发给前端。需要改为：**转发所有来自 core 的消息**。

修改位置：`realtime_backend/main.py` 的 `core_endpoint` 函数

```python
# 修改前：
if message_type == settings.state_message_type:
    state_update = StateUpdate(**message_data)
    await manager.broadcast_state(state_update.dict())
else:
    # 只回 ack，不广播
    ...

# 修改后：
# 所有来自 core 的消息都广播给客户端
await manager.broadcast_state(message_data)
# 同时给 core 回 ack（可选）
```

同时放宽 `schemas.py` 的验证（或直接去掉对 core 消息的 Pydantic 校验），因为新协议的 payload 结构更灵活。

改动量：约 5 行代码。改完后后端变成纯透明转发，不再关心 payload 内容。

---

## 4. 消息类型总览

| message_type | 方向 | 说明 |
|---|---|---|
| `simulation_init` | core → backend → clients | 连接后首发，声明所有节点元数据 |
| `state_update` | core → backend → clients | 持续推送，~10Hz |
| `command` | client → backend → core | 用户控制命令 |
| `ack` | backend → client / core | 确认 |
| `error` | backend → client / core | 错误 |

---

## 5. simulation_init（仿真初始化）

仿真核心连接后端后，**第一条消息**必须是 `simulation_init`。

```json
{
  "message_type": "simulation_init",
  "payload": {
    "version": "2.0",
    "duration": 600.0,
    "update_rate_hz": 10,
    "nodes": {
      "Sat-0-0": {
        "type": "satellite",
        "label": "Sat-0-0",
        "orbit": { "altitude_km": 550, "inclination_deg": 53, "plane": 0, "index": 0 }
      },
      "UAV-01": {
        "type": "uav",
        "label": "无人机-01",
        "group": "alpha",
        "base_alt_m": 8000,
        "speed_kmh": 300
      },
      "Ship-01": {
        "type": "ship",
        "label": "远洋货轮-01",
        "route_name": "Shanghai-Singapore",
        "speed_knots": 18
      },
      "Beijing": {
        "type": "ground_station",
        "label": "北京",
        "lat": 39.9042,
        "lon": 116.4074
      }
    },
    "link_types": {
      "isl": { "label": "星间链路", "color": "#4FC3F7" },
      "gsl": { "label": "地面-卫星链路", "color": "#FF8A65" },
      "sul": { "label": "卫星-无人机链路", "color": "#81C784" },
      "ssl": { "label": "卫星-船舶链路", "color": "#FFB74D" }
    }
  }
}
```

### 节点类型定义

| type | 说明 | 必需元数据 | 可选元数据 |
|---|---|---|---|
| `satellite` | LEO卫星 | orbit.altitude_km, orbit.inclination_deg | orbit.plane, orbit.index |
| `uav` | 无人机 | base_alt_m | group, speed_kmh, model |
| `ship` | 船舶 | route_name 或 route_waypoints | speed_knots, vessel_type |
| `ground_station` | 地面站 | lat, lon | alt, city |

### 设计说明

- `nodes` 是一个扁平字典，key 为全局唯一节点 ID
- 前端根据 `type` 创建不同视觉实体（形状、颜色、大小）
- `link_types` 声明链路分类及默认颜色，前端可覆盖
- 地面站位置在 init 中给出（静态），state_update 中不必重复

---

## 6. state_update（状态更新）

每帧推送所有**动态节点**的当前位置和链路状态。

```json
{
  "message_type": "state_update",
  "payload": {
    "timestamp": 123.45,
    "positions": {
      "Sat-0-0": { "lat": 45.0, "lon": 120.0, "alt": 550000.0 },
      "Sat-0-1": { "lat": 42.3, "lon": 118.5, "alt": 550000.0 },
      "UAV-01": { "lat": 18.2, "lon": 115.8, "alt": 8000.0, "heading": 45.0 },
      "UAV-02": { "lat": 18.5, "lon": 116.1, "alt": 8200.0, "heading": 47.0 },
      "Ship-01": { "lat": 15.3, "lon": 112.7, "alt": 0.0, "heading": 195.0 }
    },
    "links": {
      "Sat-0-0--Sat-0-1": {
        "type": "isl",
        "source": "Sat-0-0",
        "target": "Sat-0-1",
        "is_active": true,
        "bandwidth_utilization": 0.65,
        "latency_ms": 12.5,
        "loss_rate": 0.001
      },
      "Sat-3-2--UAV-01": {
        "type": "sul",
        "source": "Sat-3-2",
        "target": "UAV-01",
        "is_active": true,
        "bandwidth_utilization": 0.30,
        "latency_ms": 8.2,
        "loss_rate": 0.0
      },
      "Sat-1-5--Ship-01": {
        "type": "ssl",
        "source": "Sat-1-5",
        "target": "Ship-01",
        "is_active": true,
        "bandwidth_utilization": 0.45,
        "latency_ms": 15.8,
        "loss_rate": 0.002
      },
      "Beijing--Sat-2-0": {
        "type": "gsl",
        "source": "Beijing",
        "target": "Sat-2-0",
        "is_active": true,
        "bandwidth_utilization": 0.55,
        "latency_ms": 6.1,
        "loss_rate": 0.0
      }
    },
    "routing": {
      "highlight_path": ["Beijing", "Sat-2-0", "Sat-2-1", "Sat-3-2", "UAV-01"]
    },
    "metrics_summary": {
      "active_links": 85,
      "total_nodes": 109,
      "avg_utilization": 0.42,
      "max_latency_ms": 28.5
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `timestamp` | number | 是 | 仿真时间（秒），从0开始 |
| `positions` | object | 是 | 所有动态节点的当前位置 |
| `positions.<id>.lat` | number | 是 | 纬度（度） |
| `positions.<id>.lon` | number | 是 | 经度（度） |
| `positions.<id>.alt` | number | 是 | 高度（米）。卫星~550000，无人机~5000-15000，船舶=0 |
| `positions.<id>.heading` | number | 否 | 航向角（度），用于无人机/船舶朝向渲染 |
| `links` | object | 是 | 当前所有链路状态 |
| `links.<id>.type` | string | 是 | 链路类型：isl / gsl / sul / ssl |
| `links.<id>.source` | string | 是 | 源节点ID |
| `links.<id>.target` | string | 是 | 目标节点ID |
| `links.<id>.is_active` | bool | 是 | 链路是否激活 |
| `links.<id>.bandwidth_utilization` | number | 是 | 带宽利用率 [0, 1] |
| `links.<id>.latency_ms` | number | 否 | 时延（毫秒） |
| `links.<id>.loss_rate` | number | 否 | 丢包率 [0, 1] |
| `routing` | object | 否 | 路由高亮信息 |
| `routing.highlight_path` | array | 否 | 高亮路径节点ID序列 |
| `metrics_summary` | object | 否 | 全局统计摘要（供UI面板显示） |

### 链路ID命名规则

`<source>--<target>`，双横线分隔。例如：
- 星间：`Sat-0-0--Sat-0-1`
- 卫星-无人机：`Sat-3-2--UAV-01`
- 卫星-船舶：`Sat-1-5--Ship-01`
- 地面-卫星：`Beijing--Sat-2-0`

### 与旧协议的兼容映射

前端适配层（`hypatia-adapter.js`）负责转换：

```
旧字段                    →  新字段
satellite_positions       →  positions（仅卫星部分）
ground_stations           →  init.nodes（静态，不在state_update中）
link_status               →  links
bandwidth_utilization     →  links.<id>.bandwidth_utilization
```

---

## 7. command（控制命令）

命令格式不变，扩展 `filter` 命令支持按类型筛选。

```json
{
  "message_type": "command",
  "payload": {
    "action": "filter",
    "params": {
      "types": ["satellite", "uav", "ship", "ground_station"],
      "nodes": ["Sat-0-0", "UAV-01"],
      "exclude": ["Ship-03"]
    }
  }
}
```

### 完整命令列表

| action | params | 说明 |
|---|---|---|
| `play` | null | 播放 |
| `pause` | null | 暂停 |
| `stop` | null | 停止并归零 |
| `reset` | null | 重置时间到0（保持播放状态） |
| `speed` | `{"multiplier": 2.0}` | 调速，范围0.1-10 |
| `timeline` | `{"timestamp": 300}` | 跳转到指定仿真时间 |
| `metrics` | `{"type": "bandwidth"}` | 切换指标：bandwidth / latency / loss / none |
| `filter` | `{"types": [...], "nodes": [...], "exclude": [...]}` | 节点筛选 |
| `focus` | `{"node_id": "UAV-01"}` | 相机聚焦到指定节点（新增） |
| `view_preset` | `{"preset": "global"}` | 预设视角：global / south_china / follow（新增） |

---

## 8. 节点规模规划

| 类型 | 数量 | 运动模型 | 区域 |
|---|---|---|---|
| 卫星 | 72（6面×12颗） | 圆形轨道传播（已有） | 全球，550km |
| 无人机 | 8 | 参数方程（圆形/8字航迹） | 南海上空，5-15km |
| 船舶 | 10 | 大圆航线匀速插值 | 主要海运航线，海面 |
| 地面站 | 15 | 静态 | 全球主要城市 |
| **合计** | **105** | | |

链路估算：
- ISL（星间）：~72条轨道内 + ~72条跨轨道 ≈ 144条
- GSL（地面-卫星）：动态，约15-30条活跃
- SUL（卫星-无人机）：动态，约8-16条
- SSL（卫星-船舶）：动态，约10-20条
- 总活跃链路：~180-210条

这个规模对 CesiumJS 毫无压力（千级实体仍流畅）。

---

## 9. 无人机运动模型

采用参数方程，不需要物理精确，看起来合理即可。

### 编队圆形航迹

```python
# 编队中心
center_lat, center_lon = 18.0, 116.0  # 南海某区域
radius_km = 50.0
period_s = 120.0  # 一圈2分钟

# 第i架无人机（共N架），均匀分布在圆上
phase_offset = i * 2 * pi / N
angle = 2 * pi * t / period_s + phase_offset

# 位置计算（简化：小范围内用平面近似）
lat = center_lat + (radius_km / 111.0) * cos(angle)
lon = center_lon + (radius_km / (111.0 * cos(radians(center_lat)))) * sin(angle)
alt = base_alt + 500 * sin(angle * 2)  # 轻微高度波动
heading = degrees(angle) + 90  # 切线方向
```

### 8字航迹（可选，增加视觉趣味）

```python
# 参数方程（lemniscate）
a = radius_km
x = a * sin(angle)
y = a * sin(angle) * cos(angle)
```

---

## 10. 船舶运动模型

沿预定义航线匀速移动，用大圆插值。

### 航线定义

```python
SHIP_ROUTES = {
    "Shanghai-Singapore": {
        "waypoints": [(31.23, 121.47), (22.30, 114.17), (1.35, 103.82)],
        "speed_knots": 18,
    },
    "Shenzhen-Rotterdam": {
        "waypoints": [(22.54, 114.06), (1.35, 103.82), (12.50, 45.00), (30.00, 32.50), (51.90, 4.50)],
        "speed_knots": 22,
    },
    "Tokyo-LosAngeles": {
        "waypoints": [(35.68, 139.65), (35.00, 160.00), (30.00, -170.00), (34.05, -118.24)],
        "speed_knots": 20,
    },
}
```

### 位置插值

```python
# 1节 = 1.852 km/h
speed_kmh = speed_knots * 1.852
distance_per_tick = speed_kmh * dt / 3600.0  # km

# 沿waypoints逐段线性插值（简化，不用大圆）
# 计算总航线长度，按距离比例确定当前位置
```

---

## 11. 跨域链路建立规则

| 链路类型 | 建立条件 | 断开条件 |
|---|---|---|
| ISL | 同轨道相邻 / 跨轨道同序号（预计算） | 始终存在 |
| GSL | 卫星仰角 > 10°（距离 < 2000km） | 距离 > 2200km（迟滞） |
| SUL | 卫星在无人机上方，距离 < 1500km | 距离 > 1700km |
| SSL | 卫星在船舶上方，距离 < 1800km | 距离 > 2000km |

迟滞（hysteresis）避免链路频繁闪烁。

---

## 12. 前端渲染规范

| 节点类型 | 形状 | 颜色 | 大小 | 附加 |
|---|---|---|---|---|
| satellite | 圆点 | #4FC3F7（浅蓝） | 6px | 轨道轨迹线（半透明） |
| uav | 三角形 ▲ | #81C784（绿） | 10px | 航向旋转，尾迹线 |
| ship | 方形 ■ | #FFB74D（橙） | 8px | 航线轨迹（虚线），尾迹 |
| ground_station | 圆点 + 标签 | #EF5350（红） | 8px | 名称标签始终显示 |

| 链路类型 | 线型 | 颜色（默认） | 宽度 |
|---|---|---|---|
| isl | 实线 | 按利用率渐变（绿→黄→红） | 1.5px |
| gsl | 虚线 | #FF8A65 | 1px |
| sul | 虚线 | #81C784 | 1px |
| ssl | 虚线 | #FFB74D | 1px |
| highlight_path | 实线+辉光 | #FFFFFF glow | 3px |

---

## 13. 实施检查清单

### 后端（~30分钟）
- [ ] `main.py`：core 端点改为广播所有消息（不限于 state_update）
- [ ] `schemas.py`：去掉或放宽 StatePayload 校验（改为 `dict[str, Any]`）
- [ ] 验证：启动后端，用 websocat 发送新格式消息，确认前端能收到

### 仿真核心（2-3天）
- [ ] 新增 `UAV` 类（参数方程运动）
- [ ] 新增 `Ship` 类（航线插值运动）
- [ ] 新增跨域链路计算（SUL、SSL）
- [ ] 重写 `get_init_message()` → 输出 v2 格式
- [ ] 重写 `get_state_update()` → 输出 v2 格式
- [ ] 命令行参数增加 `--num-uavs`、`--num-ships`

### 前端（3-4天）
- [ ] `app.js`：`handleSimulationInit` 解析 v2 的 `nodes` 字典
- [ ] `cesium-manager.js`：按 type 创建差异化实体
- [ ] `cesium-manager.js`：链路渲染支持 type 字段和颜色映射
- [ ] `ui-controller.js`：筛选面板增加"按类型"选项
- [ ] `hypatia-adapter.js`：v1→v2 兼容转换（可选，用于旧数据回放）

---

## 14. 版本协商

前端连接后，等待 `simulation_init`。根据 `payload.version` 决定解析策略：
- `"2.0"`：使用本规范
- 无 version 字段（旧版）：走原有逻辑（`satellites` 数组 + `ground_stations` 字典）

这保证前端可以同时兼容新旧仿真核心。
