"""
仿真实时后端 - 演示客户端

这个脚本演示如何连接到实时后端服务并进行通信。
包含两个示例：
1. 前端客户端（接收状态更新）
2. 仿真核心（发送状态更新）
"""

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Optional

try:
    import websockets
except ImportError:
    print("Error: websockets library not installed")
    print("Install it with: pip install websockets")
    exit(1)


async def test_client(uri: str = "ws://localhost:8000/ws/client"):
    """
    前端客户端示例
    连接到后端并发送命令，接收状态更新
    """
    print("[Client] Connecting to backend...")
    try:
        async with websockets.connect(uri) as websocket:
            print("[Client] Connected successfully!")

            # 发送一个命令
            command = {
                "message_type": "command",
                "payload": {"action": "speed", "params": {"speed_factor": 2.0}},
            }

            print(f"[Client] Sending command: {json.dumps(command, indent=2)}")
            await websocket.send(json.dumps(command))

            # 接收确认
            ack = await websocket.recv()
            print(f"[Client] Received ACK: {json.dumps(json.loads(ack), indent=2)}")

            # 等待状态更新
            print("[Client] Waiting for state updates...")
            for i in range(3):
                try:
                    state = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(state)
                    print(f"[Client] Received state update #{i+1}:")
                    print(
                        f"         Timestamp: {data['payload'].get('timestamp', 'N/A')}"
                    )
                    print(
                        f"         Satellites: {len(data['payload'].get('satellite_positions', {}))}"
                    )
                except asyncio.TimeoutError:
                    print("[Client] No state update received (timeout)")

    except ConnectionRefusedError:
        print("[Client] Error: Connection refused. Is the server running?")
    except Exception as e:
        print(f"[Client] Error: {e}")


async def test_core(uri: str = "ws://localhost:8000/ws/core"):
    """
    仿真核心示例
    连接到后端并发送模拟状态数据
    """
    print("[Core] Connecting to backend...")
    try:
        async with websockets.connect(uri) as websocket:
            print("[Core] Connected successfully!")

            # 发送模拟状态数据
            for i in range(3):
                state = {
                    "message_type": "state_update",
                    "payload": {
                        "satellite_positions": {
                            "sat_1": {
                                "lat": 45.0 + i,
                                "lon": 120.0 + i,
                                "alt": 500000,
                            },
                            "sat_2": {
                                "lat": -30.0 + i,
                                "lon": 60.0 + i,
                                "alt": 500000,
                            },
                        },
                        "link_status": {
                            "link_1": {"status": "active", "capacity": 1000},
                            "link_2": {"status": "active", "capacity": 800},
                        },
                        "routing": {
                            "route_1": ["sat_1", "sat_2", "ground_1"],
                            "route_2": ["sat_2", "sat_1", "ground_2"],
                        },
                        "bandwidth_utilization": {
                            "sat_1": 0.5 + random.random() * 0.3,
                            "sat_2": 0.4 + random.random() * 0.3,
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }

                print(f"[Core] Sending state update #{i+1}...")
                await websocket.send(json.dumps(state))

                # 接收命令
                try:
                    command = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(command)
                    print(f"[Core] Received command: {data['payload'].get('action')}")
                except asyncio.TimeoutError:
                    print("[Core] Waiting for commands...")

                await asyncio.sleep(1)

    except ConnectionRefusedError:
        print("[Core] Error: Connection refused. Is the server running?")
    except Exception as e:
        print(f"[Core] Error: {e}")


async def test_concurrent():
    """
    并发测试：同时运行客户端和核心
    """
    print("\n=== Concurrent Test ===\n")

    async def client_task():
        await asyncio.sleep(0.5)  # 让核心先连接
        await test_client()

    async def core_task():
        await test_core()

    await asyncio.gather(core_task(), client_task(), return_exceptions=True)


def print_usage():
    """打印使用说明"""
    print(
        """
=== Realtime Backend Demo Client ===

Usage:
    python demo.py [command]

Commands:
    client          Test frontend client (default)
    core            Test simulation core
    concurrent      Test client and core together
    health          Check backend health
    status          Get backend status

Examples:
    python demo.py client
    python demo.py core
    python demo.py concurrent
    python demo.py health

Make sure the backend is running:
    python -m realtime_backend.run
    """
    )


async def check_health(host: str = "localhost", port: int = 8000):
    """检查后端健康状态"""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{host}:{port}/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print("\n✓ Backend Health Check:")
                    print(f"  Status: {data['status']}")
                    print(f"  Clients connected: {data['clients_connected']}")
                    print(f"  Cores connected: {data['cores_connected']}\n")
                else:
                    print(f"✗ Backend returned status {resp.status}\n")
    except ImportError:
        print("aiohttp library not installed. Skipping health check.")
        print("Install it with: pip install aiohttp\n")
    except Exception as e:
        print(f"✗ Cannot connect to backend: {e}\n")
        print("Make sure the backend is running:")
        print("  python -m realtime_backend.run\n")


async def get_status(host: str = "localhost", port: int = 8000):
    """获取后端状态"""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{host}:{port}/status") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print("\n✓ Backend Status:")
                    print(json.dumps(data, indent=2))
                    print()
                else:
                    print(f"✗ Backend returned status {resp.status}\n")
    except ImportError:
        print("aiohttp library not installed. Skipping status check.")
        print("Install it with: pip install aiohttp\n")
    except Exception as e:
        print(f"✗ Cannot connect to backend: {e}\n")
        print("Make sure the backend is running:")
        print("  python -m realtime_backend.run\n")


async def main():
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command == "client":
            await test_client()
        elif command == "core":
            await test_core()
        elif command == "concurrent":
            await test_concurrent()
        elif command == "health":
            await check_health()
        elif command == "status":
            await get_status()
        else:
            print_usage()
    else:
        # 默认运行客户端测试
        print("\n=== Testing Frontend Client ===\n")
        await check_health()
        await test_client()


if __name__ == "__main__":
    asyncio.run(main())
