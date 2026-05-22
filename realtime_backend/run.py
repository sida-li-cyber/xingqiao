from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .config import load_settings

logger = logging.getLogger(__name__)


def main() -> None:
    """主函数，用于启动后端服务"""
    parser = argparse.ArgumentParser(
        description="Realtime Simulation Backend Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m realtime_backend.run --port 8000
  python -m realtime_backend.run --host 127.0.0.1 --port 9000 --log-level debug
        """,
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server to (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["critical", "error", "warning", "info", "debug"],
        help="Log level (default: info)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on file changes (for development)",
    )

    args = parser.parse_args()

    # 加载配置
    settings = load_settings()

    # 命令行参数覆盖配置
    settings.host = args.host
    settings.port = args.port
    settings.log_level = args.log_level
    settings.reload = args.reload

    print("\n" + "=" * 50)
    print("Realtime Simulation Backend")
    print("=" * 50)
    print(f"Server: {settings.host}:{settings.port}")
    print(f"Log Level: {settings.log_level}")
    print(f"Auto-reload: {settings.reload}")
    print(f"Client WebSocket: ws://{settings.host}:{settings.port}{settings.client_ws_path}")
    print(f"Core WebSocket: ws://{settings.host}:{settings.port}{settings.core_ws_path}")
    print(f"Documentation: http://{settings.host}:{settings.port}/docs")
    print("=" * 50 + "\n")

    # 启动服务器
    uvicorn.run(
        "realtime_backend.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=settings.reload,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nServer stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
