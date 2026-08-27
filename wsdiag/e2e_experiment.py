"""端到端验证：模拟前端 WS 客户端跑通 E1~E9 教学实验。

  1. 连 ws://127.0.0.1:8000/ws/client，收 simulation_init，
     断言 payload.experiments 目录含 E1~E9；
  2. 依次 experiment_run E1..E7，收集 experiment_update 帧，
     断言每个实验 done 且 all_pass；
  3. 中途对 E2 触发一次 experiment_cancel 的取消路径冒烟（取消后重跑）；
  4. E8 故障诊断两阶段（模拟学生流程）：第一次只带 probes 观测 →
     断言 observations 观测表返回（干净/劣化分界）；第二次带 guess
     （默认种子 8 下 fault 链路可从 experiments.py 确定性推导为 L4）+
     evidence 文本 + attempts 历史，断言定位命中、得分提升且 all_pass；
  5. E9 星座设计三次迭代（模拟学生逆向设计流程）：目标约束
     e2e ≤ 40 ms / 丢包 ≤ 1% / 跳数 ≤ 4，跳数 = (P−1)+⌊M/2⌋、
     e2e ≈ 11 + 8×跳数。依次提交 P1M8（e2e≈43 ms 超标）→ P2M4
     （性能达标但仅 2 组方案）→ P2M5（3 组方案全达标），每轮回传
     上一轮 attempt 到 attempts[]，断言 targets/history 随帧下发、
     迭代判据按不同 (P, M) 组合计数、末轮四项判据全过；
  6. S5 并发排队：同时下发 6 个实验（并发上限 4），断言出现 queued 帧
     且全部最终 done（验证 FIFO 出队而非拒绝）。
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
        assert ids == ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8",
                       "E9"], f"catalog mismatch: {ids}"
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

        # ---- 4. E3 / E4 / E5 / E6 / E7 ----
        for eid in ("E3", "E4", "E5", "E6", "E7"):
            print(f"[4] run {eid} ...")
            await ws.send(json.dumps({
                "message_type": "command",
                "payload": {"action": "experiment_run",
                            "params": {"exp_id": eid}}}))
            done = await recv_until(ws, "done", eid, timeout=180)
            assert done["result"]["all_pass"], done["result"]["conclusion"]
            print(f"    {eid} PASS: {done['result']['conclusion']}")

        # ---- 5. E8 故障诊断两阶段（模拟学生流程） ----
        # fault 链路由种子确定性决定：_run_e8 默认 seed=8，
        # fault_idx = 1 + 8 % 3 = 3 → edge (S3, S4) = L4。
        # 探测 S2（路径不穿 L4，干净）与 S4（穿过 L4，劣化）呈现分界。
        print("[5] run E8 phase 1: probes only (observe) ...")
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run", "params": {
                "exp_id": "E8", "params": {"probes": "S2,S4"}}}}))
        done = await recv_until(ws, "done", "E8", timeout=180)
        res1 = done["result"]
        obs = res1.get("observations")
        assert isinstance(obs, list) and [o["node"] for o in obs] == ["S2", "S4"], \
            f"observations mismatch: {obs}"
        assert obs[0]["loss_pct"] < 20.0, f"S2 should be clean: {obs}"
        assert obs[1]["loss_pct"] >= 20.0, f"S4 should be degraded: {obs}"
        assert not res1["all_pass"], "phase 1 (no guess) must not pass"
        att1 = res1.get("attempt")
        assert isinstance(att1, dict) and {
            "params", "score", "all_pass", "ts", "metrics"} <= set(att1), att1
        print(f"    E8 phase 1 PASS: S2 loss={obs[0]['loss_pct']}%, "
              f"S4 loss={obs[1]['loss_pct']}% (boundary visible)")

        print("[5] run E8 phase 2: guess=L4 + evidence + attempts ...")
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run", "params": {
                "exp_id": "E8",
                "params": {"probes": "S2,S4", "guess": "L4",
                           "evidence": "S2 上游观测干净（丢包≈0），"
                                       "S4 观测劣化（丢包高、时延陡增）："
                                       "对比两节点，根因在 S2 与 S4 之间的链路段"},
                "attempts": [att1]}}}))
        done = await recv_until(ws, "done", "E8", timeout=180)
        res2 = done["result"]
        assert res2["all_pass"], res2["conclusion"]
        assert res2["score"] > res1["score"], \
            f"score should improve: {res1['score']} -> {res2['score']}"
        hit = next(r for r in res2["verdict"] if r["label"] == "根因链路定位")
        assert hit["pass"], res2["verdict"]
        obs2 = res2.get("observations")
        assert isinstance(obs2, list) and len(obs2) == 2, obs2
        print(f"    E8 phase 2 PASS: score {res1['score']} -> "
              f"{res2['score']}, {res2['conclusion']}")

        # ---- 6. E9 星座设计三次迭代（模拟学生逆向设计流程） ----
        # 目标约束 e2e ≤ 40 ms / 丢包 ≤ 1% / 跳数 ≤ 4；跳数 = (P−1)+⌊M/2⌋，
        # e2e ≈ 11 + 8×跳数。三次方案：P1M8（e2e≈43 ms 超标）→
        # P2M4（性能达标但仅 2 组方案）→ P2M5（3 组全达标）。
        # 每轮把上一轮结果帧的 attempt 回传 attempts[]（edu 存档闭环同款），
        # 迭代判据按不同 (P, M) 组合计数（P1M8/P2M4/P2M5 = 3 组）。
        print("[6] run E9 design iterations (P1M8 -> P2M4 -> P2M5) ...")
        e9_attempts = []
        e9_designs = [
            ({"planes": 1, "sats_per_plane": 8, "src_pps": 200}, False),
            ({"planes": 2, "sats_per_plane": 4, "src_pps": 200}, False),
            ({"planes": 2, "sats_per_plane": 5, "src_pps": 200}, True),
        ]
        res = None
        for i, (design, want_pass) in enumerate(e9_designs, 1):
            await ws.send(json.dumps({
                "message_type": "command",
                "payload": {"action": "experiment_run", "params": {
                    "exp_id": "E9", "params": design,
                    "attempts": e9_attempts}}}))
            done = await recv_until(ws, "done", "E9", timeout=180)
            res = done["result"]
            assert res["all_pass"] == want_pass, \
                f"iter {i} all_pass={res['all_pass']}, want {want_pass}"
            # 目标约束与方案对比历史随结果帧下发（前端徽标条/对比表数据源）
            assert res["targets"] == {"e2e_max_ms": 40.0, "loss_max": 0.01,
                                      "hops_max": 4}, res["targets"]
            assert len(res["history"]) == i, res["history"]
            m = res["measured"]
            print(f"    iter{i} P{design['planes']}xM{design['sats_per_plane']}: "
                  f"e2e {m['e2e_ms']:.1f} ms, loss {m['loss']:.2%}, "
                  f"hops {m['hops']} -> "
                  f"{'PASS' if res['all_pass'] else 'not yet'} "
                  f"(score {res['score']})")
            e9_attempts.append(res["attempt"])
        # 末轮：四项判据全过（性能 3 项 + 迭代 3 组不同方案）
        rows = {r["label"]: r for r in res["verdict"]}
        assert len(rows) == 4 and all(r["pass"] for r in rows.values()), \
            res["verdict"]
        assert rows["设计迭代 ≥ 3 次不同方案"]["measured"] == 3, res["verdict"]
        # 前两轮的失败原因符合逆向设计教学预期（超标→迭代不足→收敛）
        print(f"    E9 PASS: {res['conclusion']}")

        # ---- 7. S5 并发排队：6 个并发请求 > 上限 4，多出的排队 ----
        # E7 为包级仿真最重实验（墙钟 ~4 s，远大于核心主循环 ~200 ms 的
        # 命令消化周期），6 个齐发必然堆满 4 路并发，多出的进入 FIFO 队列。
        print("[7] burst 6 concurrent E7 runs (cap 4, expect queued + all done) ...")
        for _ in range(6):
            await ws.send(json.dumps({
                "message_type": "command",
                "payload": {"action": "experiment_run",
                            "params": {"exp_id": "E7"}}}))
        seen_queued = 0
        done_ids = []
        deadline = asyncio.get_event_loop().time() + 300
        while len(done_ids) < 6:
            raw = await asyncio.wait_for(
                ws.recv(), timeout=max(1, deadline - asyncio.get_event_loop().time()))
            msg = json.loads(raw)
            if msg.get("message_type") != "experiment_update":
                continue
            p = msg["payload"]
            if p.get("status") == "queued":
                seen_queued += 1
                print(f"    queued: {p.get('run_id')} "
                      f"(pos {p.get('queue_pos')})")
            elif p.get("status") == "done":
                if p.get("run_id") and p["run_id"] not in done_ids:
                    done_ids.append(p["run_id"])
                    print(f"    done: {p.get('run_id')} "
                          f"({len(done_ids)}/6)")
        assert seen_queued >= 2, \
            f"expected >=2 queued frames (6 runs vs cap 4), got {seen_queued}"
        print(f"    queue PASS: {seen_queued} queued, {len(done_ids)} done")

    print("\nALL E2E CHECKS PASSED")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
