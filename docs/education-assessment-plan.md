# 教学平台改进计划（考核评分闭环 / 身份存档教师端 / 国标对接与内容扩充）

> 目标读者：本项目开发者与执行代理。
> 编制日期：2026-08-26（承接 docs/education-platform-research.md 的差距分析与分期路线，覆盖其 P1/P2 全部条目）
> 对标标准：ilab-x《虚拟仿真实验教学项目技术接口规范（2022 版）》getinfo / score_upload；2020 版建设规范的存档要求。

## 执行状态（2026-08-26 收尾）：全部完成 ✅

| 阶段 | 条目 | 结果 |
|---|---|---|
| 一 考核评分与预习闭环 | W1 评分模型 / W2 预习测验 | 100 分制（对账 70 + 探索 10 + 预习 10 + 思考题 10）；题库答案仅存核心侧，`experiment_quiz` 命令判分；`compose_score` 合成总分 |
| 二 身份/存档/教师端 | S1 登录鉴权 / S2 服务端存档 / S3 提交批改 / S4 教师端 / S5 并发排队 | `realtime_backend/edu.py` + `edu_api.py`（16 个端点）+ `static_html/teacher.html`；实验并发上限 4、超限 FIFO 排队（E2E 实测 6 并发 → 2 queued 全部完成） |
| 三 国标对接与内容扩充 | I1 ilab-x 适配 / I2 统计补全 / I3 E5–E7 / I4 考核模式 | `edu_ilabx.py`（getinfo/score_upload/自检模式）+ 成绩分布与误差热点 + 三个新实验 + 限时固定种子考核 |

**全量验收（2026-08-26）**：
- `pytest tests/` **61 项全部通过**（含 test_experiments 24 项 E1–E7 理论对账与评分闭环、test_ilabx 6 项、test_orbit/packet_sim/phase3/6/7/8/integration_offline 等）；
- `wsdiag/e2e_experiment.py`：目录下发 E1–E7 → 全部 done 且 all_pass → E2 取消重跑 → S5 并发排队（6 路 E7，上限 4，出现 2 个 queued 帧，6 路全部完成）→ **ALL E2E CHECKS PASSED**；
- `tests/test_edu_api.py`：S1–S4 + I2 + I4 全链路 12 组断言通过（含教师批改总评 = (80+90)/2 = 85.0、成绩册 CSV、班级缺交、考核创建/绑定/收卷）；
- `tests/test_milestone_c.py`（核心 `--scale 1584`）：5 对全终端文件传输 SHA-256 全 PASS；
- `tests/test_file_e2e.py`：F1–F5 全 PASS（本次补 no_proxy 环境加固）。

---

## 阶段一：考核评分与预习闭环

### W1 评分模型（100 分制）

`satviz/experiments.py`：

- `SCORE_MAX = {verdict: 70, explore: 10, quiz: 10, questions: 10}`；
- 对账判定逐行分档：通过拿满分行分；未通过但误差 ≤ 2×容差给半分（`_score_verdict`）；
- 参数探索分：非默认参数运行即得分（鼓励调参重跑）；
- 预习测验 + 思考题由前端采集选项/文本、核心判分（`grade_quiz` / `grade_questions`），`compose_score` 合成总分。

### W2 预习测验题库

- `QUIZZES`：每实验 3 题选择题，答案与解析仅在核心侧，前端不下发；
- `experiment_quiz` 命令携带选项序号判分，回传得分/正确数/解析。

## 阶段二：身份 / 存档 / 教师端

### S1 学生注册登录（edu.py / edu_api.py）

- `POST /api/edu/login`：学号+姓名即注册/登录，返回 `X-Edu-Token`（后续请求头携带）；教师登录需口令（默认 `starbridge`，环境变量 `STARBRIDGE_TEACHER_CODE` 覆盖）；
- 无 token 401；学生访问教师端点 403；他人记录对学生不可见。

### S2 服务端存档（对齐 2020 规范"可恢复"要求）

- `POST /api/edu/records`：对账表 + 步骤日志 + 评分明细 + 思考题作答整体落库（JSON 持久化，`STARBRIDGE_EDU_DB` 指定路径）；
- 步骤日志字段对齐 ilab-x 2022 版结构：`seq/title/startTime/endTime/timeUsed/maxScore/score/repeatCount/scoringModel/evaluation`；
- `GET /api/edu/records`：登录后拉回自己的记录（刷新/换机可恢复）。

### S3 报告提交与教师批改

- `POST /records/{id}/submit`：草稿 → `submitted`；
- `POST /records/{id}/review`（教师）：评语 + 主观分；总评 = (自动分 + 主观分) / 2。

### S4 教师端（static_html/teacher.html）

- 应用统计（申报佐证）：记录总数 / 学生数 / 累计时长 / 平均分 / 已提交数；
- 成绩册：每学生 × 实验取最佳记录；实验筛选 + "仅本班"筛选；CSV 导出；
- 班级管理：文本粘贴导入名单（每行首列为学号，逗号/Tab/空格分隔），缺交统计（已交/缺交/平均分，缺交学号高亮）；
- 批改弹窗：查看对账结论与思考题作答，写评语/主观分。

### S5 实验并发处理（多客户端机房场景）

- `MAX_CONCURRENT_EXPERIMENTS = 4`；超出即入 FIFO 队列（`queued` 帧 + `queue_pos`），前面的任务结束/取消后 `_pump_experiment_queue` 自动补位；
- `experiment_cancel` 支持 run_id 定点取消（含排队中的）或全部取消。

## 阶段三：对齐国家标准与内容扩充

### I1 ilab-x 适配层（realtime_backend/edu_ilabx.py）

- `getinfo(sid)`：学号换学生信息（平台→实验系统方向）；
- `score_upload(record)`：实验记录 → 2022 版报文（`sid/sname/exp_id/exp_name/score/step_data[]/…`；步骤数据转换 `seq/title` → `step_id/step_name`）；
- **自检模式**：未配置 `STARBRIDGE_ILABX_ENDPOINT` 时报文落盘 outbox（追加不覆盖）、不发任何网络请求；字段校验缺失即拒发；
- CLI：`python -m realtime_backend.edu_ilabx` 自检完整走通 getinfo → score_upload。

### I2 统计补全（教学诊断）

- `GET /api/edu/stats` 新增：`score_distribution`（90-100 / 80-89 / 70-79 / 60-69 / <60 五段，按总评）；
- `error_hotspots`：按实验 × 判据统计失败次数与 Top3 高频失败判据 —— 教师端"对账失败热点"表格直出，支撑"哪一步学生最容易错"的教学诊断。

### I3 新实验 E5–E7（satviz/experiments.py）

| 实验 | 场景 | 理论锚点 |
|---|---|---|
| E5 路由算法对比 | 源 600 pps 走捷径（容量 400 pps）或绕行长路，对比最短时延 vs 负载感知路由 | 拥塞：最短时延丢 (λ−C)/λ=33%，负载感知绕行近零；畅通（λ ≤ C）：两路由 e2e 相当（±5%） |
| E6 星座规模探索 | P×M 网格拓扑，跳数与规模关系 | 跳数 = (P−1) + ⌊M/2⌋（曼哈顿最短路），e2e = 接入 + 跳数×ISL + 下行 |
| E7 链路预算雨衰 | Ka 波段雨衰 a dB 压低下行容量 | 容量 ×10^(−a/10)；拥塞丢包 = 1 − C_eff/λ；门限判定（雨衰调高找中断点） |

- 负载感知路由落在引擎层：`packet_sim.py` 新增 `routing_metric` 配置（`delay` 默认 / `load_aware`），链路权重 = 传播时延 × (1 + 9×队列填充率)，Dijkstra 自动绕开拥塞链路；
- 种子参数化：`params._seed` 保留键覆盖默认种子（考核可复现）。

### I4 考核模式（限时 · 固定种子 · 参数冻结）

- `POST /api/edu/exams`（教师）：名称 / 实验集 / 时长（分钟）/ 固定种子 / 参数冻结集；`POST /exams/{id}/end` 收卷；
- 学生端 lab.js：考核横幅 + 倒计时（最后 5 分钟告警），参数锁定只读，运行时注入 `_seed` 与 `exam_id`（成绩册可追溯考核来源）；到时自动收卷并解锁。

---

## 关键技术决策与环境坑（2026-08-26 实测）

1. **实验并发排队曾被事件循环饥饿掩盖（已修）**：E7 的 `eng.advance(45.0)` 单次排空约 3 s 墙钟，期间主循环收不下一条命令、WS 心跳超时断连，6 条并发命令被逐条消化，永远凑不满 4 路上限。修复：`experiments.py` 新增 `_advance()` 分块推进（默认 2 s 仿真块，块间 checkpoint 让出事件循环）；DES 事件按绝对时间排空，切块与整块语义等价（E2 测量窗本就 12 块推进）。
2. **E5 是分块推进的例外（有意保留整块）**：`route_refresh_interval=0.5 s` 下分块会让路由随队列涨落反复翻转（翻振），瞬态丢包升至 ~8%；整块推进时测量窗内路由只按预热末队列状态计算一次，负载感知稳定绕行、近零丢包。E5 仅 ~30 k 包，无饥饿问题。代码内有注释说明。
3. **ilab-x 步骤字段映射**：lab.js 步骤日志用 `seq/title`，适配层转换补 `step_id/step_name`。
4. **test_milestone_c 需要核心 `--scale 1584`**（文档已注明）：默认 105 节点规模下 `Sat-20-10` / `Sat-40-7` 不可达，属配置差异而非回归。
5. **Windows 系统代理劫持 127.0.0.1**：test_file_e2e.py 本次补上 `no_proxy` 环境加固（与 test_edu_api / test_milestone_c 同坑同修）。

## 遗留问题（不阻塞交付）

- ilab-x 真实对接需学校端分配的回调地址与密钥，当前为自检模式（报文落盘可审计）；
- E2 取消存在罕见竞态（实验过快结束时取消帧与 done 帧赛跑，E2E 已按"两者皆可接受"处理）；
- 考核计时在前端，学生改系统时间可绕过（教师端可看提交记录兜底）；
- 教师端成绩册暂未单列预习/思考题得分明细列（记录内已存，可后续加列）。
