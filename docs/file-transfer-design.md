# 自定义文件传输与实时追踪 — 设计文档

> 适用项目：空天海网络可视化实时交互仿真演示系统
> 日期：2026-07-29
> 状态：设计稿（待实施，A 期先行）
> 维护：李思达
> 协议版本：v3.1 → **v3.2**（向后兼容增量）

---

## 1. 目标

让用户能够：

1. **自定义传输数据**：上传任意文件（而非仅系统内置的 Poisson 背景流），指定源节点、目的端与优先级；
2. **真实字节落地**：文件被切片后在仿真网络中逐跳转发，到达目的端地面站后**可下载还原**；
3. **实时追踪**：在三维地球上实时看到该文件的传输进度、当前路径、逐跳时延、吞吐、重传与完成时刻；
4. **多文件并发**：多个文件同时传输并分别追踪，可观测 QoS 优先级竞争。

路由采用**自动最短路径**（复用现有反向 Dijkstra），拓扑切换时自动改路。

---

## 2. 核心设计：控制面 / 数据面分离

### 2.1 为什么不把真实字节塞进 DES

现有 `Packet`（`packet_sim.py`）是 `__slots__` 轻量对象，仅携带 `pid/src/dst/size/inject_time/enq_time/prio` 等"信封"字段；事件堆同时承载上千包/秒的背景 Poisson 流量。若把文件真实字节切片塞进每个 Packet 对象，几 MB 文件即产生数千个持字节对象，内存与 tick 速率都会被拖垮，且与已验证的包守恒模型耦合过深。

### 2.2 分离方案

- **控制面（DES，扩展现有引擎）**：文件分片在 DES 中只是**抽象包**——`Packet` 仅新增两个可选字段 `file_id` 与 `chunk_seq`。DES 负责注入时序、逐跳路由、排队、丢包、重传调度，并持续上报"第几片送达 / 丢失"。全程**不持有真实字节**。包守恒不变量（`生成 = 送达 + 丢弃 + 在途`）对文件分片与背景流量一视同仁，精确成立。
- **数据面（后端，新增）**：后端持有上传的原始字节（按 `chunk_seq` 索引，仅存一份），收到 DES 的送达事件后把对应字节段拷入目的端重组缓冲；全部分片送达即文件完成，开放 HTTP 下载。

这样 DES 进程保持轻量，字节搬运放在具备 HTTP 能力的后端，两个平面通过"分片送达 / 丢失事件"单向耦合。

```
┌────────────┐  HTTP multipart   ┌──────────────────────────────┐
│  前端上传   │ ───────────────▶ │  后端：字节存储 (按 seq 索引)  │
└────────────┘                   └───────────────┬──────────────┘
      │ WS file_send                              │ 重组缓冲 → 下载
      ▼                                           ▲
┌─────────────────────────────────────────────────┴──────────┐
│  DES 控制面：FileTransfer 模型                               │
│   分片注入 → 反向Dijkstra路由 → 排队/丢包 → ARQ重传 → 送达    │
│   上报事件: file_chunk_delivered / file_chunk_dropped         │
│   上报指标: progress / path / eta / throughput / retx         │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 控制面：DES `FileTransfer` 模型

### 3.1 数据结构

新增 `FileTransfer`（建议置于 `packet_sim.py` 或独立 `file_transfer.py`）：

| 字段 | 说明 |
| --- | --- |
| `file_id` | 全局唯一（后端生成，UUID 或自增） |
| `name` | 文件名（仅展示） |
| `src` / `dst` | 源节点 ID / 目的端（地面站）ID |
| `total_bytes` | 文件总字节数 |
| `chunk_size` | 分片大小（字节，可配置，默认 16384） |
| `total_chunks` | `ceil(total_bytes / chunk_size)` |
| `prio` | QoS 优先级（0 最高） |
| `rate_cap_bps` | 单文件注入速率上限（可选，0 = 不限） |
| `state` | `TRANSFERRING / COMPLETE / FAILED / CANCELLED` |
| `delivered` | 已送达分片序号集合（位图 / set） |
| `pending_retx` | 待重传分片序号队列 |
| `retx_count` | 累计重传次数 |
| `path` | 当前路由跳序（节点 ID 列表，供可视化） |
| `start_time` / `complete_time` | 仿真时间戳 |

`Packet` 增加可选字段 `file_id=None`、`chunk_seq=-1`（默认值保持背景流量行为不变，向后兼容）。

### 3.2 生命周期

1. **注入**：`file_send` 命令到达后，DES 在源节点按 `rate_cap_bps`（或默认速率）以 GEN 事件逐片注入，分片包携带 `(file_id, chunk_seq)`。注入受 `max_in_flight` 背压与窗口流控约束，避免一次性灌入全部分片。
2. **路由**：复用现有反向 Dijkstra（`transit` 白名单仅 `Sat-*` 可转发），文件流走当前最短路径；拓扑 dirty 重算后自动改路，`path` 字段同步更新。
3. **送达**：分片到达 `dst`（ARR 事件）→ 记入 `delivered`，发出 `file_chunk_delivered{file_id, seq}`；当 `|delivered| == total_chunks` → `state=COMPLETE`，发 `file_complete{file_id}`。
4. **丢弃与重传（ARQ）**：分片因拥塞 / 切换被丢 → 记入 `pending_retx`，发 `file_chunk_dropped{file_id, seq}`；可靠模式下经超时（如 1× 当前 RTT 估计）后**重注入该分片**。重传包计为**新生成**包，因此守恒式 `生成 = 送达 + 丢弃 + 在途` 仍精确成立。
5. **取消**：`file_cancel` → 清除在途分片（计为丢弃）、`state=CANCELLED`。

### 3.3 守恒与背景流量共存

文件分片与背景 Poisson 包共享同一套端口队列与严格优先级调度，因此：

- 大文件高速注入会真实挤占链路容量、抬高背景流时延与丢包——这正是演示价值所在；
- 高优先级文件（如遥测）在拥塞下可推挤低优先级队列（沿用现有"高优先级挤出最低优先级"丢包策略）；
- 守恒验证只需把文件分片计入 `n_generated / n_delivered / n_dropped` 即可，无需特殊分支。

---

## 4. 数据面：后端字节存储与重组

`realtime_backend` 新增（建议独立路由模块 `file_routes.py`）：

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/files/upload` | POST | multipart 上传，存原始字节（按 `chunk_seq` 切片索引），返回 `{file_id, total_chunks}` |
| `/api/files` | GET | 列出已上传 / 传输中 / 已完成文件 |
| `/api/files/{file_id}/download` | GET | 返回目的端重组后的文件（`Content-Disposition: attachment`） |
| `/api/files/{file_id}` | DELETE | 清理存储（可选） |

**重组逻辑**：后端维护 `file_id → {chunks: {seq: bytes}, received: set}`。收到 DES 经 WS 上报的 `file_chunk_delivered{file_id, seq}` 后，把该 seq 字节标记为已接收；`received` 全集齐后文件可下载。 dropped 分片不释放字节（等待 DES 重传后再次上报送达）。

**约束**：单文件上限（建议 100 MB）、并发文件上限（建议 8）、上传目录配额；超限返回 413 / 429。字节仅存于后端磁盘 / 内存，**不进入 DES 进程**。

---

## 5. 协议 v3.2 增量（向后兼容）

`init.version` 升为 `"3.2"`。新增字段均为可选，旧前端忽略即可。

### 5.1 客户端 → 后端（WS 命令）

```jsonc
{ "type": "file_send",
  "file_id": "f-001", "src": "UAV-01", "dst": "GS-Beijing",
  "prio": 0, "rate_cap_bps": 5000000 }
{ "type": "file_cancel", "file_id": "f-001" }
```

（字节经 HTTP `/api/files/upload` 先行上传，WS 仅触发传输。）

### 5.2 后端 → 客户端（state_update 增量）

```jsonc
"file_transfers": {
  "f-001": {
    "name": "payload.bin", "src": "UAV-01", "dst": "GS-Beijing",
    "state": "TRANSFERRING",
    "progress": 0.42,                 // delivered_bytes / total_bytes
    "delivered_bytes": 44040192, "total_bytes": 104857600,
    "eta_s": 12.3, "throughput_bps": 4950000,
    "path": ["UAV-01","Sat-3-2","Sat-3-3","Sat-7-3","GS-Beijing"],
    "in_flight": 18, "retx": 3
  }
}
```

仅上报**变化**的文件（delta）；完成后保留若干帧再移除。

### 5.3 后端 → 客户端（独立事件）

```jsonc
{ "type": "file_complete", "file_id": "f-001",
  "elapsed_s": 23.7, "retx": 3, "download_url": "/api/files/f-001/download" }
```

---

## 6. 前端

- **上传面板**：文件选择器 + 源节点 / 目的端选择（默认源 = 某 UAV/船、目的端 = 最近地面站）+ 优先级 + 速率上限 + 「开始传输」。先 HTTP 上传拿 `file_id`，再 WS `file_send`。
- **传输追踪器**：并发文件列表，每项含名称、进度条、`delivered/total`、吞吐、ETA、重传次数、状态徽章；支持取消。
- **三维可视化**：高亮该文件当前 `path`（沿跳序节点的折线），一个**脉冲标记**沿路径移动（复用脉冲化包动画风格，见 §8），承载该文件分片的链路着色；点击追踪器条目可聚焦其路径。
- **完成反馈**：`file_complete` → toast + 「下载」按钮（命中 `/api/files/{id}/download`）。

---

## 7. 参数与限制

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `chunk_size` | 16384 B | 分片大小；越小追踪越细但包数越多。背景流量恒为 1500 B 不受影响 |
| `rate_cap_bps` | 0（不限） | 单文件注入速率上限 |
| ARQ 超时 | 1× RTT 估计 | 选择重传；默认开启（真实字节完整落地的必要条件） |
| 单文件上限 | 100 MB | 保护后端内存 / 磁盘 |
| 并发文件上限 | 8 | 保护 DES tick 速率与前端追踪器 |

---

## 8. 与包级动画脉冲化的衔接

包级动画脉冲化（见 ROADMAP「功能里程碑 B」）将把现在的"小圆点"包改为"发光脉冲"。文件分片在三维可视化中的移动标记**直接复用脉冲风格**，并以文件专属颜色区分于背景流量脉冲，使"这条亮带正在传我的文件"一目了然。两项共享 `packet-flow.js` 的渲染基座，建议脉冲化先落地、文件可视化在其上扩展。

---

## 9. 分期计划

- **A 期（控制面）**：`FileTransfer` 模型 + `Packet` 增字段 + 分片注入 / 送达 / 丢弃 / ARQ 重传 + 守恒验证测试（`test_phase8.py` 风格，合成字节，固定种子）。**不涉及真实字节搬运**，仅验证 DES 行为与守恒。
- **B 期（数据面）**：后端 `/api/files/*` HTTP 端点 + 字节存储 + 由 DES 送达事件驱动的重组 + 下载。
- **C 期（前端）**：上传 UI + 追踪器 + 路径高亮 + 脉冲标记 + 下载。
- **D 期（文档）**：协议文档更新（`docs/protocol-v3.2-file-transfer.md`）、README、ROADMAP 实施记录。

---

## 10. 验收标准

1. 上传一个 ≥10 MB 文件，从 UAV 传至地面站，目的端下载文件与原始文件**逐字节一致**（SHA-256 校验）；
2. 传输全程 `test_phase8` 守恒精确（含重传场景：人为制造高丢包，文件仍能完整送达，重传计数 > 0）；
3. 多文件并发（≥3）下，高优先级文件在拥塞中先完成，进度 / ETA / 路径实时刷新；
4. 文件传输不破坏背景流量守恒，1584 星规模下叠加文件传输后核心仍 ≥20 ticks/s；
5. 协议 3.2 向后兼容：旧前端连接新后端不报错（忽略 `file_transfers` 字段）。
