"""
End-to-end file-transfer test (Milestone A, phase B data plane).

Proves the full pipeline moves REAL bytes, not just abstract counters:

  1. upload a random blob via POST /api/files/upload  (backend slices it into
     chunk part-files matching the simulation's chunk_size)
  2. send a ``file_send`` command over the client WS (UAV-01 -> Beijing); the
     backend enriches it with total_bytes/chunk_size and forwards to the core
  3. the core models the file as abstract chunks routed hop-by-hop through the
     ISL mesh with ARQ, and reports file_chunk_delivered events back to the
     backend over /ws/core
  4. the backend reassembles the delivered chunks and SHA-256-verifies them
  5. GET /api/files/{id}/download serves the reassembled bytes; we confirm the
     SHA-256 matches the original upload exactly (real bytes landed)

Also checks the live tracker: state_update carries file_transfers with progress
reaching 1.0 and state COMPLETE.

Run:  python tests/test_file_e2e.py        (~25 s, spawns backend + core)
"""

import asyncio
import hashlib
import json
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import websockets

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parent.parent             # dayilixiang-v3/

HOST = "127.0.0.1"
PORT = 8769
HTTP = f"http://{HOST}:{PORT}"
WS_URL = f"ws://{HOST}:{PORT}/ws/client"

SRC = "UAV-01"
DST = "Beijing"
FILE_BYTES = 200 * 1024                       # 200 KB -> 13 chunks @16 KB


# ----------------------------------------------------------------------
# Process management (mirrors test_reconnect.py)
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


def wait_health(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{HTTP}/health", timeout=2) as r:
                h = json.loads(r.read())
            if predicate(h):
                return h
        except Exception:
            pass
        time.sleep(0.3)
    return None


# ----------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ----------------------------------------------------------------------

def upload(data: bytes, filename: str) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{HTTP}/api/files/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get_record(file_id: str) -> dict:
    with urllib.request.urlopen(f"{HTTP}/api/files/{file_id}", timeout=5) as r:
        return json.loads(r.read())


def download(file_id: str) -> bytes | None:
    """Return the reassembled bytes, or None while the transfer is incomplete."""
    try:
        with urllib.request.urlopen(
                f"{HTTP}/api/files/{file_id}/download", timeout=10) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (409, 410):       # not complete yet / data missing
            return None
        raise


# ----------------------------------------------------------------------
# Test
# ----------------------------------------------------------------------

def report(name, ok, detail=""):
    print(f"  {name} {'PASS' if ok else 'FAIL'}   {detail}")
    return ok


async def main():
    results = []
    backend = start_backend()
    core = None
    try:
        assert wait_health(lambda h: h.get("status") == "ok"), \
            "backend did not come up"
        core = start_core()
        assert wait_health(lambda h: h.get("cores_connected") == 1), \
            "core did not connect"

        # Random blob with a known hash.
        rng = random.Random(1234)
        data = bytes(rng.getrandbits(8) for _ in range(FILE_BYTES))
        sha = hashlib.sha256(data).hexdigest()

        # ---- 1. upload ---------------------------------------------------
        print(f"upload {FILE_BYTES} bytes (sha256={sha[:12]}…)")
        rec = upload(data, "e2e_blob.bin")
        file_id = rec["file_id"]
        results.append(report(
            "F1 upload", rec["total_bytes"] == FILE_BYTES and
            rec["sha256"] == sha and rec["state"] == "STORED",
            f"file_id={file_id} chunks={rec['total_chunks']}"))

        # ---- 2. file_send + watch the live tracker -----------------------
        print(f"file_send {SRC} -> {DST}")
        ws = await websockets.connect(WS_URL)
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "file_send",
                        "params": {"file_id": file_id, "src": SRC, "dst": DST}},
        }))

        complete = False
        max_progress = 0.0
        saw_path = False
        deadline = time.time() + 90.0
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("message_type") != "state_update":
                continue
            ft = msg["payload"].get("file_transfers")
            if not ft or file_id not in ft:
                continue
            info = ft[file_id]
            max_progress = max(max_progress, info["progress"])
            if info.get("path"):
                saw_path = True
            if info["state"] == "COMPLETE" and info["progress"] >= 1.0:
                complete = True
                break
        await ws.close()
        results.append(report(
            "F2 transfer completes (tracker)", complete,
            f"progress={max_progress:.3f} path_seen={saw_path}"))

        # ---- 3. backend reassembly + verified download -------------------
        downloaded = None
        deadline = time.time() + 15.0
        while time.time() < deadline:
            downloaded = download(file_id)
            if downloaded is not None:
                break
            await asyncio.sleep(0.3)

        rec2 = get_record(file_id)
        results.append(report(
            "F3 backend verified COMPLETE", rec2["state"] == "COMPLETE" and
            rec2["verified"] and rec2["reassembled_sha256"] == sha,
            f"state={rec2['state']} verified={rec2['verified']}"))

        dl_sha = hashlib.sha256(downloaded).hexdigest() if downloaded else None
        results.append(report(
            "F4 downloaded bytes match upload",
            downloaded is not None and len(downloaded) == FILE_BYTES and
            dl_sha == sha,
            f"len={len(downloaded) if downloaded else 0} "
            f"sha={ (dl_sha[:12] + '…') if dl_sha else 'None'}"))

        # ---- 4. cancel API sanity (large slow file, cancel mid-transfer) --
        # 3 MB at a capped 1 Mbps takes ~24 s of sim time, so the cancel sent
        # ~1 s later lands well before completion.
        rec3 = upload(b"x" * (3 * 1024 * 1024), "cancel_me.bin")
        fid3 = rec3["file_id"]
        ws = await websockets.connect(WS_URL)
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "file_send",
                        "params": {"file_id": fid3, "src": SRC, "dst": DST,
                                   "rate_bps": 1e6}},
        }))
        await asyncio.sleep(1.0)
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "file_cancel", "params": {"file_id": fid3}},
        }))
        await asyncio.sleep(1.0)
        await ws.close()
        rec3b = get_record(fid3)
        results.append(report(
            "F5 cancel reflected in store", rec3b["state"] == "CANCELLED",
            f"state={rec3b['state']}"))

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
