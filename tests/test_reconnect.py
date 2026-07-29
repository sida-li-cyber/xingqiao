"""
Phase 6 robustness tests - disconnect / reconnect behaviour of the full
pipeline (backend relay + simulation core + frontend-style WS client).

  R1  normal flow      : client receives simulation_init + state updates.
  R2  core dies        : backend stays alive (cores_connected=0), the client
                         WS stays open, and commands get error/no_core_connected.
  R3  core restarts    : the still-connected client receives a fresh
                         simulation_init and state updates resume.
  R4  client reconnect : a new client immediately gets the replayed
                         simulation_init (backend feature) plus live updates.
  R5  backend restart  : the core SURVIVES the outage (regression: it used to
                         exit), keeps retrying, reconnects when the backend
                         returns, and a new client gets init + updates.

Run:  python test_reconnect.py        (~40 s, spawns backend + core locally)
"""

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parent.parent             # dayilixiang-v3/
SATVIZ = PROJECT_ROOT / "hypatia-master" / "satviz"

HOST = "127.0.0.1"
PORT = 8767
URL = f"ws://{HOST}:{PORT}/ws/client"
CLOSED = {"__closed__": True}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def start_backend():
    return subprocess.Popen(
        [sys.executable, "-m", "realtime_backend.run",
         "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def start_core():
    return subprocess.Popen(
        [sys.executable, "hypatia-master/satviz/demo_sim_core.py",
         "--host", HOST, "--port", str(PORT)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def health():
    with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2) as r:
        return json.loads(r.read())


def wait_health(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            h = health()
            if predicate(h):
                return h
        except Exception:
            pass
        time.sleep(0.3)
    return None


async def collect(ws, duration, store):
    """Collect messages for `duration` seconds into `store`."""
    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
            store.append(json.loads(raw))
        except asyncio.TimeoutError:
            pass
        except websockets.exceptions.ConnectionClosed:
            store.append(CLOSED)
            break


async def send_command(ws, action="play"):
    await ws.send(json.dumps({
        "message_type": "command",
        "payload": {"action": action, "params": {}},
    }))


def counts(msgs):
    c = {"simulation_init": 0, "state_update": 0, "ack": 0, "error": 0,
         "closed": 0}
    for m in msgs:
        if m is CLOSED:
            c["closed"] += 1
        else:
            mt = m.get("message_type")
            if mt in c:
                c[mt] += 1
    return c


def report(name, ok, detail=""):
    print(f"  {name} {'PASS' if ok else 'FAIL'}   {detail}")
    return ok


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------

async def main():
    results = []
    backend = start_backend()
    core = None
    ws = None
    try:
        assert wait_health(lambda h: h.get("status") == "ok"), \
            "backend did not come up"
        core = start_core()
        time.sleep(2.0)

        # ---- R1: normal flow ------------------------------------------
        print("R1: normal flow (init + state updates)")
        ws = await websockets.connect(URL)
        msgs = []
        await collect(ws, 2.5, msgs)
        c = counts(msgs)
        results.append(report(
            "R1", c["simulation_init"] >= 1 and c["state_update"] >= 3,
            f"init={c['simulation_init']} updates={c['state_update']}"))

        # ---- R2: core dies ---------------------------------------------
        print("R2: kill core -> backend survives, client stays connected")
        core.terminate()
        core.wait()
        h = wait_health(lambda h: h.get("cores_connected") == 0)
        msgs = []
        await send_command(ws)                  # command into the void
        await collect(ws, 1.5, msgs)
        c = counts(msgs)
        err = next((m for m in msgs
                    if m is not CLOSED and m.get("message_type") == "error"),
                   None)
        no_core = bool(err and
                       err["payload"].get("status") == "no_core_connected")
        results.append(report(
            "R2", h is not None and c["closed"] == 0 and no_core,
            f"health={h} closed={c['closed']} no_core_error={no_core}"))

        # ---- R3: core restarts ------------------------------------------
        print("R3: restart core -> fresh init + updates resume")
        core = start_core()
        msgs = []
        await collect(ws, 6.0, msgs)
        c = counts(msgs)
        results.append(report(
            "R3", c["simulation_init"] >= 1 and c["state_update"] >= 3,
            f"init={c['simulation_init']} updates={c['state_update']}"))

        # ---- R4: client reconnects --------------------------------------
        print("R4: new client -> replayed init without any command")
        await ws.close()
        ws2 = await websockets.connect(URL)
        msgs = []
        await collect(ws2, 2.0, msgs)
        c = counts(msgs)
        first_is_init = (msgs and msgs[0] is not CLOSED and
                         msgs[0].get("message_type") == "simulation_init")
        results.append(report(
            "R4", first_is_init and c["state_update"] >= 1,
            f"first_is_init={first_is_init} updates={c['state_update']}"))
        await ws2.close()

        # ---- R5: backend restart ----------------------------------------
        print("R5: kill backend -> core survives and reconnects on return")
        backend.terminate()
        backend.wait()
        time.sleep(1.5)
        core_alive = core.poll() is None        # regression: used to exit
        backend = start_backend()
        h = wait_health(lambda h: h.get("cores_connected") == 1, timeout=15.0)
        ws3 = await websockets.connect(URL)
        msgs = []
        await collect(ws3, 4.0, msgs)
        c = counts(msgs)
        results.append(report(
            "R5", core_alive and h is not None and
            c["simulation_init"] >= 1 and c["state_update"] >= 3,
            f"core_survived={core_alive} reconnected={h is not None} "
            f"init={c['simulation_init']} updates={c['state_update']}"))
        await ws3.close()

    finally:
        for p in (core, backend):
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()

    print("=" * 64)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return all(results)


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
