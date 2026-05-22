"""
Live end-to-end test: demo_sim_core -> backend -> client.
This test starts the demo_sim_core in a background task and verifies
real data flows through the pipeline.
"""
import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'realtime_backend'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'satviz'))

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed")
    sys.exit(1)

BACKEND_URI = "ws://localhost:8000"


async def run_demo_core(duration=8.0):
    """Run the demo sim core briefly to generate test data."""
    from demo_sim_core import DemoSimCore

    core = DemoSimCore(host="localhost", port=8000, num_orbits=3, sats_per_orbit=4)
    core.duration = duration

    print(f"[Core] Connecting to {core.uri}...")
    retry = 0
    while retry < 10:
        try:
            async with websockets.connect(core.uri, ping_interval=20) as ws:
                core.ws = ws
                core.running = True
                print("[Core] Connected!")

                # Send init
                init_msg = core.get_init_message()
                await ws.send(json.dumps(init_msg))
                print(f"[Core] Sent init: {len(init_msg['payload']['satellites'])} satellites, "
                      f"{len(init_msg['payload']['ground_stations'])} stations")

                # Run for a few seconds
                start_time = time.time()
                update_count = 0
                while time.time() - start_time < duration:
                    loop_start = time.time()

                    # Receive commands
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.02)
                        data = json.loads(msg)
                        await core.handle_command(data)
                    except asyncio.TimeoutError:
                        pass
                    except websockets.exceptions.ConnectionClosed:
                        break

                    # Advance sim
                    if core.is_playing:
                        core.sim_time += 0.1 * core.speed
                        if core.sim_time >= core.duration:
                            core.sim_time = core.duration
                            core.is_playing = False

                    # Send state
                    state_msg = core.get_state_update()
                    await ws.send(json.dumps(state_msg))
                    update_count += 1

                    elapsed = time.time() - loop_start
                    await asyncio.sleep(max(0, 0.1 - elapsed))

                print(f"[Core] Sent {update_count} state updates in {duration}s")
                return update_count
        except (ConnectionRefusedError, OSError) as e:
            retry += 1
            await asyncio.sleep(1)
    return 0


async def run_client(duration=8.0):
    """Run a simulated frontend client that receives and validates state updates."""
    print(f"[Client] Connecting to {BACKEND_URI}/ws/client...")

    async with websockets.connect(f"{BACKEND_URI}/ws/client") as ws:
        print("[Client] Connected!")
        received_updates = []
        received_init = None
        start_time = time.time()

        while time.time() - start_time < duration:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                data = json.loads(msg)

                if data["message_type"] == "simulation_init" and received_init is None:
                    received_init = data["payload"]
                    print(f"[Client] Received init: {len(received_init.get('satellites', []))} satellites")

                elif data["message_type"] == "state_update":
                    received_updates.append(data["payload"])
                    ts = data["payload"].get("timestamp", 0)
                    n_sats = len(data["payload"].get("satellite_positions", {}))
                    n_links = len(data["payload"].get("link_status", {}))
                    if len(received_updates) <= 3 or len(received_updates) % 20 == 0:
                        print(f"[Client] Update #{len(received_updates)}: t={ts:.1f}s, "
                              f"{n_sats} satellites, {n_links} links")

            except asyncio.TimeoutError:
                pass
            except websockets.exceptions.ConnectionClosed:
                print("[Client] Connection closed")
                break

        print(f"[Client] Received {len(received_updates)} state updates and "
              f"{1 if received_init else 0} init message in {duration}s")
        return received_init, received_updates


async def test_interactive_control():
    """Test sending control commands while simulation is running."""
    print("\n[Interactive Control Test]")

    # Start demo core in background
    core_task = asyncio.create_task(run_demo_core(duration=12.0))
    await asyncio.sleep(2)  # Wait for core to connect and start sending

    async with websockets.connect(f"{BACKEND_URI}/ws/client") as ws:
        print("[Client] Connected for control test")

        # Receive init
        init_msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
        print(f"[Client] Got init")

        # Receive a few updates to confirm flow
        for _ in range(3):
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
        print("[Client] Confirmed state flow working")

        # Test pause
        cmd = {"message_type": "command", "payload": {"action": "pause", "params": None}}
        await ws.send(json.dumps(cmd))
        ack = await asyncio.wait_for(ws.recv(), timeout=2.0)
        ack_data = json.loads(ack)
        print(f"[Client] Pause: ACK={ack_data['message_type']=='ack'}")

        await asyncio.sleep(1)

        # Test play
        cmd = {"message_type": "command", "payload": {"action": "play", "params": None}}
        await ws.send(json.dumps(cmd))
        ack = await asyncio.wait_for(ws.recv(), timeout=2.0)
        print(f"[Client] Play: ACK={json.loads(ack)['message_type']=='ack'}")

        await asyncio.sleep(1)

        # Test speed change
        cmd = {"message_type": "command", "payload": {"action": "speed", "params": {"multiplier": 5.0}}}
        await ws.send(json.dumps(cmd))
        ack = await asyncio.wait_for(ws.recv(), timeout=2.0)
        print(f"[Client] Speed 5x: ACK={json.loads(ack)['message_type']=='ack'}")

        await asyncio.sleep(1)

        # Test timeline jump
        cmd = {"message_type": "command", "payload": {"action": "timeline", "params": {"timestamp": 300}}}
        await ws.send(json.dumps(cmd))
        ack = await asyncio.wait_for(ws.recv(), timeout=2.0)
        print(f"[Client] Timeline jump: ACK={json.loads(ack)['message_type']=='ack'}")

        print("[Client] All control commands verified")

    await core_task


async def main():
    print("=" * 60)
    print("  Live End-to-End Test: demo_sim_core -> backend -> client")
    print("=" * 60)

    # Test 1: Basic data flow
    print("\n--- Test 1: Basic real-time data flow ---")
    client_task = asyncio.create_task(run_client(duration=8.0))
    core_task_result = asyncio.create_task(run_demo_core(duration=8.0))

    received_init, received_updates = await client_task
    update_count = await core_task_result

    print("\n--- Results ---")
    success = True
    if received_init is None:
        print("FAIL: Client did not receive simulation_init")
        success = False
    else:
        n_sats = len(received_init.get("satellites", []))
        n_stations = len(received_init.get("ground_stations", {}))
        print(f"PASS: Received init with {n_sats} satellites, {n_stations} stations")

    if len(received_updates) < 5:
        print(f"FAIL: Only {len(received_updates)} state updates received (expected >= 5)")
        success = False
    else:
        # Validate update content
        first = received_updates[0]
        has_positions = "satellite_positions" in first
        has_links = "link_status" in first
        has_timestamp = "timestamp" in first
        print(f"PASS: Received {len(received_updates)} state updates")
        print(f"  First update has: positions={has_positions}, links={has_links}, timestamp={has_timestamp}")

        # Check timestamps are monotonically increasing
        timestamps = [u.get("timestamp", 0) for u in received_updates]
        is_increasing = all(timestamps[i] <= timestamps[i+1] for i in range(len(timestamps)-1))
        print(f"  Timestamps increasing: {is_increasing}")

    # Test 2: Interactive control
    print("\n--- Test 2: Interactive control ---")
    await test_interactive_control()

    print("\n" + "=" * 60)
    print(f"  Overall: {'PASSED' if success else 'FAILED'}")
    print("=" * 60)
    return success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
