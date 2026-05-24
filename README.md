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

## License

The Hypatia components (satgenpy, satviz, realtime_backend) are licensed under MIT. ns3-sat-sim is licensed under GNU GPLv2. See each subdirectory for details.
