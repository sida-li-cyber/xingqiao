# Hypatia — LEO Satellite Network Simulation (Enhanced)

This repository is an enhanced fork of the [Hypatia](https://github.com/snkas/hypatia) LEO satellite network simulation framework, adding real-time interactive 3D visualization via WebSocket streaming and CesiumJS.

## Project Structure

| Directory | Description |
|---|---|
| [hypatia-master/](hypatia-master/) | Main simulation framework (satgenpy, ns3-sat-sim, satviz, paper, integration_tests) |
| [realtime_backend/](realtime_backend/) | FastAPI + WebSocket relay server for real-time visualization |

### Unrelated Directories

| Directory | Description |
|---|---|
| `video_processor/` | Standalone C++ video processing library (FFmpeg-based) — not part of Hypatia |
| `andrej-karpathy-skills-main/` | Third-party Claude Code behavioral guidelines plugin — not part of Hypatia |

## Documentation

- **[GUIDE.md](GUIDE.md)** — 完整用户手册（功能说明、架构、下载、安装、运行、依赖项、故障排除）
- [hypatia-master/README.md](hypatia-master/README.md) — 项目主 README
- [realtime_backend/README.md](realtime_backend/README.md) — 后端 API 文档
- [realtime_backend/QUICKSTART.md](realtime_backend/QUICKSTART.md) — 后端快速入门（中文）
- [hypatia-master/satviz/FRONTEND_README.md](hypatia-master/satviz/FRONTEND_README.md) — 前端详细文档（中文）
- [docs/phase6-validation.md](docs/phase6-validation.md) — 阶段 6 性能 / 正确性校验报告（对账理论值、压测、断线重连）
- [docs/protocol-v3.2-file-transfer.md](docs/protocol-v3.2-file-transfer.md) — 文件传输协议规范 v3.2（HTTP 数据面 + WS 命令 + `file_transfers` 遥测）
- [docs/file-transfer-design.md](docs/file-transfer-design.md) — 自定义文件传输与实时追踪设计文档（控制面 / 数据面分离）
- [docs/experiments/](docs/experiments/) — **教学实验指导书 E1–E4**（时延分解 / M/D/1 / 切换丢包 / QoS，含教师速查表）
- [docs/education-upgrade-plan.md](docs/education-upgrade-plan.md) — 教育化升级计划（一键启动 / 教学实验 / SGP4 真实轨道）
- [docs/education-assessment-plan.md](docs/education-assessment-plan.md) — 教学平台改进计划（评分闭环 / 身份存档教师端 / ilab-x 国标对接 / E5–E7 新实验，含执行状态与验收记录）
- [ROADMAP.md](ROADMAP.md) — 系统完善路线图（六阶段，已全部完成）

## Quick Start (Windows)

双击 `start_starbridge.bat` — 自动装依赖并拉起 后端 + 仿真核心 + 前端，
浏览器打开 <http://127.0.0.1:8080/static_html/index.html>（免 Cesium Token）。
停止用 `stop_starbridge.bat`。详细步骤见 [GUIDE.md §6](GUIDE.md)。

## Testing / Validation

所有测试位于 `hypatia-master/satviz/`，固定随机种子、单命令可复现：

```bash
cd hypatia-master/satviz
python test_packet_sim.py            # DES 单元校验：轻载时延对账、拥塞排队/丢包
python test_phase3.py                # 切换丢包尖峰对账、QoS 严格优先
python test_phase6.py                # 阶段 6：守恒/吞吐/M-D-1 对账 + 长时与背压压测（--fast 跳过两个长时测试）
python test_phase8.py                # 文件传输控制面：分片注入/ARQ重传/多文件QoS + 包守恒（含重传场景）
python test_integration_offline.py   # 全管线离线集成（真实 DemoSimCore，无需后端）
python test_reconnect.py             # 断线重连健壮性（自动起停 backend + sim_core 子进程，约 40s）

cd ../..                             # 回到项目根目录
python tests/test_file_e2e.py        # 文件传输端到端：上传→传输→下载 SHA-256 一致 + 取消（自起 backend+core，端口 8769）
python tests/test_orbit.py           # SGP4 轨道模型 O1–O7（复制自 V4 的模块与 V3 核心集成）
python tests/test_experiments.py     # 教学实验 E1–E7：场景 + 理论对账 + 评分闭环 + 种子参数化（24 项）
python tests/test_edu_api.py         # 教学数据面：登录鉴权/存档/提交批改/成绩册/班级名单/考核（自起 backend，端口 8771）
python -m pytest tests/test_ilabx.py -q   # ilab-x 国标适配层：getinfo / score_upload / 自检模式（6 项）
python tests/test_milestone_c.py     # 全终端文件传输集成（需运行中的栈：backend :8000 + core --scale 1584）
python wsdiag/e2e_experiment.py      # 实验 WS 全链路：E1–E7 + 并发排队（需运行中的栈：backend :8000 + core）

# 或一次跑全部 pytest 单元/对账测试（无需起栈，61 项）：
python -m pytest tests/ -q --ignore=tests/test_milestone_c.py
```

**文件传输命令行客户端**（无需前端即可上传 / 传输 / 下载校验）：

```bash
python tools/file_transfer_client.py <file> --src UAV-01 --dst Beijing --rate 5000000 --port 8000
```

## License

The Hypatia components (satgenpy, satviz, realtime_backend) are licensed under MIT. ns3-sat-sim is licensed under GNU GPLv2. See each subdirectory for details.
