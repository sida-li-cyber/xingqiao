"""E2E check: constellation hot-swap through backend -> core -> client.

Connects as a browser client to /ws/client, verifies the initial
simulation_init, then drives set_constellation (preset + custom) and
asserts that a fresh simulation_init echoes the new constellation.

Usage: python wsdig/verify_constellation_switch.py
"""

import asyncio
import json
import sys

import websockets

URI = "ws://127.0.0.1:8000/ws/client"

EXPECTED = {
    # (name) -> sat_count
    "demo72": 72,
    "kuiper": 1156,
    "telesat": 351,
}


async def next_init(ws, timeout=15.0):
    """Consume frames until the next simulation_init arrives."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("message_type") == "simulation_init":
            return msg["payload"].get("constellation") or {}
    raise TimeoutError("no simulation_init received")


async def switch(ws, params):
    await ws.send(json.dumps({
        "message_type": "command",
        "payload": {"action": "set_constellation", "params": params},
    }))


async def main():
    failures = []

    def check(label, cond, detail=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
              + (f"  ({detail})" if detail else ""), flush=True)
        if not cond:
            failures.append(label)

    async with websockets.connect(URI) as ws:
        # 1) synchronise the init stream: drain any replayed / stale inits
        # until the distinctive trigger response arrives.
        await switch(ws, {"name": "telesat"})
        while True:
            const = await next_init(ws)
            if const.get("name") == "telesat":
                break
        print(f"[1] synced on trigger init: {const.get('name')} "
              f"{const.get('sat_count')} sats", flush=True)
        check("trigger switch echoes telesat", const.get("sat_count") == 351)

        # 2) preset switches
        for name, count in (("kuiper", EXPECTED["kuiper"]),
                            ("demo72", EXPECTED["demo72"])):
            await switch(ws, {"name": name})
            const = await next_init(ws)
            print(f"[2] after switch -> {const.get('name')} "
                  f"{const.get('sat_count')} sats", flush=True)
            check(f"switch to {name}: init echoes name",
                  const.get("name") == name, const.get("name"))
            check(f"switch to {name}: sat_count == {count}",
                  const.get("sat_count") == count,
                  const.get("sat_count"))
            check(f"switch to {name}: shells announced",
                  bool(const.get("shells")),
                  f"{len(const.get('shells') or [])} shell(s)")

        # 3) custom single shell
        await switch(ws, {"custom": {"planes": 3, "sats_per_plane": 4,
                                     "altitude_km": 800,
                                     "inclination_deg": 45}})
        const = await next_init(ws)
        print(f"[3] after custom -> {const.get('name')} "
              f"{const.get('sat_count')} sats", flush=True)
        check("custom: init echoes name", const.get("name") == "custom")
        check("custom: sat_count == 12", const.get("sat_count") == 12)
        shell = (const.get("shells") or [{}])[0]
        check("custom: shell params echoed",
              shell.get("planes") == 3 and shell.get("sats_per_plane") == 4
              and abs(shell.get("altitude_km", 0) - 800) < 1e-6)

        # 4) invalid command leaves state unchanged
        await switch(ws, {"name": "galileo"})
        got_init = False
        poll_deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < poll_deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                break
            if json.loads(raw).get("message_type") == "simulation_init":
                got_init = True
                break
        check("invalid preset: no init re-sent", not got_init)

        # 5) restore default for the live demo session
        await switch(ws, {"name": "demo72"})
        const = await next_init(ws)
        check("restore demo72", const.get("name") == "demo72")

    print(flush=True)
    if failures:
        print(f"RESULT: FAIL ({len(failures)} check(s) failed)", flush=True)
        sys.exit(1)
    print("RESULT: PASS (all checks passed)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
