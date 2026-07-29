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
- [ROADMAP.md](ROADMAP.md) — 系统完善路线图（六阶段，已全部完成）

## Testing / Validation

所有测试位于 `hypatia-master/satviz/`，固定随机种子、单命令可复现：

```bash
cd hypatia-master/satviz
python test_packet_sim.py            # DES 单元校验：轻载时延对账、拥塞排队/丢包
python test_phase3.py                # 切换丢包尖峰对账、QoS 严格优先
python test_phase6.py                # 阶段 6：守恒/吞吐/M-D-1 对账 + 长时与背压压测（--fast 跳过两个长时测试）
python test_integration_offline.py   # 全管线离线集成（真实 DemoSimCore，无需后端）
python test_reconnect.py             # 断线重连健壮性（自动起停 backend + sim_core 子进程，约 40s）
```

## License

The Hypatia components (satgenpy, satviz, realtime_backend) are licensed under MIT. ns3-sat-sim is licensed under GNU GPLv2. See each subdirectory for details.
