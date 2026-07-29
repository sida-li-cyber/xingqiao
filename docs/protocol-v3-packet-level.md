# 包级仿真遥测协议规范 v3.0

> 适用项目：空天海网络可视化实时交互仿真演示系统
> 日期：2026-07-29
> 状态：设计稿（阶段 1 — 协议与指标地基）
> 前置文档：`docs/protocol-v2-multidomain.md`（v2.0 多域节点协议）

---

## 1. 背景与目标

v2 协议已经统一了多域节点（卫星/无人机/船舶/地面站）与链路（ISL/GSL/SUL/SSL）的数据结构，但链路指标（`bandwidth_utilization` / `latency_ms` / `loss_rate`）仍是**模拟数据**（正弦波 + 噪声），没有真实数据包在流动。

v3 协议的目标是：**为包级仿真（packet-level simulation）定义遥测字段**，让前端能够展示从真实数据包转发过程中涌现的指标。

v3 是 v2 的**增量扩展**（additive）：

- 所有 v2 字段保持不变、含义不变；
- 新增字段全部为**可选**（optional），v2 前端收到 v3 数据流仍可正常渲染（忽略新字段）；
- v3 前端对缺失的新字段做兜底处理（显示 "—"）。

版本协商沿用 v2 机制：前端根据 `simulation_init.payload.version` 选择解析策略，`"3.0"` 走本规范。

---

## 2. 设计原则

- **增量兼容**：v3 = v2 字段 + 包级遥测字段，不破坏任何现有字段。
- **后端零改动**：`realtime_backend` 仍是纯 JSON 透明转发层，v3 不要求后端做任何修改。
- **指标可涌现**：所有新增字段都对应包级仿真器（DES）中可直接采集的量（逐包时间戳、逐链路字节计数、队列深度），不凭空构造。
- **单位明确**：所有容量/吞吐用 `bps`（比特每秒），时延用 `ms`，队列用"包数"，避免单位歧义。

---

## 3. 链路容量基准（capacity）

每条链路的容量由链路类型决定，在 `simulation_init` 中按类型声明默认值，`state_update` 中每条链路携带当前有效容量（便于未来建模链路降速/误码导致的容量衰减）。

| 链路类型 | 默认容量 | 说明 |
|---|---|---|
| `isl` | 10 Gbps（1e10） | 星间激光链路 |
| `gsl` | 1 Gbps（1e9） | 卫星 ↔ 地面站馈电/用户链路 |
| `sul` | 500 Mbps（5e8） | 卫星 ↔ 无人机上行 |
| `ssl` | 500 Mbps（5e8） | 卫星 ↔ 船舶上行 |

> 以上为演示基准值，可在仿真核心配置中调整。未来接入真实链路预算（自由空间损耗、雨衰）后，容量可动态修正。

### 包模型基准

| 参数 | 默认值 | 说明 |
|---|---|---|
| `packet_size_bytes` | 1500 | 单包大小（字节） |
| `queue_capacity_pkts` | 200 | 每输出端口队列深度上限（包），溢出丢包 |

---

## 4. simulation_init 扩展

在 v2 的 `payload` 基础上，`link_types` 增加容量声明，并新增可选的 `packet_model` 段。

```json
{
  "message_type": "simulation_init",
  "payload": {
    "version": "3.0",
    "duration": 600.0,
    "update_rate_hz": 5,
    "nodes": { "...": "（与 v2 完全一致）" },
    "link_types": {
      "isl": { "label": "星间链路", "color": "#4FC3F7", "capacity_bps": 1e10 },
      "gsl": { "label": "地面-卫星链路", "color": "#FF8A65", "capacity_bps": 1e9 },
      "sul": { "label": "卫星-无人机链路", "color": "#81C784", "capacity_bps": 5e8 },
      "ssl": { "label": "卫星-船舶链路", "color": "#FFB74D", "capacity_bps": 5e8 }
    },
    "packet_model": {
      "packet_size_bytes": 1500,
      "queue_capacity_pkts": 200,
      "traffic": "poisson",
      "notes": "阶段1为占位配置，阶段2由DES流量模型驱动"
    }
  }
}
```

### 新增字段说明（init）

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `link_types.<type>.capacity_bps` | number | 否 | 该类型链路默认容量（bps） |
| `packet_model` | object | 否 | 包模型参数（供前端展示/未来配置） |
| `packet_model.packet_size_bytes` | number | 否 | 单包大小 |
| `packet_model.queue_capacity_pkts` | number | 否 | 队列深度上限（包） |
| `packet_model.traffic` | string | 否 | 流量模型标识：`poisson` / `cbr` |

---

## 5. state_update 扩展

### 5.1 链路级包级遥测（`links.<id>` 新增字段）

在 v2 链路字段（`type` / `source` / `target` / `is_active` / `bandwidth_utilization` / `latency_ms` / `loss_rate`）基础上新增：

```json
"Sat-3-2--UAV-01": {
  "type": "sul",
  "source": "Sat-3-2",
  "target": "UAV-01",
  "is_active": true,

  "bandwidth_utilization": 0.30,   // v2 字段，v3 中 = tx_bps / capacity_bps（真实值）
  "latency_ms": 8.2,               // v2 字段，v3 中 = 逐包实测（传播+排队+串行化）
  "loss_rate": 0.0,                // v2 字段，v3 中 = 实测丢包占比

  "tx_bps": 1.5e8,                 // 新增：测量窗口内的发送吞吐（bps）
  "capacity_bps": 5e8,             // 新增：当前有效容量（bps）
  "queue_depth": 12,               // 新增：输出队列当前深度（包）
  "queue_capacity": 200,           // 新增：队列深度上限（包）
  "propagation_ms": 6.1            // 新增：纯几何传播时延 = 斜距/光速（ms）
}
```

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `tx_bps` | number | 否 | 测量窗口内该链路的发送吞吐（bits/s） |
| `capacity_bps` | number | 否 | 当前有效容量（bits/s），默认取自 init 的类型容量 |
| `queue_depth` | number | 否 | 输出队列当前积压包数 |
| `queue_capacity` | number | 否 | 队列上限（包），溢出即丢包 |
| `propagation_ms` | number | 否 | 纯传播时延（几何），与排队/串行化时延解耦 |

**语义约定**：

- `bandwidth_utilization = clamp(tx_bps / capacity_bps, 0, 1)`。v2 中是独立模拟值，v3 中由吞吐/容量导出。
- `latency_ms` 是该链路上数据包的**实测平均逐包时延** = `propagation_ms` + 排队等待 + 串行化（包长/带宽）。`propagation_ms` 单列以便前端区分"几何时延"与"拥塞时延"。
- `loss_rate` 是该链路的**实测丢包占比**（队列溢出 + 误码），不再是场景查表值。

### 5.2 节点级包级遥测（新增顶层 `node_metrics`）

v2 没有节点级指标。v3 新增顶层 `node_metrics` 字典，key 为节点 ID：

```json
"node_metrics": {
  "UAV-01": {
    "pkts_sent": 1240,        // 该节点作为流量源注入的包数（累计）
    "pkts_recv": 310,         // 该节点作为终点收到的包数（累计）
    "pkts_fwd": 0,            // 该节点中继转发的包数（卫星>0，终端节点=0）
    "pkts_dropped": 3,        // 在该节点被丢弃的包数（队列溢出，累计）
    "e2e_latency_ms": 24.6,   // 该节点相关的端到端时延均值（ms）
    "jitter_ms": 2.1          // 时延抖动（ms，标准差或相邻差均值）
  },
  "Sat-2-0": {
    "pkts_sent": 0,
    "pkts_recv": 0,
    "pkts_fwd": 8930,
    "pkts_dropped": 17,
    "e2e_latency_ms": 0.0,
    "jitter_ms": 0.0
  }
}
```

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `node_metrics.<id>.pkts_sent` | number | 否 | 作为流量源注入的包数（累计） |
| `node_metrics.<id>.pkts_recv` | number | 否 | 作为目的节点收到的包数（累计） |
| `node_metrics.<id>.pkts_fwd` | number | 否 | 中继转发的包数（累计） |
| `node_metrics.<id>.pkts_dropped` | number | 否 | 本节点丢弃的包数（累计） |
| `node_metrics.<id>.e2e_latency_ms` | number | 否 | 端到端时延均值（仅对产生/接收流量的节点有意义） |
| `node_metrics.<id>.jitter_ms` | number | 否 | 时延抖动 |

**语义约定**：

- 计数均为**累计值**（monotonic），前端如需"速率"可自行对时间差分。累计值在 seek/重置时随仿真时钟归零。
- `e2e_latency_ms` 指"以该节点为源或宿的数据包"的端到端时延均值；纯转发节点（卫星）该值为 0/缺省。

### 5.3 全局摘要扩展（`metrics_summary` 新增字段）

在 v2 的 `active_links` / `total_nodes` / `avg_utilization` / `max_latency_ms` 基础上新增：

```json
"metrics_summary": {
  "active_links": 85,
  "total_nodes": 105,
  "avg_utilization": 0.42,
  "max_latency_ms": 28.5,

  "pkts_in_flight": 156,          // 新增：当前网络中在途包数
  "pkts_delivered": 48210,        // 新增：累计送达包数
  "pkts_dropped": 87,             // 新增：累计丢包数
  "avg_e2e_latency_ms": 22.3,     // 新增：全网端到端时延均值
  "aggregate_throughput_bps": 3.2e9  // 新增：全网总吞吐
}
```

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `pkts_in_flight` | number | 否 | 当前在途（已注入未送达）包数 |
| `pkts_delivered` | number | 否 | 累计送达包数 |
| `pkts_dropped` | number | 否 | 累计丢包数 |
| `avg_e2e_latency_ms` | number | 否 | 全网端到端时延均值 |
| `aggregate_throughput_bps` | number | 否 | 全网总吞吐（bps） |

---

## 6. 完整 state_update 示例（v3）

```json
{
  "message_type": "state_update",
  "payload": {
    "timestamp": 123.4,
    "positions": {
      "Sat-0-0": { "lat": 45.0, "lon": 120.0, "alt": 550000.0 },
      "UAV-01": { "lat": 18.2, "lon": 115.8, "alt": 8000.0, "heading": 45.0 },
      "Ship-01": { "lat": 15.3, "lon": 112.7, "alt": 0.0, "heading": 195.0 }
    },
    "links": {
      "Sat-3-2--UAV-01": {
        "type": "sul", "source": "Sat-3-2", "target": "UAV-01", "is_active": true,
        "bandwidth_utilization": 0.30, "latency_ms": 8.2, "loss_rate": 0.0,
        "tx_bps": 1.5e8, "capacity_bps": 5e8,
        "queue_depth": 12, "queue_capacity": 200, "propagation_ms": 6.1
      }
    },
    "node_metrics": {
      "UAV-01": { "pkts_sent": 1240, "pkts_recv": 310, "pkts_fwd": 0,
                  "pkts_dropped": 3, "e2e_latency_ms": 24.6, "jitter_ms": 2.1 }
    },
    "routing": { "highlight_path": ["Beijing", "Sat-2-0", "Sat-3-2", "UAV-01"] },
    "metrics_summary": {
      "active_links": 85, "total_nodes": 105, "avg_utilization": 0.42, "max_latency_ms": 28.5,
      "pkts_in_flight": 156, "pkts_delivered": 48210, "pkts_dropped": 87,
      "avg_e2e_latency_ms": 22.3, "aggregate_throughput_bps": 3.2e9
    }
  }
}
```

---

## 7. 阶段 1 的占位实现约定

阶段 1 只搭地基，**不实现真实 DES**。为了让管线端到端跑通、前端能渲染新字段，仿真核心按下述规则生成**占位数据**：

- `capacity_bps`：取自 `link_types` 的类型容量（常量）。
- `tx_bps`：`bandwidth_utilization`（v2 模拟值）× `capacity_bps`。
- `queue_depth`：由 `bandwidth_utilization` 映射出的模拟值（如 `round(util * queue_capacity * 0.3)`）。
- `propagation_ms`：按链路两端位置用**真实几何**计算（斜距/光速）——这是阶段 1 就能给真的字段。
- `node_metrics` / `metrics_summary` 新字段：由链路模拟值聚合出的占位值。

阶段 2 实现 DES 后，这些字段逐一替换为真实采集值，**字段名与结构不变**。这正是"协议先行"的价值：前端与后端在阶段 1 即完成适配，阶段 2 只换数据源。

---

## 8. 前端适配要点（阶段 1）

- 详情面板（链路）：在现有 带宽利用率/时延/丢包/状态 基础上，增加 吞吐(`tx_bps`)、容量(`capacity_bps`)、队列(`queue_depth`/`queue_capacity`)、传播时延(`propagation_ms`) 四组展示；字段缺失时显示 "—"。
- 详情面板（节点）：增加 发送/接收/转发/丢包 计数与 端到端时延/抖动 展示；字段缺失时显示 "—"。
- 统计面板：`metrics_summary` 新增字段（在途包/送达/丢包/平均时延/总吞吐）可选展示。
- 数值格式化：吞吐按 Gbps/Mbps/Kbps 自适应；时延保留 1 位小数；计数用千分位。

---

## 9. 版本协商（更新）

前端收到 `simulation_init` 后按 `payload.version` 分支：

- `"3.0"`：本规范（v2 字段 + 包级遥测）。
- `"2.0"`：v2 规范（无包级字段，新字段显示 "—"）。
- 无 version：v1 旧逻辑。

v3 前端必须对包级字段做**存在性兜底**，保证接 v2 核心时不报错、不白屏。
