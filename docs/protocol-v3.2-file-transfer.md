# 文件传输协议规范 v3.2

> 适用项目：空天海网络可视化实时交互仿真演示系统
> 日期：2026-07-30
> 状态：已实施（功能里程碑 A — 控制面 / 数据面 / 前端 / 文档四期完成）
> 前置文档：`docs/protocol-v3-packet-level.md`（v3.0 包级遥测）、`docs/file-transfer-design.md`（设计稿）
> 维护：李思达

---

## 1. 背景与目标

v3.0 让链路指标从真实数据包转发中涌现，但网络上流动的只有系统内置的 Poisson 背景流。v3.2 的目标是：**让用户上传任意文件，指定源节点 / 目的端 / 优先级，文件字节真正"经过"仿真网络逐跳转发，到达目的端地面站后可下载还原，并全程实时追踪。**

v3.2 是 v3.0 / v3.1 的**增量扩展**（additive）：

- 所有既有字段保持不变、含义不变；
- 新增字段（`state_update.file_transfers`）与新增命令（`file_send` / `file_cancel`）全部为**可选**；
- 旧前端连接新后端不报错——忽略 `file_transfers` 字段即可正常渲染（验收标准 5）。

版本协商沿用既有机制：`simulation_init.payload.version` 升为 `"3.2"`，前端按主版本 `"3"` 解析并对新字段做存在性兜底。

---

## 2. 核心设计：控制面 / 数据面分离

文件传输最关键的架构决策是**不把真实字节塞进离散事件仿真器（DES）**。

- **控制面（DES，`packet_sim.py`）**：文件被建模为一串**抽象分片**——`Packet` 仅新增两个可选字段 `file_id` 与 `chunk_seq`。DES 负责注入时序、逐跳路由、排队、丢包与超时重传（选择重传 ARQ），并持续上报"第几片送达"。全程**不持有任何 payload 字节**，因此 tick 速率与已验证的包守恒模型不受影响。
- **数据面（后端，`realtime_backend/files.py`）**：后端持有上传的原始字节，按 `chunk_seq` 切片落盘（仅存一份）；收到 DES 经 WebSocket 上报的送达事件后标记对应分片已接收；全部分片齐备即拼接重组并做 SHA-256 校验，校验通过才开放下载。

两个平面通过"分片送达 / 完成 / 取消事件"**单向耦合**（DES → 后端）。包守恒不变量 `生成 = 送达 + 丢弃 + 在途` 对文件分片与背景流量一视同仁，精确成立（重传包计为新生成包）。

```
┌────────────┐  HTTP multipart    ┌──────────────────────────────────┐
│  前端上传   │ ─────────────────▶ │ 后端数据面: 字节存储 (按 seq 切片) │
└────────────┘                    └───────────────┬──────────────────┘
      │ WS file_send                               │ 重组 + SHA-256 → 下载
      ▼                                            ▲
┌─────────────────────────────────────────────────┴──────────────┐
│ DES 控制面: FileTransfer 模型                                    │
│   分片注入 → 反向Dijkstra路由 → 排队/丢包 → ARQ重传 → 送达         │
│   上报事件: file_started / file_chunk_delivered /                 │
│             file_complete / file_cancelled                        │
│   上报指标: state_update.file_transfers                           │
│             (progress / path / eta / throughput / retx)           │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据面：HTTP 端点（`/api/files`）

后端新增一组 REST 端点承载真实字节，与 WebSocket 遥测通道解耦。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/files/upload` | POST | multipart 上传（表单字段名 `file`），落盘并按 `chunk_size` 切片，返回文件记录 |
| `/api/files` | GET | 列出全部已跟踪传输（含进度 / 状态） |
| `/api/files/{file_id}` | GET | 单个传输记录 |
| `/api/files/{file_id}/download` | GET | 返回重组并校验通过的文件（`Content-Disposition: attachment`） |
| `/api/files/{file_id}` | DELETE | 清理存储（记录 + 磁盘分片） |

### 3.1 上传响应 / 文件记录字段

`POST /api/files/upload`、`GET /api/files`、`GET /api/files/{id}` 返回的记录结构一致：

```json
{
  "file_id": "1ccd30d903c4",
  "name": "屏幕截图.png",
  "total_bytes": 44856,
  "chunk_size": 16384,
  "total_chunks": 3,
  "received_chunks": 3,
  "received_bytes": 44856,
  "progress": 1.0,
  "sha256": "34092f78…b539",
  "src": "Ship-01",
  "dst": "Beijing",
  "state": "COMPLETE",
  "reassembled_sha256": "34092f78…b539",
  "verified": true,
  "created_at": 1785340207.04,
  "completed_at": 1785340209.31
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `file_id` | string | 后端生成的唯一 ID（12 位十六进制） |
| `name` | string | 上传文件名（仅展示，下载时作为附件名） |
| `total_bytes` / `chunk_size` / `total_chunks` | number | 总字节 / 分片大小 / 分片数 = `ceil(total_bytes / chunk_size)` |
| `received_chunks` / `received_bytes` | number | 已由 DES 送达的分片数 / 字节数 |
| `progress` | number | `received_bytes / total_bytes`，保留 4 位小数 |
| `sha256` | string | 上传原始文件的 SHA-256 |
| `src` / `dst` | string\|null | 源节点 / 目的端（`file_send` 前为 null） |
| `state` | string | `STORED` / `TRANSFERRING` / `COMPLETE` / `CANCELLED` / `FAILED` |
| `reassembled_sha256` | string\|null | 重组文件的 SHA-256（完成后填充） |
| `verified` | boolean | `reassembled_sha256 == sha256`，逐字节一致的判据 |
| `created_at` / `completed_at` | number | Unix 时间戳（秒） |

**状态机**：`STORED`（已上传未发送）→ `file_send` 后 `TRANSFERRING` → 全部分片送达且校验通过 `COMPLETE`；用户取消或仿真时间不连续（seek/stop/reset）中断 → `CANCELLED`；重组哈希不匹配 → `FAILED`（理论上不应发生）。

### 3.2 下载守卫

`GET /api/files/{id}/download` 仅在 `state == COMPLETE` 时返回 200 + 字节流；否则：

- `404` — file_id 不存在；
- `409` — 传输未完成（响应体含当前 state）；
- `410` — 重组数据缺失。

### 3.3 约束

| 参数 | 默认 | 说明 |
|---|---|---|
| `chunk_size` | 16384 B | 与 DES 分片大小一致（后端为单一事实源，`file_send` 时显式带给核心） |
| 单文件上限 | 100 MB | 超限返回 400 |
| 并发文件上限 | 64 | 超限返回 400 |

字节仅存于后端磁盘（`realtime_backend/data/files/<file_id>/`），**不进入 DES 进程**。

---

## 4. 控制面：WS 命令（客户端 → 后端 → 核心）

命令沿用既有 `CommandMessage` 信封，后端识别 `file_send` / `file_cancel` 并**富化**后转发核心。

### 4.1 file_send

```json
{
  "message_type": "command",
  "payload": {
    "action": "file_send",
    "params": {
      "file_id": "1ccd30d903c4",
      "src": "Ship-01",
      "dst": "Beijing",
      "prio": 1,
      "rate_bps": 5000000
    }
  }
}
```

| 参数 | 必需 | 说明 |
|---|---|---|
| `file_id` | 是 | 必须已经 `/api/files/upload` 上传，否则后端回 `unknown_file` 错误 |
| `src` | 是 | 源节点 ID（UAV / 船） |
| `dst` | 是 | 目的端地面站 ID |
| `prio` | 否 | QoS 优先级，0=高（默认见核心配置），1=尽力 |
| `rate_bps` | 否 | 单文件注入速率上限（bits/s），缺省取核心 `file_default_rate_bps` |

后端在转发前注入 `total_bytes` / `chunk_size` / `name`（取自存储记录），并把记录状态置为 `TRANSFERRING`。**核心侧行为**：若仿真时间已临近 / 到达 `duration` 上限，自动延长 120 s 并恢复播放，避免仿真结束后上传的文件永久卡死。

### 4.2 file_cancel

```json
{ "message_type": "command",
  "payload": { "action": "file_cancel", "params": { "file_id": "1ccd30d903c4" } } }
```

后端把记录置为 `CANCELLED`，核心清除该文件的在途分片。

### 4.3 应答与错误

后端对每条命令回 `ack`（`status: "forwarded"`）；file_id 未上传时回：

```json
{ "message_type": "error",
  "payload": { "status": "unknown_file",
               "detail": "file_id not found; upload it first via /api/files/upload." } }
```

---

## 5. 遥测：`state_update.file_transfers`（核心 → 后端 → 客户端）

`state_update.payload` 新增可选顶层字段 `file_transfers`——一个以 `file_id` 为 key 的字典，**每个仿真帧携带全部活跃传输的当前快照**（无传输时该字段缺省 / 为 null）。

```json
"file_transfers": {
  "1ccd30d903c4": {
    "name": "屏幕截图.png",
    "src": "Ship-01",
    "dst": "Beijing",
    "state": "TRANSFERRING",
    "progress": 0.42,
    "delivered_bytes": 18874,
    "total_bytes": 44856,
    "eta_s": 12.3,
    "throughput_bps": 4950000,
    "path": ["Ship-01", "Sat-3-0", "Sat-3-1", "Beijing"],
    "in_flight": 2,
    "retx": 3
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` / `src` / `dst` | string | 文件名 / 源 / 目的端 |
| `state` | string | `TRANSFERRING` / `COMPLETE` / `CANCELLED` |
| `progress` | number | `delivered_bytes / total_bytes`（0–1） |
| `delivered_bytes` / `total_bytes` | number | 已送达字节 / 总字节 |
| `eta_s` | number | 预计剩余时间（秒）= 剩余字节 / 当前吞吐；非传输中为 0 |
| `throughput_bps` | number | 平均吞吐（bits/s）= 已送达字节 / 已用时间 × 8 |
| `path` | string[] | 当前转发链 `src → … → dst`（节点 ID 列表，供路径高亮） |
| `in_flight` | number | 已注入未确认的分片数（ARQ 窗口） |
| `retx` | number | 累计重传次数 |

**语义约定**：

- `throughput_bps` 是自传输开始以来的**平均吞吐**（非瞬时窗口），`eta_s` 据此线性外推；
- `path` 由当前路由表 best-effort 还原，拓扑切换改路后随之更新；
- 完成后该条目仍保留在字典中（state=COMPLETE），前端可据此触发完成反馈与下载按钮。

---

## 6. 内部事件：`file_event`（核心 → 后端，**不广播**）

核心通过其 WebSocket 向后端发送 `file_event` 消息驱动数据面重组。该消息**仅用于后端内部**，后端**不会**将其转发给前端客户端（前端只消费 `state_update.file_transfers` 与 HTTP 端点）。

```json
{ "message_type": "file_event",
  "events": [ { "type": "file_chunk_delivered", "file_id": "…", "seq": 2, "bytes": 16384 } ] }
```

| 事件类型 | 字段 | 触发时机 / 后端动作 |
|---|---|---|
| `file_started` | `file_id, name, src, dst, total_bytes, total_chunks, prio` | `start_file` 注册时（后端已在 `file_send` 时置 TRANSFERRING） |
| `file_chunk_delivered` | `file_id, seq, bytes` | 分片送达目的端 → 标记该 seq 已接收 |
| `file_complete` | `file_id, elapsed_s, retx, total_bytes` | 全部分片送达 → 拼接重组 + SHA-256 校验 → COMPLETE |
| `file_cancelled` | `file_id` | 用户取消，或仿真时间不连续（seek/stop/reset）中断 → CANCELLED |

> `file_cancelled` 由 `flush()` 在时间不连续时主动补发，保证被中断的传输在后端被标记为 `CANCELLED`，而非永久滞留 `TRANSFERRING`。

---

## 7. 完整交互时序

```
前端                      后端                         核心(DE S)
 │  POST /api/files/upload  │                              │
 │ ───────────────────────▶ │ 落盘切片 + SHA-256            │
 │ ◀─────────────────────── │ {file_id, state:STORED}      │
 │  WS file_send{file_id…}  │                              │
 │ ───────────────────────▶ │ 富化 total_bytes/chunk_size   │
 │                          │ 置 TRANSFERRING ────────────▶ │ start_file → FGEN 注入分片
 │ ◀── ack{forwarded} ───── │                              │ 反向Dijkstra路由 → 逐跳转发
 │                          │                              │ ARQ 超时重传(如需)
 │                          │ ◀── file_event(chunk_delivered)│ 分片送达
 │                          │ 标记 seq 已接收               │
 │ ◀── state_update.file_transfers(progress↑) ──────────── │ 每帧快照
 │                          │ ◀── file_event(file_complete) │ 全部送达
 │                          │ 重组 + SHA-256 校验 → COMPLETE │
 │ ◀── state_update{state:COMPLETE} ──────────────────────  │
 │  GET /api/files/{id}/download                            │
 │ ───────────────────────▶ │ 200 + 重组字节                │
 │ ◀─────────────────────── │ (SHA-256 与上传一致)          │
```

---

## 8. 前端适配要点

- **上传面板**（`#filePanel`）：文件选择器 + 源节点 / 目的端下拉（源默认列出 UAV / 船，目的端列出地面站）+ 优先级 + 速率上限 + 「上传并发送」。点击后先 `fetch` 上传拿 `file_id`，再 WS `file_send`。
- **传输追踪器**：消费 `state_update.file_transfers`，每项渲染名称、进度条、`delivered/total`、吞吐、ETA、重传、状态徽章；支持取消（`file_cancel`）与下载（命中 `/api/files/{id}/download`）。
- **路径高亮**：选中某传输条目 → 用其 `path` 调用链路高亮（`highlightRoute`），优先于演示自动轮播。
- **存在性兜底**：`file_transfers` 缺失时不渲染面板列表，保证接 v3.0 / v3.1 核心不报错、不白屏。

---

## 9. 版本协商（更新）

前端收到 `simulation_init` 后按 `payload.version` 主版本分支：

- `"3.2"`：本规范（v3.0 包级遥测 + 文件传输）。
- `"3.0"` / `"3.1"`：包级遥测，无文件传输（`file_transfers` 显示为空）。
- `"2.0"`：v2 多域节点协议（无包级字段）。

v3.2 前端必须对 `file_transfers` 做存在性兜底；旧前端接 v3.2 后端时忽略该字段即可。

---

## 10. 实现索引

| 组件 | 文件 |
|---|---|
| DES 控制面（FileTransfer / ARQ / 事件 / 快照） | `hypatia-master/satviz/packet_sim.py` |
| 核心命令接入 / 富化转发 / init 版本 | `hypatia-master/satviz/demo_sim_core.py` |
| 后端数据面（字节存储 / 重组 / 校验） | `realtime_backend/files.py` |
| 后端 HTTP 端点 | `realtime_backend/files_api.py` |
| 后端命令富化 / 事件拦截 | `realtime_backend/main.py` |
| 前端上传面板 / 追踪器 / 路径高亮 | `hypatia-master/satviz/js/ui-controller.js`、`js/app.js`、`static_html/index.html` |
| 命令行客户端 | `tools/file_transfer_client.py` |
| 端到端测试 | `tests/test_file_e2e.py`（上传→传输→下载 SHA-256 一致 + 取消） |
| 守恒测试 | `hypatia-master/satviz/test_phase8.py`（文件分片计入守恒，含重传场景） |
