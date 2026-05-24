"""
Integration test for multi-constellation switching feature.

Tests:
  1. Standalone: constellation creation, scaling, link computation
  2. Integration: WebSocket protocol — init, switch_constellation, shell switching,
     rapid successive switches, and reconnection behaviour

Usage:
    python hypatia-master/satviz/test_constellation.py
"""

import asyncio
import json
import math
import subprocess
import sys
import time

# ---------- Import the module under test ----------
sys.path.insert(0, "hypatia-master/satviz")
import demo_sim_core as dsc


# ============================================================
# PART 1 — Standalone logic tests
# ============================================================

def test_constellation_definitions():
    """Verify all three constellations and their shells are defined correctly."""
    print("1.1 Constellation definitions ... ", end="")
    assert "Starlink" in dsc._CONSTELLATION_FULL
    assert "Kuiper" in dsc._CONSTELLATION_FULL
    assert "Telesat" in dsc._CONSTELLATION_FULL
    assert len(dsc._CONSTELLATION_FULL["Starlink"]) == 5
    assert len(dsc._CONSTELLATION_FULL["Kuiper"]) == 3
    assert len(dsc._CONSTELLATION_FULL["Telesat"]) == 2

    # Verify each shell has required keys
    for name, shells in dsc._CONSTELLATION_FULL.items():
        for i, cfg in enumerate(shells):
            for k in ("orbits", "sats_per_orbit", "inclination", "altitude_km"):
                assert k in cfg, f"{name} shell {i} missing key {k}"
    print("OK")


def test_scaling():
    """Verify scaling produces 50-150 satellites per shell."""
    print("1.2 Scaling ... ", end="")
    for name, shells in dsc._CONSTELLATION_FULL.items():
        for i, cfg in enumerate(shells):
            o, s = dsc.scale_shell_params(cfg["orbits"], cfg["sats_per_orbit"])
            n = o * s
            assert 50 <= n <= 150, \
                f"{name} shell {i}: {o}x{s}={n} out of [50,150] range"
            assert o >= 1, f"{name} shell {i}: 0 orbits"
            assert s >= 1, f"{name} shell {i}: 0 sats per orbit"
    print("OK")


def test_create_constellation():
    """Verify constellation creation produces correct Satellite objects."""
    print("1.3 create_constellation ... ", end="")
    for name in dsc._CONSTELLATION_FULL:
        for shell in range(len(dsc._CONSTELLATION_FULL[name])):
            sats = dsc.create_constellation(name, shell)
            cfg = dsc._CONSTELLATION_FULL[name][shell]
            o, s = dsc.scale_shell_params(cfg["orbits"], cfg["sats_per_orbit"])
            assert len(sats) == o * s, \
                f"{name} shell {shell}: expected {o*s} sats, got {len(sats)}"

            # Verify satellite IDs and attributes
            for sat in sats:
                assert sat.sat_id.startswith("Sat-"), f"Bad ID: {sat.sat_id}"
                assert sat.altitude_km == cfg["altitude_km"], \
                    f"Wrong altitude for {sat.sat_id}"
                assert sat.inclination_rad == math.radians(cfg["inclination"]), \
                    f"Wrong inclination for {sat.sat_id}"

            # Verify orbit coverage
            orbit_ids = set()
            for sat in sats:
                orbit_ids.add(sat.sat_id.split("-")[1])
            assert len(orbit_ids) == o, \
                f"{name} shell {shell}: expected {o} orbits, got {len(orbit_ids)}"
    print("OK")


def test_out_of_range_shell():
    """Verify out-of-range shell raises ValueError."""
    print("1.4 Out-of-range shell ... ", end="")
    try:
        dsc.create_constellation("Starlink", 5)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    try:
        dsc.create_constellation("Kuiper", -1)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("OK")


def test_compute_links():
    """Verify link computation for all scaled constellations."""
    print("1.5 compute_links ... ", end="")
    gs = dsc.create_ground_stations()
    for name in dsc._CONSTELLATION_FULL:
        for shell in range(len(dsc._CONSTELLATION_FULL[name])):
            sats = dsc.create_constellation(name, shell)
            links = dsc.compute_links(sats, gs)
            assert len(links) > 0, f"{name} shell {shell}: 0 links"

            # All links should be ISL type (no GSL in current compute_links)
            for lid, linfo in links.items():
                assert linfo["type"] == "isl", \
                    f"Unexpected link type: {linfo['type']}"
                assert "source" in linfo
                assert "target" in linfo
    print("OK")


def test_get_init_message():
    """Verify simulation_init includes constellation field."""
    print("1.6 get_init_message ... ", end="")
    core = dsc.DemoSimCore(constellation_name="Kuiper", shell_index=1)
    msg = core.get_init_message()
    assert msg["message_type"] == "simulation_init"
    p = msg["payload"]
    assert "constellation" in p
    assert p["constellation"]["name"] == "Kuiper"
    assert p["constellation"]["current_shell"] == 1
    assert p["constellation"]["shell_count"] == 3
    assert "total_satellites" in p
    assert "total_links" in p
    assert p["total_satellites"] == len(core.satellites)
    assert p["total_links"] == len(core.links)
    assert "satellites" in p
    assert "ground_stations" in p
    assert "duration" in p
    print("OK")


def test_switch_constellation_command():
    """Verify handle_command processes switch_constellation."""
    print("1.7 switch_constellation command ... ", end="")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        core = dsc.DemoSimCore(constellation_name="Starlink", shell_index=0)
        orig_count = len(core.satellites)
        orig_links = len(core.links)

        # Switch to Telesat shell 0
        data = {
            "payload": {
                "action": "switch_constellation",
                "params": {"constellation": "Telesat", "shell": 0},
            }
        }
        await core.handle_command(data)

        assert core.constellation_name == "Telesat"
        assert core.current_shell == 0
        assert core.sim_time == 0.0
        assert len(core.satellites) != orig_count
        assert len(core.links) != orig_links

        # Verify init message reflects new state
        msg = core.get_init_message()
        assert msg["payload"]["constellation"]["name"] == "Telesat"
        assert msg["payload"]["constellation"]["current_shell"] == 0
        print("OK")

    loop.run_until_complete(_run())


def test_clamped_shell_index():
    """Verify shell index is clamped to valid range."""
    print("1.8 Shell index clamping ... ", end="")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        core = dsc.DemoSimCore(constellation_name="Starlink", shell_index=0)
        # Request out-of-range shell — should clamp
        data = {
            "payload": {
                "action": "switch_constellation",
                "params": {"constellation": "Starlink", "shell": 99},
            }
        }
        await core.handle_command(data)
        assert core.current_shell == 4  # max valid = 4 (5 shells total)
        print("OK")

    loop.run_until_complete(_run())


def test_unknown_constellation():
    """Verify unknown constellation is rejected gracefully."""
    print("1.9 Unknown constellation ... ", end="")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        core = dsc.DemoSimCore(constellation_name="Starlink", shell_index=0)
        orig_name = core.constellation_name
        data = {
            "payload": {
                "action": "switch_constellation",
                "params": {"constellation": "BogusConstellation", "shell": 0},
            }
        }
        await core.handle_command(data)
        assert core.constellation_name == orig_name  # unchanged
        print("OK")

    loop.run_until_complete(_run())


# ============================================================
# PART 2 — Integration tests (requires backend running)
# ============================================================

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8765  # use non-standard port to avoid conflicts


class TestClient:
    """Simulates a browser frontend connecting to the backend."""

    def __init__(self):
        self.ws = None
        self.messages = []
        self.init_messages = []
        self.state_updates = []
        self.acks = []
        self.errors = []

    async def connect(self, host=BACKEND_HOST, port=BACKEND_PORT):
        import websockets
        self.ws = await websockets.connect(f"ws://{host}:{port}/ws/client")
        return self

    async def recv_loop(self, duration=2.0):
        """Collect messages for a given duration."""
        import websockets
        deadline = time.time() + duration
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=0.3)
                data = json.loads(msg)
                self.messages.append(data)
                mt = data.get("message_type")
                if mt == "simulation_init":
                    self.init_messages.append(data)
                elif mt == "state_update":
                    self.state_updates.append(data)
                elif mt == "ack":
                    self.acks.append(data)
                elif mt == "error":
                    self.errors.append(data)
            except asyncio.TimeoutError:
                pass
            except websockets.exceptions.ConnectionClosed:
                break

    async def send_command(self, action, params=None):
        msg = {
            "message_type": "command",
            "payload": {"action": action, "params": params or {}},
        }
        await self.ws.send(json.dumps(msg))

    async def disconnect(self):
        if self.ws:
            await self.ws.close()

    def last_init(self):
        return self.init_messages[-1]["payload"] if self.init_messages else None


async def test_integration_initial_load():
    """Connect a client mid-session and force init via switch_constellation.

    In the relay architecture, simulation_init is broadcast only when the
    core sends it.  Since our test client connects after the core, we
    trigger a switch_constellation to force a new init to arrive.
    """
    print("\n2.1 Integration: initial load / constellation field ... ", end="")
    client = TestClient()
    await client.connect()

    # Send switch to force the core to re-send an init
    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 0})
    await client.recv_loop(duration=1.5)
    init = client.last_init()
    assert init is not None, "No simulation_init received after switch"
    assert "constellation" in init, "constellation field missing in init"
    c = init["constellation"]
    assert c["name"] == "Starlink"
    assert "shell_count" in c
    assert c["current_shell"] == 0
    assert "total_satellites" in init
    assert "total_links" in init
    assert len(init["satellites"]) > 0
    assert len(init["ground_stations"]) > 0
    print(f"OK  (name={c['name']}, shell={c['current_shell']}, "
          f"sats={init['total_satellites']}, links={init['total_links']})")
    await client.disconnect()


async def test_integration_switch_constellation():
    """Switch constellation and verify new init arrives with correct data."""
    print("2.2 Integration: switch constellation ... ", end="")
    client = TestClient()
    await client.connect()

    # Force an init first
    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 0})
    await client.recv_loop(duration=1.0)
    init_before = client.last_init()
    assert init_before is not None, "No init after initial switch"

    old_name = init_before["constellation"]["name"]

    # Switch to a different constellation
    new_name = "Kuiper" if old_name == "Starlink" else "Starlink"
    await client.send_command("switch_constellation",
                              {"constellation": new_name, "shell": 0})
    await client.recv_loop(duration=1.0)

    # Should have ack + new init
    assert len(client.acks) > 0, "No ack received"
    assert len(client.init_messages) >= 2, \
        f"Expected >=2 inits, got {len(client.init_messages)}"

    new_init = client.last_init()
    assert new_init["constellation"]["name"] == new_name
    assert new_init["constellation"]["current_shell"] == 0
    assert new_init["total_satellites"] > 0
    print(f"OK  ({old_name} → {new_name}, "
          f"sats={new_init['total_satellites']})")
    await client.disconnect()


async def test_integration_switch_shell():
    """Switch shell within same constellation and verify."""
    print("2.3 Integration: switch shell ... ", end="")
    client = TestClient()
    await client.connect()

    # Force an init from the current constellation
    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 0})
    await client.recv_loop(duration=1.0)
    init_before = client.last_init()
    assert init_before is not None

    name = init_before["constellation"]["name"]

    # Switch to shell 1
    await client.send_command("switch_constellation",
                              {"constellation": name, "shell": 1})
    await client.recv_loop(duration=1.0)

    new_init = client.last_init()
    assert new_init["constellation"]["name"] == name
    assert new_init["constellation"]["current_shell"] == 1
    print(f"OK  ({name} shell 0→1, "
          f"sats={new_init['total_satellites']})")
    await client.disconnect()


async def test_integration_rapid_switches():
    """Send multiple switch commands rapidly and verify final state is correct."""
    print("2.4 Integration: rapid successive switches ... ", end="")
    client = TestClient()
    await client.connect()

    # Force an init first
    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 0})
    await client.recv_loop(duration=0.8)

    # Send three switches back-to-back
    await client.send_command("switch_constellation",
                              {"constellation": "Kuiper", "shell": 0})
    await client.send_command("switch_constellation",
                              {"constellation": "Telesat", "shell": 0})
    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 2})

    await client.recv_loop(duration=2.0)

    # The last init should reflect the final switch
    final_init = client.last_init()
    assert final_init is not None
    assert final_init["constellation"]["name"] == "Starlink"
    assert final_init["constellation"]["current_shell"] == 2

    # Should have at least 3 acks + 1 initial init + 3 switch inits
    assert len(client.init_messages) >= 4, \
        f"Expected >=4 inits, got {len(client.init_messages)}"
    print(f"OK  (final={final_init['constellation']['name']} "
          f"shell {final_init['constellation']['current_shell']}, "
          f"inits={len(client.init_messages)})")
    await client.disconnect()


async def test_integration_reconnect():
    """Disconnect and reconnect — verify init is received after forced switch."""
    print("2.5 Integration: WebSocket reconnect ... ", end="")
    # Connect, get init via switch, disconnect
    client1 = TestClient()
    await client1.connect()
    await client1.send_command("switch_constellation",
                               {"constellation": "Kuiper", "shell": 1})
    await client1.recv_loop(duration=1.0)
    init1 = client1.last_init()
    assert init1 is not None
    assert init1["constellation"]["name"] == "Kuiper"
    await client1.disconnect()

    # Reconnect — no automatic init, use switch to force one
    client2 = TestClient()
    await client2.connect()
    await client2.send_command("switch_constellation",
                               {"constellation": "Starlink", "shell": 0})
    await client2.recv_loop(duration=1.5)

    assert len(client2.init_messages) >= 1, \
        f"Reconnected client should receive init after switch, got {len(client2.init_messages)}"
    init2 = client2.last_init()
    assert "constellation" in init2
    print(f"OK  (reconnect init: {init2['constellation']['name']} "
          f"shell {init2['constellation']['current_shell']})")
    await client2.disconnect()


async def test_integration_state_updates():
    """Verify state_updates arrive after init and contain expected fields."""
    print("2.6 Integration: state updates ... ", end="")
    client = TestClient()
    await client.connect()

    # Force an init with a known constellation
    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 0})
    await client.recv_loop(duration=0.5)

    # Now collect state updates
    client.state_updates.clear()
    await client.recv_loop(duration=1.5)

    assert len(client.state_updates) > 0, "No state_updates received"
    su = client.state_updates[0]["payload"]
    assert "satellite_positions" in su, "Missing satellite_positions"
    assert "link_status" in su, "Missing link_status"
    assert "ground_stations" in su, "Missing ground_stations"
    assert "timestamp" in su, "Missing timestamp"

    # Satellite positions should match the init satellite list
    init = client.last_init()
    sat_ids = set(init["satellites"])
    update_ids = set(su["satellite_positions"].keys())
    assert sat_ids == update_ids, \
        f"Satellite ID mismatch: init has {len(sat_ids)}, update has {len(update_ids)}"

    print(f"OK  ({len(client.state_updates)} updates, "
          f"{len(su['satellite_positions'])} sats, "
          f"{len(su['link_status'])} links)")
    await client.disconnect()


async def test_integration_switch_then_state():
    """Switch constellation then verify subsequent state updates use new IDs."""
    print("2.7 Integration: switch then state ... ", end="")
    client = TestClient()
    await client.connect()

    await client.send_command("switch_constellation",
                              {"constellation": "Starlink", "shell": 0})
    await client.recv_loop(duration=0.8)
    init1 = client.last_init()
    assert init1 is not None

    old_ids = set(init1["satellites"])

    # Switch to Kuiper
    await client.send_command("switch_constellation",
                              {"constellation": "Kuiper", "shell": 1})
    await client.recv_loop(duration=0.5)
    init2 = client.last_init()
    new_ids = set(init2["satellites"])

    # IDs are Sat-{orbit}-{sat} which may overlap between constellations.
    # This is correct behaviour — entities are cleared before rebuild.
    assert old_ids != new_ids or len(old_ids) != len(new_ids), \
        "Satellite IDs or count should differ after switch"

    # Now collect more state updates and verify they have new IDs
    client.state_updates.clear()
    await client.recv_loop(duration=1.0)

    for su in client.state_updates:
        update_ids = set(su["payload"]["satellite_positions"].keys())
        assert update_ids == new_ids, \
            "State update IDs should match new constellation"

    print(f"OK  (old={len(old_ids)} sats → new={len(new_ids)} sats)")
    await client.disconnect()


# ============================================================
# Main test runner
# ============================================================

async def run_integration_tests():
    """Run all integration tests. Requires a running backend + core.

    IMPORTANT: The relay architecture broadcasts simulation_init to
    currently-connected clients at the moment the core sends it.  Clients
    that connect later only receive state_updates (no init) until a
    switch_constellation command causes the core to re-send init.
    """
    import websockets

    # Check if backend is already running
    try:
        ws = await asyncio.wait_for(
            websockets.connect(f"ws://{BACKEND_HOST}:{BACKEND_PORT}/ws/client"),
            timeout=2
        )
        await ws.close()
        using_existing = True
        print("\n--- Integration Tests ---")
        print("(using existing backend + core)")
    except Exception:
        using_existing = False
        print("\n--- Integration Tests ---")
        print("Starting backend ... ", end="", flush=True)
        backend_proc = subprocess.Popen(
            [sys.executable, "-m", "realtime_backend.run",
             "--host", BACKEND_HOST, "--port", str(BACKEND_PORT),
             "--log-level", "warning"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2.0)
        print("OK")

        # Connect test client FIRST so it receives the init broadcast
        print("Starting test client before core ... ", end="", flush=True)
        test_client = TestClient()
        await test_client.connect()
        print("OK")

        # Start simulation core (init will be broadcast to our client)
        print("Starting simulation core ... ", end="", flush=True)
        core_proc = subprocess.Popen(
            [sys.executable, "hypatia-master/satviz/demo_sim_core.py",
             "--host", BACKEND_HOST, "--port", str(BACKEND_PORT),
             "--constellation", "Starlink", "--shell", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        print("OK")

        # Wait for init to arrive at the pre-connected client
        await test_client.recv_loop(duration=2.0)
        assert test_client.last_init() is not None, \
            "No simulation_init received (client connected before core)"
        print(f"  Initial init: {test_client.last_init()['constellation']['name']} "
              f"shell {test_client.last_init()['constellation']['current_shell']}, "
              f"{test_client.last_init()['total_satellites']} sats")
        await test_client.disconnect()
        print("Pre-connect validation OK")

    try:
        await test_integration_initial_load()
        await test_integration_switch_constellation()
        await test_integration_switch_shell()
        await test_integration_rapid_switches()
        await test_integration_reconnect()
        await test_integration_state_updates()
        await test_integration_switch_then_state()
        print("\nAll integration tests passed.")
    finally:
        if not using_existing:
            core_proc.terminate()
            backend_proc.terminate()
            core_proc.wait()
            backend_proc.wait()


def main():
    print("=" * 60)
    print("Constellation Switching — Verification Suite")
    print("=" * 60)

    # Part 1: Standalone logic tests
    print("\n--- Standalone Logic Tests ---")
    test_constellation_definitions()
    test_scaling()
    test_create_constellation()
    test_out_of_range_shell()
    test_compute_links()
    test_get_init_message()
    test_switch_constellation_command()
    test_clamped_shell_index()
    test_unknown_constellation()
    print("\nAll standalone tests passed.")

    # Part 2: Integration tests (only if websockets is available)
    try:
        import websockets  # noqa: F401
        asyncio.run(run_integration_tests())
    except ImportError:
        print("\n--- Integration Tests ---")
        print("SKIPPED: websockets library not installed.")
        print("Install with: pip install websockets")

    print("\n" + "=" * 60)
    print("All tests completed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
