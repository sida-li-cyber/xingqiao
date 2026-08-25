# 教育化升级实施计划（改进 #1 / #2 / #3）

> 目标读者：本项目开发者与执行代理。执行方式：本会话内逐阶段实施，每阶段结束跑验收命令。
> 编制日期：2026-08-25（基于对 V3 代码库与 V4 SGP4 实现的三路调研）

## 执行状态（2026-08-25 收尾）：全部完成 ✅

| 阶段 | 结果 |
|---|---|
| A 一键启动 + 免 Token | `start_starbridge.bat/sh` + `stop_starbridge.bat` + 免 Token 底图；桌面实测核心连接、零 404、105 节点、116 FPS |
| B SGP4 真实轨道 | `ephemeris.py` / `tle_source.py` 等自 V4 复制（哈希前后一致，V4 零改动）；O1–O7 通过；全量回归通过；`--tle celestrak:starlink` 可用 |
| C 四大实验产品化 | `experiments.py`（E1–E4 沙箱+理论对账）+ 前端「教学实验」面板 + HTML 实验报告导出 + 四份指导书（docs/experiments/）；E2E 全链路验证：目录下发 / E1–E4 全 PASS / 取消路径正常 |
| D 回归、文档与收尾 | `tests/` 38 项全通过（含新 test_experiments 7 项、test_milestone_c 集成 2 项——已修复系统代理导致的 502）；README / GUIDE 已更新 |

E2E 实测示例（固定种子，与 tests 输出一致）：
E1 误差 0.000 ms；E4 HIGH e2e 35.6 ms / loss 0.000，BE e2e 1064.9 ms / loss 0.605。

---

**目标**：把"星桥"从研究原型推进到参赛可演示的教学产品——一键启动、免 Token、四大实验产品化、SGP4 真实轨道。

**总体原则**：
- V4 文件夹**只读**，任何文件不修改、不删除、不重命名，仅 `Copy-Item` 复制到 V3；
- V3 现有行为默认不变（circular 轨道、现有协议、现有测试全量回归通过）；
- 所有新能力都是加法：新文件、新参数、新命令、新面板。

---

## 调研结论摘要（计划依据）

| 事实 | 来源 |
|---|---|
| 启动需 3 进程：后端 `python -m realtime_backend.run --port 8000`（根目录）；核心 `python demo_sim_core.py`（hypatia-master/satviz）；前端 `python -m http.server 8080`（hypatia-master/satviz/static_html） | realtime_backend/run.py、demo_sim_core.py、GUIDE.md |
| Cesium Token 存 localStorage（`cesiumToken`），由用户在 UI 输入；未配置则地球瓦片加载失败 | js/app.js:50、js/cesium-manager.js:258、js/ui-controller.js:858 |
| V4 SGP4 为双 Provider 架构：`CircularProvider`/`SGP4Provider` 统一接口 `get_position(t) -> (lat, lon, alt_m)`；V3 圆轨道内联在 `Satellite.get_position` | V4 hypatia-master/satviz/ephemeris.py、demo_sim_core.py |
| V4 SGP4 自包含模块：`ephemeris.py`、`tle_source.py`、`tle_history.py`、`data/starlink_sample.tle`、`tests/test_orbit.py`（O1~O8） | V4 目录调研 |
| 四大实验的参数与理论值已在测试中固化：E1 三跳 21.037ms（test_packet_sim.py Test1）；E2 M/D/1 ρ=0.8 理论 24.00ms vs 实测 23.60ms（test_phase6.py Test8）；E3 切换尖峰 201 包（test_phase3.py Test3）；E4 QoS 双流 60pps 瓶颈 1Mbps（test_phase3.py Test4） | tests/ 目录 |
| 前端无组件框架：面板 = index.html 的 `div.glass.side-panel` + ui-controller.js `_bindCollapse()`；图表 = chart.js 的 Canvas `TimeSeriesChart`；新命令走 `command` 通道，核心在 `demo_sim_core.py handle_command()` 分发 | 前端调研 |
| 后端是透明转发层：核心→客户端消息由 ConnectionManager 广播（simulation_init 缓存重放） | realtime_backend/core.py |

---

## 阶段 A：一键启动 + 免 Token（改进 #1）

**A1. 运行时依赖清单**（新增 `requirements-runtime.txt`，根目录）

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
PyYAML>=6.0
pydantic>=2.0
pydantic-settings>=2.0
websockets>=12.0
sgp4>=2.21
numpy>=1.26
```

不引入 hypatia 全量科研依赖（astropy/cartopy 等），保持"轻量"承诺。

**A2. 一键启动 `启动星桥.bat`（根目录）+ `start.sh`**

bat 逻辑：
1. `chcp 65001` + 切到脚本所在目录（`cd /d %~dp0`）；
2. 检测 `python`（`where python`，失败则提示安装并暂停退出）；
3. 若无 `.venv` 则 `python -m venv .venv`；激活；
4. `pip install -r requirements-runtime.txt --quiet`（已装则秒过）；
5. 端口预检：`netstat -ano | findstr :8000/:8080`，被占则提示并带 `/K` 参数复用或换 8001/8081（默认直接尝试续用，失败提示）；
6. 三个后台窗口启动（`start "星桥-后端" cmd /k ...`）：
   - 后端：`python -m realtime_backend.run --port 8000`（根目录）
   - 核心：`python demo_sim_core.py --port 8000`（hypatia-master\satviz）
   - 前端：`python -m http.server 8080`（hypatia-master\satviz\static_html）
7. 等待 6 秒后 `start http://127.0.0.1:8080/index.html` 打开浏览器；
8. 主窗口提示"关闭本窗口不会停止服务；双击 停止星桥.bat 一键停止"。

`停止星桥.bat`：按窗口标题 `taskkill /FI "WINDOWTITLE eq 星桥-*"` 杀三个子窗口进程。

**A3. 免 Token 模式（改 `js/cesium-manager.js` + `js/app.js` + `js/ui-controller.js`）**

方案：无 token 时改用 **OpenStreetMap 栅格瓦片 + 椭球地形**（不经过 Cesium Ion，零 token 合法可用）：

```javascript
// cesium-manager.js — 构造 Viewer 处
const hasToken = !!this.cesiumToken;
if (hasToken) {
  Cesium.Ion.defaultAccessToken = this.cesiumToken;
  this.viewer = new Cesium.Viewer(container, { ...现有参数 });
} else {
  this.viewer = new Cesium.Viewer(container, {
    baseLayer: new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
      url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      credit: "© OpenStreetMap contributors"
    })),
    terrainProvider: new Cesium.EllipsoidTerrainProvider(),
    ...其余参数保持
  });
}
```

配套：
- app.js：无 token 不再阻塞初始化；控制台输出"未配置 Token，已使用免 Token 模式（OSM 底图）"；
- ui-controller.js：Token 设置对话框文案补一句"留空即可使用免 Token 模式"；
- index.html：脚本版本参数 `?v=3k4` → `?v=3k5` 强制刷新缓存。

**A4. 验收（阶段 A 完成标准）**

- [ ] 删除 `.venv` 与浏览器 localStorage 的 token，双击 `启动星桥.bat`：全自动安装→三进程启动→浏览器打开→OSM 地球可见、卫星/链路渲染正常；
- [ ] 配置 token 后仍是 Ion 高清底图（两模式并存）；
- [ ] `停止星桥.bat` 能清掉三个进程；
- [ ] 现有 `tests/test_reconnect.py` 通过（协议未动，应不受影响）。

---

## 阶段 B：SGP4 真实轨道（改进 #3，从 V4 只读复制）

**B1. 复制清单（V4 → V3，`Copy-Item`，绝不写 V4）**

| 源（V4，只读） | 目标（V3） |
|---|---|
| `hypatia-master\satviz\ephemeris.py` | `hypatia-master\satviz\ephemeris.py` |
| `hypatia-master\satviz\tle_source.py` | `hypatia-master\satviz\tle_source.py` |
| `hypatia-master\satviz\tle_history.py` | `hypatia-master\satviz\tle_history.py` |
| `hypatia-master\satviz\data\starlink_sample.tle` | `hypatia-master\satviz\data\starlink_sample.tle` |
| `tests\test_orbit.py` | `tests\test_orbit.py` |

不复制：`real_geometry.py`（research 双星历几何工具，主链路不需要）、`twin_*.py`（数字孪生，超参赛范围）、10~30MB 历史归档 TLE（教学用 sample 子集足够；链接在文档注明获取方式）。若 `test_orbit.py` 引用了未复制模块，则同步最小复制或在 V3 副本中裁剪该引用（改 V3 副本，不动 V4 原件）。

**B2. V3 核心接入（改 `hypatia-master\satviz\demo_sim_core.py`，最小侵入）**

1. `Satellite.__init__` 增加 `provider=None` 形参；`get_position(t)` 改为委托：
   ```python
   if provider is None:
       provider = CircularProvider(alt_km, incl_rad, raan_rad, ma_rad)
   self.provider = provider
   def get_position(self, t):
       return self.provider.get_position(t)
   ```
   CircularProvider 内部公式与 V3 现内联实现一致（V4 已对账，O4 验证两者仅在 J2 摄动下缓慢发散）→ 默认行为不变。
2. 新增模块级函数（照搬 V4）：`satellites_from_tle_records()`、`create_constellation_from_tle()`；真实 TLE 星座 `shell=plane=idx=-1`。
3. 几何 ISL 分支：`self._isl_geometric = any(s.shell < 0 ...)` 为真时，ISL 用位置 k 近邻生成（V4 同名逻辑照搬），否则维持结构化 plane/index 邻接。
4. 命令行参数：`--ephemeris {circular,sgp4}`（默认 circular）、`--tle <path>`（优先级高于 ephemeris）、`--epoch <ISO时间>`；`simulation_init.payload` 增加 `ephemeris: {mode, tle_source, sat_count}`。
5. `tle_source.resolve_tle()` 支持 `celestrak:starlink` 在线获取+缓存，离线自动回退 `data/starlink_sample.tle`。

**B3. 测试**

- 跑复制来的 `tests/test_orbit.py`：O1~O7 必须通过（O8 千星长测标记 `--long` 跳过）；
- V3 全量回归：`test_packet_sim / test_phase3 / test_phase6 / test_phase7 / test_phase8 / test_integration_offline / test_milestone_c`。

**B4. 验收**

- [ ] `python demo_sim_core.py --ephemeris sgp4`（合成 TLE）与 `--tle data/starlink_sample.tle`（真实 Starlink）均能起核心并发 `simulation_init`；
- [ ] O1~O7 通过；circular 默认模式全量回归通过（旧测试一个不改）；
- [ ] V4 文件夹哈希前后一致（复制后抽查 `git status` 式比对——V4 无 git，用文件时间戳/大小快照核对未变）。

---

## 阶段 C：四大实验产品化（改进 #2）

**C1. 核心侧实验运行器 `hypatia-master\satviz\experiments.py`（新文件）**

设计决策：**旁路实验台**——实验在核心进程内的独立 `PacketEngine` 沙箱中运行（复用 tests 的场景构造与理论公式），与主星座仿真互不干扰。理由：参数确定性（与测试同源）、秒级完成、理论对账可精确复现；主星座保持实时漫游供直觉观察。

```python
EXPERIMENTS = {
  "E1": {...}, "E2": {...}, "E3": {...}, "E4": {...},
}
# 每个 Experiment 定义：
#   meta: id/name/理论要点/指导书锚点
#   build(engine_config) -> PacketEngine 场景（参数取自 tests，见下表）
#   theory(engine) -> {"label": 理论值...}   # 闭式公式
#   measure(engine) -> {"label": 实测值...}  # 从引擎 summary 读
#   verdict(theory, meas) -> [(label, 理论, 实测, 误差, 判定)]
```

四个实验参数与理论值（全部来自现有测试，已对账）：

| 实验 | 场景参数 | 理论基准 | 通过判据 |
|---|---|---|---|
| E1 时延对账 | 三跳 UAV→Sat→Sat→GS，传播 5+10+6ms，10pps 轻载 | e2e = Σ传播 + 发送时延 ≈ 21.04ms | 误差 ≤ 1ms（tests 标准） |
| E2 M/D/1 排队 | 瓶颈 2Mbps，包 1500B（服务 6ms），λ=133.33pps，ρ=0.8 | Wq=ρs/(2(1−ρ))=12ms；e2e≈24.00ms | 误差 ≤ 3ms（tests 标准） |
| E3 切换丢包 | SUL 1Mbps 瓶颈，源超速堆队列（容量 200），中途移除活跃链路 | 尖峰 = 200 在队 + 1 在途 = 201 | 尖峰计数精确一致 |
| E4 QoS 优先级 | 双流各 60pps（HIGH/BE）共享 1Mbps 瓶颈 | 高优先零丢包低时延；BE 承担丢包 | HIGH 丢包 < BE 丢包 且 HIGH 时延 < BE 时延 |

运行方式：`async def run_experiment(exp_id, on_progress)` —— 分 tick 驱动引擎，`on_progress(stage, pct, partial)` 推进度；结束产出 `verdict` 行集 + 结论。

**C2. 协议扩展（改 `demo_sim_core.py handle_command()` + `realtime_backend/core.py` 若有白名单）**

- 新 action：`experiment_run {exp_id}`、`experiment_cancel {}`；
- 核心新消息：`experiment_update`（`payload: {exp_id, stage, progress, verdict[], done, error}`）广播给客户端；后端 core.py 若按 message_type 白名单转发则补一行，否则零改动；
- 实验互斥：同一时刻只跑一个实验，运行中新请求返回 `error: "experiment_busy"`。

**C3. 前端实验面板（改 `static_html/index.html` + 新 `js/experiment.js` + `js/websocket.js` 加 sendExperimentCommand）**

- 新面板 `#experimentPanel`（复用 `glass side-panel` + `_bindCollapse` 模式，右侧，位于文件面板下方）；
- 内容：四张实验卡片（名称+一句话理论）、「运行」按钮、进度条、结果表（指标 | 理论值 | 实测值 | 误差 | 判定✔/✘）、「生成实验报告」按钮；
- 实验报告：弹层内可编辑 姓名/学号/班级 + 自动填入实验参数与对账结果 → 一键下载 `.txt`（或复制到剪贴板）。报告文案含"本结果由星桥平台自动对账生成"；
- `experiment.js` 订阅 `experiment_update` 消息驱动 UI；`index.html` 脚本 `?v=3k5`。

**C4. 实验指导书 `docs/experiments/E1-时延分解与对账.md` ~ `E4-QoS优先级.md`**

每份结构：实验目的 → 预备知识（公式推导）→ 平台操作步骤（对应面板按钮）→ 观测点与记录表 → 思考题（2~3 道）→ 评分标准（对账误差分档：≤1ms 满分 / ≤3ms 良好 / 其他重做）。

**C5. 验收**

- [ ] 前端依次运行 E1~E4：全部得到"通过"判定，数值与 tests 输出一致（E2 实测 ≈23.6ms、E3 尖峰 201、E4 HIGH 丢包 0）；
- [ ] 实验运行期间主星座仿真不中断；报告可下载；
- [ ] 新增 `tests/test_experiments.py`：直接调 `experiments.py` 断言四组 verdict 全过（进 CI 套件）。

---

## 阶段 D：回归、文档与收尾

- [ ] 全量测试清单（一条命令逐个跑并记录）：`test_packet_sim, test_phase3, test_phase6, test_phase7, test_phase8, test_integration_offline, test_reconnect(如适用), test_file_e2e, test_milestone_c, test_orbit(新), test_experiments(新)`；
- [ ] README.md：新增"30 秒启动"章节 + SGP4 用法 + 实验功能截图位；
- [ ] GUIDE.md：更新启动方式（推荐 bat）、免 Token 说明；
- [ ] 记忆文件更新；向用户输出分阶段完成报告（含遗留问题清单）。

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| Cesium 1.141 Viewer 构造参数随版本变化（baseLayer vs imageryProvider） | 实现时先在浏览器控制台验证两种构造；保留 token 分支原样，回退安全 |
| V4 `test_orbit.py` 依赖 V4 专属模块 | 复制后在 **V3 副本** 裁剪依赖（不动 V4 原件）；O6/O7 若依赖 twin 模块则降级为实现等价断言 |
| experiments 与主仿真争 CPU | 实验引擎 tick 间 `await asyncio.sleep(0)`；实验规模小（单链/三跳），秒级完成 |
| bat 在含空格/中文路径下的引号问题 | 全程加引号 + `%~dp0`；实测中文路径"竞赛材料"同级的中文目录名 |
| 后端转发白名单漏掉 `experiment_update` | 先读 core.py 确认转发策略再定改法（调研显示是透明广播，预计零改动） |

## 里程碑

| 阶段 | 交付物 | 验收命令 |
|---|---|---|
| A | 启动星桥.bat / 停止星桥.bat / start.sh / requirements-runtime.txt / 免Token前端 | 双击 bat 全新环境出地球 |
| B | ephemeris.py 等 5 文件 + 核心接入 + test_orbit.py | `python tests/test_orbit.py`；全量回归 |
| C | experiments.py + experiment.js + 面板 + 指导书×4 + test_experiments.py | 前端四实验全"通过" |
| D | 文档更新 + 完成报告 | 全量测试逐项通过 |
