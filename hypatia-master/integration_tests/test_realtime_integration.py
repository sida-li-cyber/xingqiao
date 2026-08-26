"""
End-to-end integration test for the Hypatia realtime visualization pipeline.

Tests:
  1. Backend server startup and health check
  2. Core connection and simulation_init broadcast
  3. Real-time state_update push from core to all clients
  4. Client command forwarding to core (play, pause, speed, timeline, etc.)
  5. Multiple client support
  6. Connection/disconnection handling

Usage:
    # Start the backend first:
    python -m realtime_backend.run --port 8000 &

    # Then run this test:
    python integration_tests/test_realtime_integration.py
"""

import asyncio
import json
import sys
import time
import os
import signal
import subprocess
from datetime import datetime

# Add paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'realtime_backend'))

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

BACKEND_HOST = "localhost"
BACKEND_PORT = 8000
BACKEND_URI = f"ws://{BACKEND_HOST}:{BACKEND_PORT}"

# Test results tracking
results = {"passed": 0, "failed": 0, "errors": []}


def record_result(test_name, success, detail=""):
    if success:
        results["passed"] += 1
        print(f"  PASS: {test_name}")
    else:
        results["failed"] += 1
        results["errors"].append(f"{test_name}: {detail}")
        print(f"  FAIL: {test_name} - {detail}")


async def test_health_check():
    """Test 1: Backend health endpoint"""
    print("\n[Test 1] Health check endpoint")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{BACKEND_HOST}:{BACKEND_PORT}/health") as resp:
                data = await resp.json()
                success = (resp.status == 200 and data["status"] == "ok")
                record_result("Health check returns ok", success, str(data))
                return success
    except ImportError:
        print("  SKIP: aiohttp not installed")
        return True
    except Exception as e:
        record_result("Health check endpoint", False, str(e))
        return False


async def test_status_endpoint():
    """Test 2: Backend status endpoint"""
    print("\n[Test 2] Status endpoint")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{BACKEND_HOST}:{BACKEND_PORT}/status") as resp:
                data = await resp.json()
                success = (resp.status == 200 and data["status"] == "running")
                record_result("Status returns running", success, str(data))
                return success
    except ImportError:
        print("  SKIP: aiohttp not installed")
        return True
    except Exception as e:
        record_result("Status endpoint", False, str(e))
        return False


async def test_core_connection_and_init():
    """Test 3: Core connects and sends simulation_init, client receives it"""
    print("\n[Test 3] Core connection and simulation_init broadcast")
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/core") as core_ws:
            # Connect a client
            async with websockets.connect(f"{BACKEND_URI}/ws/client") as client_ws:
                # Core sends simulation_init
                init_msg = {
                    "message_type": "simulation_init",
                    "payload": {
                        "satellites": ["Sat-0-0", "Sat-0-1", "Sat-1-0", "Sat-1-1"],
                        "ground_stations": {
                            "Beijing": {"lat": 39.9, "lon": 116.4, "alt": 0.0, "name": "Beijing"},
                            "New York": {"lat": 40.7, "lon": -74.0, "alt": 0.0, "name": "New York"},
                        },
                        "duration": 600.0,
                    },
                }
                await core_ws.send(json.dumps(init_msg))

                # Client should receive the simulation_init
                try:
                    msg = await asyncio.wait_for(client_ws.recv(), timeout=3.0)
                    data = json.loads(msg)
                    success = (
                        data["message_type"] == "simulation_init"
                        and len(data["payload"]["satellites"]) == 4
                        and len(data["payload"]["ground_stations"]) == 2
                        and data["payload"]["duration"] == 600.0
                    )
                    record_result("simulation_init broadcast to client", success, str(data)[:200])
                except asyncio.TimeoutError:
                    record_result("simulation_init broadcast", False, "Timeout waiting for init message")

                await core_ws.close()
                await client_ws.close()
    except Exception as e:
        record_result("Core connection and init", False, str(e))


async def test_state_update_push():
    """Test 4: Core sends state_update, client receives it"""
    print("\n[Test 4] Real-time state_update push")
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/core") as core_ws:
            async with websockets.connect(f"{BACKEND_URI}/ws/client") as client_ws:
                # Skip simulation_init
                init_msg = {"message_type": "simulation_init", "payload": {"satellites": [], "ground_stations": {}, "duration": 100}}
                await core_ws.send(json.dumps(init_msg))
                await asyncio.wait_for(client_ws.recv(), timeout=2.0)

                # Now send state_update
                state_msg = {
                    "message_type": "state_update",
                    "payload": {
                        "satellite_positions": {
                            "Sat-0-0": {"lat": 45.0, "lon": 120.0, "alt": 550000.0},
                            "Sat-0-1": {"lat": 46.0, "lon": 121.0, "alt": 550000.0},
                        },
                        "ground_stations": {
                            "Beijing": {"lat": 39.9, "lon": 116.4, "alt": 0.0},
                        },
                        "link_status": {
                            "Sat-0-0-Sat-0-1": {
                                "is_active": True,
                                "bandwidth_utilization": 0.65,
                                "latency": 15.2,
                                "loss_rate": 0.001,
                            }
                        },
                        "routing": {"highlight_path": ["Sat-0-0", "Sat-0-1"]},
                        "bandwidth_utilization": {"Sat-0-0-Sat-0-1": 0.65},
                        "timestamp": 123.45,
                    },
                }
                await core_ws.send(json.dumps(state_msg))

                # Client should receive state_update
                try:
                    msg = await asyncio.wait_for(client_ws.recv(), timeout=3.0)
                    data = json.loads(msg)
                    success = (
                        data["message_type"] == "state_update"
                        and len(data["payload"]["satellite_positions"]) == 2
                        and data["payload"]["timestamp"] == 123.45
                    )
                    record_result("state_update push to client", success, str(data)[:200])
                except asyncio.TimeoutError:
                    record_result("state_update push", False, "Timeout waiting for state update")

                await core_ws.close()
                await client_ws.close()
    except Exception as e:
        record_result("State update push", False, str(e))


async def test_multiple_state_updates():
    """Test 5: Multiple consecutive state updates arrive in order"""
    print("\n[Test 5] Multiple state updates arrive in order")
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/core") as core_ws:
            async with websockets.connect(f"{BACKEND_URI}/ws/client") as client_ws:
                # Skip init
                init_msg = {"message_type": "simulation_init", "payload": {"satellites": [], "ground_stations": {}, "duration": 100}}
                await core_ws.send(json.dumps(init_msg))
                await asyncio.wait_for(client_ws.recv(), timeout=2.0)

                # Send 5 state updates
                timestamps = []
                for i in range(5):
                    state_msg = {
                        "message_type": "state_update",
                        "payload": {
                            "satellite_positions": {f"Sat-{i}": {"lat": 45.0 + i, "lon": 120.0 + i, "alt": 550000}},
                            "timestamp": float(i * 10),
                        },
                    }
                    await core_ws.send(json.dumps(state_msg))
                    # Receive each update
                    try:
                        msg = await asyncio.wait_for(client_ws.recv(), timeout=3.0)
                        data = json.loads(msg)
                        timestamps.append(data["payload"]["timestamp"])
                    except asyncio.TimeoutError:
                        break

                success = len(timestamps) == 5 and timestamps == [0.0, 10.0, 20.0, 30.0, 40.0]
                record_result(
                    "Multiple state updates in order",
                    success,
                    f"Received {len(timestamps)}/5 updates, timestamps: {timestamps}",
                )

                await core_ws.close()
                await client_ws.close()
    except Exception as e:
        record_result("Multiple state updates", False, str(e))


async def test_command_forwarding():
    """Test 6: Client sends command, backend forwards to core"""
    print("\n[Test 6] Command forwarding (client -> backend -> core)")
    test_results = []

    # Test each command type
    commands = [
        {"action": "play", "params": None},
        {"action": "pause", "params": None},
        {"action": "stop", "params": None},
        {"action": "reset", "params": None},
        {"action": "speed", "params": {"multiplier": 2.0}},
        {"action": "timeline", "params": {"timestamp": 300}},
        {"action": "metrics", "params": {"type": "bandwidth"}},
        {"action": "filter", "params": {"satellites": ["Sat-0-0"], "stations": ["Beijing"]}},
    ]

    for cmd in commands:
        try:
            async with websockets.connect(f"{BACKEND_URI}/ws/core") as core_ws:
                async with websockets.connect(f"{BACKEND_URI}/ws/client") as client_ws:
                    # Skip init
                    init_msg = {"message_type": "simulation_init", "payload": {"satellites": [], "ground_stations": {}, "duration": 100}}
                    await core_ws.send(json.dumps(init_msg))
                    await asyncio.wait_for(client_ws.recv(), timeout=2.0)

                    # Client sends command
                    command_msg = {
                        "message_type": "command",
                        "payload": cmd,
                    }
                    await client_ws.send(json.dumps(command_msg))

                    # Client should receive ACK
                    try:
                        ack = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
                        ack_data = json.loads(ack)
                        client_got_ack = ack_data["message_type"] == "ack"
                    except asyncio.TimeoutError:
                        client_got_ack = False

                    # Core should receive the forwarded command
                    try:
                        fwd = await asyncio.wait_for(core_ws.recv(), timeout=2.0)
                        fwd_data = json.loads(fwd)
                        core_got_cmd = (
                            fwd_data["message_type"] == "command"
                            and fwd_data["payload"]["action"] == cmd["action"]
                        )
                    except asyncio.TimeoutError:
                        core_got_cmd = False

                    success = client_got_ack and core_got_cmd
                    test_results.append(success)
                    record_result(
                        f"Command '{cmd['action']}' forwarded",
                        success,
                        f"ACK={client_got_ack}, Core received={core_got_cmd}",
                    )

                    await core_ws.close()
                    await client_ws.close()
        except Exception as e:
            record_result(f"Command '{cmd['action']}'", False, str(e))
            test_results.append(False)

    return all(test_results)


async def test_multiple_clients():
    """Test 7: Multiple clients all receive broadcasts"""
    print("\n[Test 7] Multiple clients receive broadcasts")
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/core") as core_ws:
            async with websockets.connect(f"{BACKEND_URI}/ws/client") as client1:
                async with websockets.connect(f"{BACKEND_URI}/ws/client") as client2:
                    # Send init
                    init_msg = {"message_type": "simulation_init", "payload": {"satellites": ["S1"], "ground_stations": {}, "duration": 10}}
                    await core_ws.send(json.dumps(init_msg))

                    # Both clients should receive init
                    results_clients = []
                    for label, ws in [("Client1", client1), ("Client2", client2)]:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                            data = json.loads(msg)
                            results_clients.append(data["message_type"] == "simulation_init")
                        except asyncio.TimeoutError:
                            results_clients.append(False)

                    success = all(results_clients)
                    record_result("Both clients receive broadcasts", success, f"Results: {results_clients}")

                    await core_ws.close()
                    await client1.close()
                    await client2.close()
    except Exception as e:
        record_result("Multiple clients", False, str(e))


async def test_no_core_error():
    """Test 8: Client gets error when no core connected"""
    print("\n[Test 8] Error when no core connected")
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/client") as client_ws:
            # Send command without core connected
            command_msg = {
                "message_type": "command",
                "payload": {"action": "play", "params": None},
            }
            await client_ws.send(json.dumps(command_msg))

            try:
                msg = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
                data = json.loads(msg)
                success = data["message_type"] == "error" and "no_core" in str(data.get("payload", {}))
                record_result("Error on no-core command", success, str(data)[:200])
            except asyncio.TimeoutError:
                record_result("Error on no-core command", False, "No response received")

            await client_ws.close()
    except Exception as e:
        record_result("No-core error", False, str(e))


async def test_demo_sim_core_compatibility():
    """Test 9: Verify demo_sim_core.py module loads correctly"""
    print("\n[Test 9] Demo simulation core compatibility")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "demo_sim_core",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "satviz", "demo_sim_core.py"
            )
        )
        module = importlib.util.module_from_spec(spec)

        # Check all required classes and functions exist
        spec.loader.exec_module(module)
        checks = [
            hasattr(module, "Satellite"),
            hasattr(module, "create_starlink_constellation"),
            hasattr(module, "create_ground_stations"),
            hasattr(module, "compute_links"),
            hasattr(module, "DemoSimCore"),
        ]
        success = all(checks)
        record_result("demo_sim_core.py loads correctly", success, f"Checks: {checks}")
    except Exception as e:
        record_result("demo_sim_core compatibility", False, str(e))


async def test_frontend_files_exist():
    """Test 10: Verify all frontend files exist"""
    print("\n[Test 10] Frontend files integrity")
    satviz_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "satviz")
    required_files = [
        "static_html/index.html",
        "js/app.js",
        "js/cesium-manager.js",
        "js/ui-controller.js",
        "js/websocket.js",
        "js/config.js",
        "demo_sim_core.py",
    ]
    for f in required_files:
        path = os.path.join(satviz_dir, f)
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        record_result(f"File exists: {f}", exists and size > 0, f"path={path}, size={size}")


async def main():
    print("=" * 60)
    print("  Hypatia Realtime Integration Test Suite")
    print("=" * 60)
    print(f"  Backend: {BACKEND_URI}")
    print(f"  Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Check backend is running
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/core") as ws:
            await ws.close()
        print("\nBackend is running. Starting tests...")
    except Exception as e:
        print(f"\nERROR: Cannot connect to backend at {BACKEND_URI}")
        print("Please start the backend first:")
        print("  cd /path/to/hypatia-master")
        print("  PYTHONPATH=/path/to/hypatia-master:/path/to/realtime_backend python -m realtime_backend.run --port 8000")
        print(f"\nDetails: {e}")
        sys.exit(1)

    # 防污染护栏：本套件会接入 mock 核心并重发 simulation_init，
    # 若后端已挂真实核心（客户端连接会收到 init 重放），运行将
    # 覆盖所有在线客户端的场景与后端的 init 缓存。检测到即中止。
    try:
        async with websockets.connect(f"{BACKEND_URI}/ws/client") as ws:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                if msg.get("message_type") == "simulation_init":
                    print("\nERROR: A live simulation core is already attached to this backend.")
                    print("Running this v2 suite would overwrite its simulation_init for all")
                    print("connected clients. Stop the demo core first, or point the suite at")
                    print("a dedicated backend instance.")
                    sys.exit(2)
            except asyncio.TimeoutError:
                pass  # 无 init 重放 = 无真实核心，安全
    except Exception:
        pass

    # Run all tests
    await test_frontend_files_exist()
    await test_health_check()
    await test_status_endpoint()
    await test_core_connection_and_init()
    await test_state_update_push()
    await test_multiple_state_updates()
    await test_command_forwarding()
    await test_multiple_clients()
    await test_no_core_error()
    await test_demo_sim_core_compatibility()

    # Summary
    print("\n" + "=" * 60)
    print("  Test Results Summary")
    print("=" * 60)
    total = results["passed"] + results["failed"]
    print(f"  Total: {total}")
    print(f"  Passed: {results['passed']}")
    print(f"  Failed: {results['failed']}")
    if results["errors"]:
        print("\n  Errors:")
        for err in results["errors"]:
            print(f"    - {err}")
    print("=" * 60)

    return results["failed"] == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
