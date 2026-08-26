# -*- coding: utf-8 -*-
"""P0-P3 回归冒烟：以浏览器客户端身份连 :8000/ws/client，验证协议端到端。

检查项：
  1. simulation_init 到达（version 3.x / sat_order / link_types）
  2. state_update 持续到达，紧凑帧字段健全（sat_pos / links / is_playing）
  3. pause 指令往返：ack + is_playing 翻 False；play 恢复为 True
  4. Protocol31 纯函数能解码真实帧（与浏览器同源逻辑）
"""
import asyncio
import json
import sys
import io

import websockets

URI = "ws://127.0.0.1:8000/ws/client"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def main():
    async with websockets.connect(URI, max_size=16 * 1024 * 1024) as ws:
        init = None
        frames = []
        # 收 init + 若干 state 帧
        try:
            while len(frames) < 4:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                mt = msg.get("message_type")
                if mt == "simulation_init":
                    init = msg.get("payload", {})
                elif mt == "state_update":
                    frames.append(msg.get("payload", {}))
        except asyncio.TimeoutError:
            pass

        check("simulation_init 到达", init is not None,
              f"version={init and init.get('version')}")
        if init:
            check("sat_order/link_types 齐全",
                  bool(init.get("sat_order")) and bool(init.get("link_types")),
                  f"星数={len(init.get('sat_order') or [])}")
        check("state_update 持续推送(>=3帧/10s)", len(frames) >= 3, f"收到 {len(frames)} 帧")
        if not frames:
            return

        p = frames[-1]
        check("紧凑帧字段健全",
              isinstance(p.get("sat_pos"), list) and isinstance(p.get("links"), dict),
              f"sat_pos={len(p.get('sat_pos') or [])} links={len(p.get('links') or [])}")
        check("is_playing 字段存在(P0)", isinstance(p.get("is_playing"), bool),
              f"is_playing={p.get('is_playing')}")

        # Protocol31 同源逻辑解码真实帧（对齐/展开/合并）
        sat_order = (init or {}).get("sat_order") or []
        link_types = (init or {}).get("link_types") or {}
        positions = {}
        sp = p.get("sat_pos") or []
        for i in range(min(len(sp), len(sat_order))):
            positions[sat_order[i]] = {"lat": sp[i][0], "lon": sp[i][1]}
        if p.get("positions"):
            positions.update(p["positions"])
        check("Protocol31 逻辑解码真实帧", len(positions) >= len(sp) > 0,
              f"还原位置 {len(positions)} 个")

        # pause → ack + is_playing=False
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "pause", "params": None}}))
        got_ack, playing_after_pause = False, None
        try:
            deadline = asyncio.get_event_loop().time() + 8
            while asyncio.get_event_loop().time() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                mt = msg.get("message_type")
                pl = msg.get("payload", {})
                if mt == "ack" and pl.get("action") == "pause":
                    got_ack = True
                elif mt == "state_update" and isinstance(pl.get("is_playing"), bool):
                    playing_after_pause = pl["is_playing"]
                    if got_ack:
                        break
        except asyncio.TimeoutError:
            pass
        check("pause 指令 ack(P0)", got_ack)
        check("暂停后 is_playing=False(P0)", playing_after_pause is False,
              f"is_playing={playing_after_pause}")

        # play 恢复
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "play", "params": None}}))
        got_ack, playing_after_play = False, None
        try:
            deadline = asyncio.get_event_loop().time() + 8
            while asyncio.get_event_loop().time() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                mt = msg.get("message_type")
                pl = msg.get("payload", {})
                if mt == "ack" and pl.get("action") == "play":
                    got_ack = True
                elif mt == "state_update" and isinstance(pl.get("is_playing"), bool):
                    playing_after_play = pl["is_playing"]
                    if got_ack and playing_after_play:
                        break
        except asyncio.TimeoutError:
            pass
        check("play 指令 ack(P0)", got_ack)
        check("恢复后 is_playing=True(P0)", playing_after_play is True,
              f"is_playing={playing_after_play}")

    failed = [r for r in results if not r[1]]
    print(f"\n==== {len(results) - len(failed)}/{len(results)} PASS ====")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
