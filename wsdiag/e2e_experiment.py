"""端到端验证：模拟前端 WS 客户端跑通 E1~E4 教学实验。

  1. 连 ws://127.0.0.1:8000/ws/client，收 simulation_init，
     断言 payload.experiments 目录含 E1~E4；
  2. 依次 experiment_run E1..E4，收集 experiment_update 帧，
     断言每个实验 done 且 all_pass；
  3. 中途对 E2 触发一次 experiment_cancel 的取消路径冒烟（取消后重跑）。
"""

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8000/ws/client"


async def recv_until(ws, want_status, exp_id, timeout=60.0):
    """收帧直到目标实验出现指定 status，返回该帧 payload。"""
    terminal = {"done", "cancelled", "error"}
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("message_type") != "experiment_update":
            continue
        p = msg["payload"]
        print(f"    [{p['exp_id']}] {p['status']:<10} "
              f"{p.get('stage', '') or '':<12} "
              f"{round(p.get('progress', 0) * 100):>3}%  {p.get('note', '')}")
        if p["exp_id"] != exp_id:
            continue
        if p["status"] == want_status:
            return p
        # 实验一旦进入任一终止状态（done/cancelled/error）即返回，
        # 由调用方判定是否符合预期；否则取消路径会因广播帧持续到达
        # 而永远等不到 wanted 状态（recv 每次都有帧刷新，永不超时）。
        if p["status"] in terminal:
            return p


async def main():
    async with websockets.connect(URL) as ws:
        # ---- 1. simulation_init 中的实验目录 ----
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("message_type") == "simulation_init":
                break
        catalog = msg["payload"].get("experiments") or []
        ids = [e["exp_id"] for e in catalog]
        print(f"[1] simulation_init.experiments = {ids}")
        assert ids == ["E1", "E2", "E3", "E4"], f"catalog mismatch: {ids}"
        assert all("params" in e and "theory_note" in e for e in catalog)

        # ---- 2. E1 全流程 ----
        print("[2] run E1 ...")
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run", "params": {"exp_id": "E1"}}}))
        done = await recv_until(ws, "done", "E1")
        assert done["result"]["all_pass"], done["result"]["conclusion"]
        assert len(done["result"]["verdict"]) == 3
        print(f"    E1 PASS: {done['result']['conclusion']}")

        # ---- 3. E2 取消后重跑 ----
        print("[3] run E2, cancel, then rerun ...")
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run", "params": {"exp_id": "E2"}}}))
        await recv_until(ws, "running", "E2", timeout=30)
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_cancel", "params": {}}}))
        # 实验太快时取消可能赶不上：cancelled 或 done 均可接受
        end = await recv_until(ws, "cancelled", "E2", timeout=30)
        if end["status"] == "cancelled":
            print("    E2 cancelled OK")
        else:
            print("    E2 finished before cancel arrived (acceptable)")
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run", "params": {"exp_id": "E2"}}}))
        done = await recv_until(ws, "done", "E2", timeout=120)
        assert done["result"]["all_pass"], done["result"]["conclusion"]
        print(f"    E2 PASS: {done['result']['conclusion']}")

        # ---- 4. E3 / E4 ----
        for eid in ("E3", "E4"):
            print(f"[4] run {eid} ...")
            await ws.send(json.dumps({
                "message_type": "command",
                "payload": {"action": "experiment_run",
                            "params": {"exp_id": eid}}}))
            done = await recv_until(ws, "done", eid, timeout=120)
            assert done["result"]["all_pass"], done["result"]["conclusion"]
            print(f"    {eid} PASS: {done['result']['conclusion']}")

    print("\nALL E2E CHECKS PASSED")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
