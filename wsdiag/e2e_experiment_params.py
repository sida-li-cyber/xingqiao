"""端到端验证（P0 参数化）：模拟前端 WS 客户端带自定义参数运行实验。

验证链路：lab.html 参数表单 → experiment_run(params) → demo_sim_core 透传
→ experiments.run_experiment 夹紧/生效 → experiment_update(done) 回传。

Run:  python -u wsdiag/e2e_experiment_params.py
前置：realtime_backend(:8000) + demo_sim_core 已连接
"""

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8000/ws/client"

CASES = [
    ("E1", {"pkt_bytes": 9000, "pps": 20}, 21.223, 1.0),
    ("E2", {"rho": 0.6, "window_s": 60}, None, None),
    ("E3", {"queue_pkts": 100, "src_pps": 5000}, 101.0, 1.0),
]


async def recv_done(ws, exp_id, timeout=120.0):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("message_type") != "experiment_update":
            continue
        p = msg["payload"]
        if p["exp_id"] != exp_id:
            continue
        print(f"    [{exp_id}] {p['status']:<10} "
              f"{round(p.get('progress', 0) * 100):>3}%")
        if p["status"] == "done":
            return p["result"]
        if p["status"] == "error":
            raise AssertionError(f"experiment error: {p.get('error')}")


async def main():
    async with websockets.connect(URL) as ws:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("message_type") == "simulation_init":
                break
        assert msg["payload"].get("experiments"), "catalog missing"
        for e in msg["payload"]["experiments"]:
            assert e.get("inputs"), f"{e['exp_id']} inputs schema missing"

        for exp_id, params, expect, tol in CASES:
            print(f"[*] run {exp_id} with params={params}")
            await ws.send(json.dumps({
                "message_type": "command",
                "payload": {"action": "experiment_run",
                            "params": {"exp_id": exp_id, "params": params}}}))
            r = await recv_done(ws, exp_id)
            assert r["all_pass"], r["conclusion"]
            used = r["params_used"]
            assert used == {**params} or all(used[k] == v for k, v in params.items()), \
                f"params not applied: {used}"
            if expect is not None:
                m = r["verdict"][0]["measured"]
                assert abs(m - expect) <= tol, f"{exp_id} measured {m} != {expect}"
            print(f"    {exp_id} PASS  params_used={used}")
            print(f"    {r['conclusion']}")

        # 越界夹紧经 WS 链路同样生效
        print("[*] run E3 with out-of-range params (clamp check)")
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run", "params": {
                "exp_id": "E3", "params": {"queue_pkts": 99999,
                                           "src_pps": 1}}}}))
        r = await recv_done(ws, "E3")
        assert r["params_used"]["queue_pkts"] == 400
        assert r["params_used"]["src_pps"] == 1000
        assert r["all_pass"], r["conclusion"]
        print(f"    clamp OK: {r['params_used']}")

    print("\nALL PARAMETRIZED E2E CHECKS PASSED")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
