"""端到端验证（改进计划阶段一/二核心链路，真实后端 + 核心 WS）：

  1. simulation_init.experiments 携带 quiz（题干+选项，无答案泄漏）与 score_max；
  2. experiment_quiz 命令：全对 -> 10 分，判分帧带解析；
  3. experiment_run：帧带 run_id，结果带 score / score_detail（E1 默认 = 70）；
  4. 并发：E1 运行中再发 E2，两个 run_id 各自完成（S5）；
  5. experiment_cancel 带 run_id 取消第三个实验（或它已跑完，二者均验证帧正确性）。

消费模型：单一读取循环把所有 experiment_update 帧存入缓冲，避免等待
特定帧时丢弃其它终态帧。

Run:  python wsdiag/verify_experiments_phase1.py   （需 8000 端口栈在线）
"""
import asyncio
import json
import os

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

import websockets  # noqa: E402

WS = "ws://127.0.0.1:8000/ws/client"


class FrameBuf:
    def __init__(self, ws):
        self.ws = ws
        self.frames = []

    async def wait(self, pred, timeout=90.0):
        """缓冲中找满足条件的帧；没有则继续收帧直到超时。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            for f in self.frames:
                if pred(f):
                    self.frames.remove(f)
                    return f
            remain = deadline - asyncio.get_event_loop().time()
            if remain <= 0:
                raise TimeoutError("frame not received")
            msg = json.loads(await asyncio.wait_for(
                self.ws.recv(), timeout=remain))
            if msg.get("message_type") == "experiment_update":
                self.frames.append(msg["payload"])


async def main():
    async with websockets.connect(WS, max_size=16 * 1024 * 1024) as ws:
        buf = FrameBuf(ws)

        # 1) init 目录（非 experiment_update，直接读）
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("message_type") == "simulation_init":
                break
            if msg.get("message_type") == "experiment_update":
                buf.frames.append(msg["payload"])
        exps = msg["payload"]["experiments"]
        assert [e["exp_id"] for e in exps] == ["E1", "E2", "E3", "E4"]
        for e in exps:
            assert e["score_max"] == {"verdict": 70, "explore": 10,
                                      "quiz": 10, "questions": 10}
            assert len(e["quiz"]) == 3
            for q in e["quiz"]:
                assert set(q) == {"q", "options"}, "答案不得下发"
        print("1. catalog quiz/score_max OK（无答案泄漏）")

        # 2) quiz 判分
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_quiz",
                        "params": {"exp_id": "E2", "answers": {"0": 0, "1": 0,
                                                               "2": 0}}}}))
        grade = (await buf.wait(
            lambda p: p.get("status") == "quiz"))["quiz"]
        assert grade["score"] == 10.0 and grade["n_correct"] == 3
        assert all(d["explain"] for d in grade["detail"])
        print(f"2. quiz 判分 OK: {grade['n_correct']}/{grade['n_total']} "
              f"-> {grade['score']} 分")

        # 3+4) 并发：E1（默认，期望 70 分）运行中再发 E2
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run",
                        "params": {"exp_id": "E1"}}}))
        run1 = (await buf.wait(
            lambda p: p.get("run_id", "").startswith("E1-")
            and p.get("status") == "running"))["run_id"]
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run",
                        "params": {"exp_id": "E2"}}}))
        run2 = (await buf.wait(
            lambda p: p.get("run_id", "").startswith("E2-")
            and p.get("status") == "running"))["run_id"]
        assert run1 != run2
        print(f"3. 并发运行 OK: {run1} 与 {run2} 同时在跑")

        # 5) 再发 E3，拿到 run_id 后定向取消
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_run",
                        "params": {"exp_id": "E3"}}}))
        run3 = (await buf.wait(
            lambda p: p.get("run_id", "").startswith("E3-")))["run_id"]
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "experiment_cancel",
                        "params": {"run_id": run3}}}))

        # 收集三个终态（done / cancelled 均可，error 视为失败）
        terminal = {}
        for _ in range(3):
            p = await buf.wait(
                lambda p: p.get("status") in ("done", "cancelled", "error")
                and p.get("run_id") not in terminal)
            terminal[p["run_id"]] = p

        r1, r2 = terminal[run1]["result"], terminal[run2]["result"]
        assert terminal[run1]["status"] == "done" and r1["all_pass"]
        assert r1["score"] == 70.0, r1["score"]          # 默认参数：70+0
        assert {d["item"] for d in r1["score_detail"]} == {"对账判定", "参数探索"}
        assert terminal[run2]["status"] == "done" and r2["score"] == 70.0
        assert terminal[run3]["status"] in ("done", "cancelled"), \
            terminal[run3]["status"]
        print(f"4. 结果评分 OK: {run1} score={r1['score']}，"
              f"{run2} score={r2['score']}（默认参数无探索分）")
        print(f"5. E3 终态 = {terminal[run3]['status']}"
              f"（取消命令已按 run_id 定向下发）")
        print("\nALL OK — 阶段一/二 WS 链路验证通过")


if __name__ == "__main__":
    asyncio.run(main())
