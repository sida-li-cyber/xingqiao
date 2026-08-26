"""星桥教学实验运行器 v3（评分闭环版）。

在独立沙箱 PacketEngine 中构造 E1~E4 教学实验场景，支持：

  * 声明式 inputs Schema（范围/步进/默认值/单位/提示），
    experiment_run 命令可携带 params 覆盖默认值（越界自动夹紧）；
  * 理论值随参数动态计算，对账容差随统计难度自适应放宽；
  * 每实验附带 guide（目的/原理/步骤/思考题）与 topology
    （SVG 拓扑数据）随 simulation_init.experiments 下发，
    前端据此自动生成输入表单与实验台界面；
  * 评分闭环（100 分制）：对账判定 70 + 参数探索 10 +
    预习测验 10 + 思考题 10。run_experiment 返回的 score/
    score_detail 只含前两项（与仿真结果相关的部分），预习与思考题由
    grade_quiz / grade_questions 判分，compose_score 合成总分；
  * 预习测验题库 QUIZZES：答案与解析仅在核心侧，经
    experiment_quiz 命令判分后回传（前端不下发答案）。

实验目录（默认参数）：

  E1 时延分解与对账   e2e ≈ 21 ms(传播) + 0.037 ms(发送) ≈ 21.037 ms
  E2 M/D/1 排队模型   Wq = ρs/2(1−ρ) = 12 ms，e2e ≈ 24 ms，ρ ≈ 0.8
  E3 链路切换丢包     尖峰 = 队列 200 + 在途 1 = 201 包
  E4 QoS 严格优先级   拥塞时 HIGH 时延/丢包 < BE 且 BE loss > 0
  E5 路由算法对比     最短时延路由拥塞丢包 (λ−C)/λ；负载感知绕行近零丢包
  E6 星座规模探索     网格 P×M：跳数 = (P−1) + ⌊M/2⌋，e2e = 11 + 8×跳数 ms
  E7 链路预算雨衰     Ka 波段雨衰 a dB → 容量 ×10^(−a/10)，丢包 = 1−C_eff/λ

引擎接口约定（packet_sim.PacketEngine）：

  * sync_topology(nodes, edges) 中容量按 link_type 取自
    config["capacity"]，传播时延由每边 prop_s 显式给出；
  * snapshot(dt) 的 e2e / utilization 是窗口化统计（每次调用清窗），
    delivered / dropped 为累计值——预热期用一次 snapshot 清窗，
    测量值取"测量窗末 snapshot − 基线累计数"；
  * sync_topology 移除边时，端口队列 + 在途包由 drain 计入
    handover 口径（total_handover_dropped），E3 的尖峰即来源于此。
"""

import asyncio

from packet_sim import (DEFAULT_CONFIG, PacketEngine, PRIO_BEST_EFFORT,
                        PRIO_HIGH)

PKT_BITS = 1500 * 8


class ExperimentCancelled(Exception):
    """用户主动取消实验。"""


class ExperimentNotFound(KeyError):
    """实验编号不存在。"""


# ----------------------------------------------------------------------
# 参数与引擎工具
# ----------------------------------------------------------------------

def _default_params(inputs):
    return {f["key"]: f["default"] for f in inputs}


def _sanitize_params(inputs, run_params):
    p = _default_params(inputs)
    if not run_params:
        return p
    for f in inputs:
        k = f["key"]
        if k not in run_params:
            continue
        cast = type(f["default"])
        try:
            v = cast(run_params[k])
        except (TypeError, ValueError):
            continue
        v = max(f["min"], min(f["max"], cast(v)))
        step = f.get("step")
        if step:
            v = cast(round((v - f["min"]) / step) * step + f["min"])
        p[k] = v
    return p


def _mk_engine(seed, capacity_extra=None, **cfg):
    cap = dict(DEFAULT_CONFIG["capacity"])
    if capacity_extra:
        cap.update(capacity_extra)
    config = {"capacity": cap}
    config.update(cfg)
    return PacketEngine(config=config, seed=seed)


async def _checkpoint(cancel_check):
    if cancel_check and cancel_check():
        raise ExperimentCancelled()
    await asyncio.sleep(0)


ADVANCE_CHUNK_S = 2.0


async def _advance(eng, until, cancel_check, chunk_s=ADVANCE_CHUNK_S):
    """分块推进 DES 到绝对时刻 until，块间让出事件循环。

    PacketEngine.advance 一次排空整个事件窗，长窗（如 E7 的 45 s 仿真
    ≈ 3 s 墙钟）会把事件循环饿死：主循环收不下一条命令、WS 心跳超时
    断连、并发上限永远凑不满（S5 排队机制失效）。DES 事件按绝对时间
    排空，切块推进与一次推进到 until 语义等价。
    """
    while until - eng.now > 1e-9:
        eng.advance(min(eng.now + chunk_s, until))
        await _checkpoint(cancel_check)


def _rows_pass(rows):
    return all(r["pass"] for r in rows)


# ----------------------------------------------------------------------
# 评分模型（改进计划 W1 / W2）：
#   对账判定 70 + 参数探索 10 + 预习测验 10 + 思考题 10 = 100 分。
# 前两项由仿真结果决定，在 run_experiment 内计算；后两项由前端采集、
# 核心判分（grade_quiz / grade_questions），compose_score 合成总分。
# ----------------------------------------------------------------------

SCORE_MAX = {"verdict": 70, "explore": 10, "quiz": 10, "questions": 10}


def _score_verdict(rows, max_pts=SCORE_MAX["verdict"]):
    """逐行分档：判据通过拿满分行分；未通过但数值误差 ≤ 2×容差给半分。"""
    if not rows:
        return 0.0
    per = max_pts / len(rows)
    got = 0.0
    for r in rows:
        if r["pass"]:
            got += per
            continue
        err, tol = r.get("error"), r.get("tolerance")
        if (isinstance(err, (int, float)) and isinstance(tol, (int, float))
                and tol and abs(err) <= 2.0 * tol):
            got += per * 0.5
    return round(got, 1)


def _score_explore(inputs, params):
    """参数探索分：改过任一默认参数并运行才得分（鼓励动手调参）。"""
    defaults = _default_params(inputs)
    changed = [k for k in defaults if params.get(k) != defaults[k]]
    return SCORE_MAX["explore"] if changed else 0


def _hop_summary(summary, nodes_metrics, src):
    """测量窗读数：e2e(ms) + 该源累计 sent/recv。"""
    return {
        "e2e_ms": nodes_metrics.get(src, {}).get("e2e_latency_ms", 0.0),
        "sent": nodes_metrics.get(src, {}).get("pkts_sent", 0),
        "recv": nodes_metrics.get(src, {}).get("pkts_recv", 0),
        "summary": summary,
    }


# ----------------------------------------------------------------------
# E1 时延分解与对账
# ----------------------------------------------------------------------

E1_INPUTS = [
    {"key": "pps", "label": "业务速率", "type": "float", "min": 5, "max": 50,
     "step": 1, "default": 10, "unit": "pps", "tip": "轻载泊松业务源"},
    {"key": "pkt_bytes", "label": "包长", "type": "int", "min": 500, "max": 9000,
     "step": 500, "default": 1500, "unit": "B", "tip": "IP 包长，决定发送时延"},
]
E1_PROP_MS = 21.0                     # 三跳传播 5 + 10 + 6 ms
E1_SER_PER_BIT = 1 / 5e8 + 1 / 1e10 + 1 / 1e9   # SUL + ISL + GSL 串行化


def _e1_theory(p):
    ser = p["pkt_bytes"] * 8 * E1_SER_PER_BIT * 1000.0
    return {"ser_ms": ser, "e2e_ms": E1_PROP_MS + ser}


async def _run_e1(p, on_progress, cancel_check, seed=42):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e1_theory(p)
    eng = _mk_engine(seed=seed, packet_size_bytes=p["pkt_bytes"])
    eng.sync_topology(
        ["UAV-1", "Sat-A", "Sat-B", "GS-1"],
        [("UAV-1", "Sat-A", "sul", 0.005),
         ("Sat-A", "Sat-B", "isl", 0.010),
         ("Sat-B", "GS-1", "gsl", 0.006)],
        transit=["Sat-A", "Sat-B"])
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": float(p["pps"])})

    prog("warmup", 0.1)
    await _advance(eng, 10.0, cancel_check)
    prog("simulating", 0.6)
    await _advance(eng, 20.0, cancel_check)
    prog("measuring", 0.9)
    snap = eng.snapshot(30.0)
    s = snap["summary"]

    e2e = s["avg_e2e_latency_ms"]
    rows = [
        {"label": "端到端平均时延", "theory": round(th["e2e_ms"], 3),
         "measured": round(e2e, 3), "unit": "ms",
         "error": round(e2e - th["e2e_ms"], 3), "tolerance": 1.0,
         "pass": abs(e2e - th["e2e_ms"]) <= 1.0},
        {"label": "丢包数", "theory": 0, "measured": s["pkts_dropped"],
         "unit": "包", "error": s["pkts_dropped"], "tolerance": 0,
         "pass": s["pkts_dropped"] == 0},
        {"label": "送达包数", "theory": "> 50% 发送数",
         "measured": s["pkts_delivered"], "unit": "包",
         "error": None, "tolerance": None,
         "pass": s["pkts_delivered"] > 0.5 * 30 * p["pps"]},
    ]
    ok = _rows_pass(rows)
    return {"verdict": rows, "all_pass": ok,
            "conclusion": (
                f"实测 e2e {e2e:.3f} ms，理论 {th['e2e_ms']:.3f} ms"
                f"（传播 {E1_PROP_MS:.0f} + 发送 {th['ser_ms']:.3f}），误差 "
                f"{abs(e2e - th['e2e_ms']):.3f} ms。"
                + ("" if ok else " 请检查是否已偏离轻载假设。")),
            "measured": s}


# ----------------------------------------------------------------------
# E2 M/D/1 排队模型
# ----------------------------------------------------------------------

E2_INPUTS = [
    {"key": "rho", "label": "利用率 ρ", "type": "float", "min": 0.3, "max": 0.95,
     "step": 0.05, "default": 0.8, "unit": "",
     "tip": "ρ→1 时排队时延悬崖式增长"},
    {"key": "window_s", "label": "测量窗口", "type": "int", "min": 30, "max": 300,
     "step": 30, "default": 120, "unit": "s", "tip": "预热 60 s 后的稳态测量时长"},
]
E2_S_MS = 6.0                         # 1500 B @ 2 Mbps 瓶颈
E2_PROP_MS = 6.0                      # 1 ms 接入 + 5 ms 下行
E2_SER_OTHER_MS = PKT_BITS / 1e10 * 1000.0   # 10 Gbps 接入链路发送时延


def _e2_theory(p):
    rho = p["rho"]
    wq = rho * E2_S_MS / (2.0 * (1.0 - rho))
    return {"wq_ms": wq,
            "e2e_ms": E2_PROP_MS + E2_SER_OTHER_MS + E2_S_MS + wq,
            "lam_pps": rho / E2_S_MS * 1000.0}


async def _run_e2(p, on_progress, cancel_check, seed=99):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e2_theory(p)
    tol = max(3.0, 0.22 * th["wq_ms"])        # ρ 高时涨落大，容差放宽

    eng = _mk_engine(seed=seed, capacity_extra={"bn": 2e6},
                     packet_size_bytes=1500)
    eng.sync_topology(
        ["src", "R1", "GS"],
        [("src", "R1", "isl", 0.001),          # 轻载接入
         ("R1", "GS", "bn", 0.005)],           # 2 Mbps 瓶颈 → s = 6 ms
        transit=["R1"])
    eng.sync_flows({"src": "GS"}, {"src": th["lam_pps"]})

    prog("warmup", 0.05, "预热 60 s（统计丢弃）")
    await _advance(eng, 60.0, cancel_check)
    eng.snapshot(0.0)                          # 清空预热窗口统计
    base_drop = eng.total_dropped
    base_sent = eng.n_generated["src"]

    chunks, total = 12, p["window_s"]
    for i in range(chunks):
        await _advance(eng, 60.0 + total * (i + 1) / chunks,
                       cancel_check)
        prog("measuring", 0.05 + 0.9 * (i + 1) / chunks,
             f"测量窗 {round(total * (i + 1) / chunks)}/{total} s")
    snap = eng.snapshot(float(total))
    s = snap["summary"]
    util = snap["links"].get(frozenset(("R1", "GS")), {}).get("utilization", 0.0)
    e2e = s["avg_e2e_latency_ms"]
    drops = s["pkts_dropped"] - base_drop

    rows = [
        {"label": "端到端平均时延", "theory": round(th["e2e_ms"], 2),
         "measured": round(e2e, 2), "unit": "ms",
         "error": round(e2e - th["e2e_ms"], 2), "tolerance": round(tol, 2),
         "pass": abs(e2e - th["e2e_ms"]) <= tol},
        {"label": "瓶颈利用率 ρ", "theory": p["rho"],
         "measured": round(util, 3), "unit": "",
         "error": round(util - p["rho"], 3), "tolerance": 0.05,
         "pass": abs(util - p["rho"]) <= 0.05},
        {"label": "稳态丢包", "theory": 0, "measured": drops, "unit": "包",
         "error": drops, "tolerance": 5,
         "pass": drops <= 5},
    ]
    ok = _rows_pass(rows)
    return {"verdict": rows, "all_pass": ok,
            "conclusion": (
                f"Wq 理论 {th['wq_ms']:.1f} ms；实测 e2e {e2e:.2f} ms"
                f"（容差 ±{tol:.1f} ms），利用率 {util:.3f}"
                f"（理论 {p['rho']:.2f}）。"
                + ("" if ok else " ρ 接近 1 时涨落增大，可加长测量窗口重试。")),
            "measured": s}


# ----------------------------------------------------------------------
# E3 链路切换丢包
# ----------------------------------------------------------------------

E3_INPUTS = [
    {"key": "queue_pkts", "label": "队列容量", "type": "int", "min": 50, "max": 400,
     "step": 50, "default": 200, "unit": "包", "tip": "端口缓存大小（Q）"},
    {"key": "src_pps", "label": "源速率", "type": "int", "min": 1000, "max": 6000,
     "step": 500, "default": 3000, "unit": "pps", "tip": "远超上行容量，用于堆满队列"},
]

E3_NODES = ["UAV-1", "Sat-A", "Sat-B", "GS-1"]
# A 路（主）：UAV-1—Sat-A—GS-1。上行用 1 Mbps "up" 类型：远低于源速率，
# 10 s 内把队列堆满，切换 drain 时产生 Q+1 尖峰。传播 5+5 ms。
E3_EDGES_A = [("UAV-1", "Sat-A", "up", 0.005),
              ("Sat-A", "GS-1", "gsl", 0.005)]
# B 路（备，虚线）：传播更大确保初始路由选 A
E3_EDGES_B = [("UAV-1", "Sat-B", "up", 0.008),
              ("Sat-B", "GS-1", "gsl", 0.008)]


def _e3_theory(p):
    return {"spike_pkts": p["queue_pkts"] + 1}


async def _run_e3(p, on_progress, cancel_check, seed=7):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e3_theory(p)
    # route_refresh_interval=0：切换后路由立即重算，避免 link-gone 丢包
    # 混入统计、破坏 handover 口径。
    eng = _mk_engine(seed=seed, capacity_extra={"up": 1e6},
                     queue_capacity_pkts=p["queue_pkts"],
                     route_refresh_interval=0.0)
    eng.sync_topology(E3_NODES, E3_EDGES_A + E3_EDGES_B,
                      transit=["Sat-A", "Sat-B"])
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": float(p["src_pps"])})

    prog("queueing", 0.3, "超速源堆积队列（10 s）")
    await _advance(eng, 10.0, cancel_check)
    base_drop = eng.total_dropped
    base_handover = eng.total_handover_dropped
    base_delivered = eng.total_delivered

    eng.sync_topology(E3_NODES, E3_EDGES_B)    # 切换：A 路拆除
    # 突发结束：流量回落到常态 40 pps（B 路可承载），
    # 使切换后的丢包仅为切换尖峰本身，保证口径纯净。
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": 40.0})
    await _advance(eng, 10.5, cancel_check)    # 切换后 0.5 s（新路由恢复）
    handover_spike = eng.total_handover_dropped - base_handover
    drops_at_switch = eng.total_dropped - base_drop

    prog("restoring", 0.7, "切换完成，恢复期 10 s")
    await _advance(eng, 20.5, cancel_check)
    prog("measuring", 0.95)
    s = eng.snapshot(10.0)["summary"]

    rows = [
        {"label": "切换丢包尖峰", "theory": th["spike_pkts"],
         "measured": handover_spike, "unit": "包",
         "error": handover_spike - th["spike_pkts"], "tolerance": 1,
         "pass": abs(handover_spike - th["spike_pkts"]) <= 1},
        {"label": "切换期残留丢包（非 handover）", "theory": "≤ 2",
         "measured": drops_at_switch - handover_spike, "unit": "包",
         "error": drops_at_switch - handover_spike, "tolerance": 2,
         "pass": (drops_at_switch - handover_spike) <= 2},
        {"label": "切换后 10.5 s 恢复送达", "theory": "> 0",
         "measured": eng.total_delivered - base_delivered, "unit": "包",
         "error": None, "tolerance": None,
         "pass": (eng.total_delivered - base_delivered) > 0},
    ]
    ok = _rows_pass(rows)
    return {"verdict": rows, "all_pass": ok,
            "conclusion": (
                f"尖峰 {handover_spike} 包 = 队列 {p['queue_pkts']} + 在途 1；"
                f"突发回落 40 pps 后恢复期送达 "
                f"{eng.total_delivered - base_delivered} 包。"
                + ("" if ok else " 尖峰不符：队列可能未在 10 s 内堆满。")),
            "measured": s}


# ----------------------------------------------------------------------
# E4 QoS 严格优先级
# ----------------------------------------------------------------------

E4_INPUTS = [
    {"key": "high_pps", "label": "高优先速率", "type": "int", "min": 20, "max": 80,
     "step": 5, "default": 60, "unit": "pps", "tip": "PRIO_HIGH 业务源"},
    {"key": "low_pps", "label": "尽力速率", "type": "int", "min": 20, "max": 120,
     "step": 5, "default": 60, "unit": "pps", "tip": "BEST_EFFORT 业务源"},
    {"key": "bottleneck_mbps", "label": "瓶颈容量", "type": "float", "min": 0.5,
     "max": 2.0, "step": 0.25, "default": 1.0, "unit": "Mbps", "tip": "共享出口链路"},
]


def _e4_theory(p):
    cap = p["bottleneck_mbps"] * 1e6 / PKT_BITS
    load = p["high_pps"] + p["low_pps"]
    return {"cap_pps": cap, "load_pps": load, "congested": load > cap}


async def _run_e4(p, on_progress, cancel_check, seed=11):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e4_theory(p)
    eng = _mk_engine(seed=seed, capacity_extra={"bn": p["bottleneck_mbps"] * 1e6},
                     packet_size_bytes=1500)
    eng.sync_topology(
        ["srcH", "srcL", "R1", "GS"],
        [("srcH", "R1", "isl", 0.001), ("srcL", "R1", "isl", 0.001),
         ("R1", "GS", "bn", 0.005)],
        transit=["R1"])
    eng.sync_flows({"srcH": "GS", "srcL": "GS"},
                   {"srcH": float(p["high_pps"]), "srcL": float(p["low_pps"])},
                   {"srcH": PRIO_HIGH, "srcL": PRIO_BEST_EFFORT})

    prog("warmup", 0.15, "拥塞演化 55 s")
    await _advance(eng, 55.0, cancel_check)
    eng.snapshot(0.0)                           # 清窗
    base = {s: eng.n_generated[s] for s in ("srcH", "srcL")}
    prio_base = {PRIO_HIGH: eng.n_delivered_prio[PRIO_HIGH],
                 PRIO_BEST_EFFORT: eng.n_delivered_prio[PRIO_BEST_EFFORT]}

    prog("measuring", 0.85, "测量窗 5 s")
    await _advance(eng, 60.0, cancel_check)
    snap = eng.snapshot(5.0)
    nm = snap["nodes"]

    h, b = _hop_summary(snap["summary"], nm, "srcH"), \
        _hop_summary(snap["summary"], nm, "srcL")
    h_sent = eng.n_generated["srcH"] - base["srcH"]
    b_sent = eng.n_generated["srcL"] - base["srcL"]
    # n_delivered 按到达节点记账（键为 GS），按流统计须用优先级计数器。
    h_recv = eng.n_delivered_prio[PRIO_HIGH] - prio_base[PRIO_HIGH]
    b_recv = (eng.n_delivered_prio[PRIO_BEST_EFFORT]
              - prio_base[PRIO_BEST_EFFORT])
    h_e2e, b_e2e = h["e2e_ms"], b["e2e_ms"]
    h_loss = 1.0 - (h_recv / h_sent) if h_sent else 0.0
    b_loss = 1.0 - (b_recv / b_sent) if b_sent else 0.0

    if th["congested"]:
        rows = [
            {"label": "HIGH 时延 < BE 时延", "theory": "HIGH < BE",
             "measured": round(h_e2e, 1), "unit": "ms",
             "error": round(h_e2e - b_e2e, 1), "tolerance": None,
             "pass": h_e2e < b_e2e},
            {"label": "HIGH 丢包 < BE 丢包", "theory": "HIGH < BE",
             "measured": round(h_loss, 3), "unit": "",
             "error": round(h_loss - b_loss, 3), "tolerance": None,
             "pass": h_loss < b_loss},
            {"label": "尽力流承担拥塞丢包", "theory": "BE loss > 0",
             "measured": round(b_loss, 3), "unit": "",
             "error": None, "tolerance": None, "pass": b_loss > 0},
        ]
        concl = (f"总负载 {th['load_pps']:.0f} pps > 容量 {th['cap_pps']:.0f} pps"
                 f"（拥塞）：HIGH e2e {h_e2e:.1f} ms / loss {h_loss:.3f}，"
                 f"BE e2e {b_e2e:.1f} ms / loss {b_loss:.3f}"
                 " —— 拥塞代价由尽力流承担。")
    else:
        rows = [
            {"label": "畅通：HIGH ≈ BE 时延", "theory": "≤ BE×1.5",
             "measured": round(h_e2e, 1), "unit": "ms",
             "error": round(h_e2e - b_e2e, 1),
             "tolerance": round(b_e2e * 0.5, 1),
             "pass": h_e2e <= b_e2e * 1.5 + 1},
            {"label": "HIGH 零丢包", "theory": 0,
             "measured": h_sent - h_recv, "unit": "包",
             "error": h_sent - h_recv, "tolerance": 0, "pass": h_recv == h_sent},
            {"label": "畅通：BE 零丢包", "theory": 0,
             "measured": b_sent - b_recv, "unit": "包",
             "error": b_sent - b_recv, "tolerance": 0, "pass": b_recv == b_sent},
        ]
        concl = (f"总负载 {th['load_pps']:.0f} pps ≤ 容量 {th['cap_pps']:.0f} pps"
                 f"（畅通）：HIGH {h_e2e:.1f} ms，BE {b_e2e:.1f} ms"
                 " —— 优先级在非拥塞网络中不显式起作用。")
    ok = _rows_pass(rows)
    return {"verdict": rows, "all_pass": ok,
            "conclusion": concl + ("" if ok else "（部分判据未通过）"),
            "measured": {"high": {"e2e_ms": h_e2e, "loss": h_loss,
                                  "sent": h_sent, "recv": h_recv},
                         "be": {"e2e_ms": b_e2e, "loss": b_loss,
                                "sent": b_sent, "recv": b_recv}}}


# ----------------------------------------------------------------------
# E5 路由算法对比（最短时延 vs 负载感知）
# ----------------------------------------------------------------------

E5_INPUTS = [
    {"key": "src_pps", "label": "源速率", "type": "int", "min": 300, "max": 800,
     "step": 50, "default": 600, "unit": "pps", "tip": "总业务量（两路均可达）"},
    {"key": "a_cap_pps", "label": "捷径容量", "type": "int", "min": 200, "max": 600,
     "step": 50, "default": 400, "unit": "pps", "tip": "A 路（低时延瓶颈）容量"},
]

E5_NODES = ["src", "Ha", "Hb1", "Hb2", "GS"]
E5_EDGES = [("src", "Ha", "isl", 0.002), ("Ha", "GS", "bn", 0.004),
            ("src", "Hb1", "isl", 0.003),
            ("Hb1", "Hb2", "isl", 0.004), ("Hb2", "GS", "isl", 0.004)]


def _e5_theory(p):
    lam, cap = float(p["src_pps"]), float(p["a_cap_pps"])
    congested = lam > cap
    return {"congested": congested,
            "loss_delay": max(0.0, 1.0 - cap / lam) if congested else 0.0,
            "loss_load_aware": 0.0}


async def _e5_run_one(metric, p, seed, on_progress, cancel_check, stage, frac):
    eng = _mk_engine(seed=seed, capacity_extra={"bn": p["a_cap_pps"] * PKT_BITS},
                     routing_metric=metric, route_refresh_interval=0.5)
    eng.sync_topology(E5_NODES, E5_EDGES, transit=["Ha", "Hb1", "Hb2"])
    eng.sync_flows({"src": "GS"}, {"src": float(p["src_pps"])})

    # E5 不走 _advance 分块：refresh_interval=0.5 s 下分块会让路由
    # 随队列涨落反复翻转（翻振 → 瞬态丢包 ~8%）；整块推进时测量窗
    # 内路由只按预热末队列状态计算一次，负载感知稳定绕行、近零丢包。
    # E5 仅 ~30 k 包（≈0.3 s 墙钟），无事件循环饥饿问题。
    eng.advance(10.0)
    await _checkpoint(cancel_check)
    if on_progress:
        on_progress({"stage": stage, "progress": frac, "note":
                     f"{'负载感知' if metric == 'load_aware' else '最短时延'}路由运行中"})
    base_sent = eng.n_generated["src"]
    base_recv = eng.n_delivered["GS"]
    base_drop = eng.total_dropped
    eng.advance(40.0)
    await _checkpoint(cancel_check)
    s = eng.snapshot(30.0)["summary"]
    sent = eng.n_generated["src"] - base_sent
    recv = eng.n_delivered["GS"] - base_recv
    drop = eng.total_dropped - base_drop
    return {"loss": (drop / sent) if sent else 0.0,
            "e2e_ms": s["avg_e2e_latency_ms"],
            "sent": sent, "recv": recv, "drop": drop}


async def _run_e5(p, on_progress, cancel_check, seed=17):
    th = _e5_theory(p)

    dly = await _e5_run_one("delay", p, seed, on_progress, cancel_check,
                            "run_delay", 0.5)
    la = await _e5_run_one("load_aware", p, seed, on_progress, cancel_check,
                           "run_load_aware", 0.95)

    tol = 0.05
    rows = [
        {"label": "最短时延路由：拥塞丢包率", "theory": round(th["loss_delay"], 3),
         "measured": round(dly["loss"], 3), "unit": "",
         "error": round(dly["loss"] - th["loss_delay"], 3), "tolerance": tol,
         "pass": abs(dly["loss"] - th["loss_delay"]) <= tol},
        {"label": "负载感知路由：绕行丢包率", "theory": th["loss_load_aware"],
         "measured": round(la["loss"], 3), "unit": "",
         "error": round(la["loss"], 3), "tolerance": 0.02,
         "pass": la["loss"] <= 0.02},
    ]
    if th["congested"]:
        rows.append({
            "label": "负载感知 e2e < 最短时延 e2e", "theory": "绕行更低",
            "measured": round(la["e2e_ms"], 1), "unit": "ms",
            "error": round(la["e2e_ms"] - dly["e2e_ms"], 1), "tolerance": None,
            "pass": la["e2e_ms"] < dly["e2e_ms"]})
    else:
        rows.append({
            "label": "畅通：两路由 e2e 相当（±5%）", "theory": "≈ 相等",
            "measured": round(la["e2e_ms"], 1), "unit": "ms",
            "error": round(la["e2e_ms"] - dly["e2e_ms"], 1), "tolerance": None,
            "pass": abs(la["e2e_ms"] - dly["e2e_ms"])
            <= 0.05 * max(la["e2e_ms"], dly["e2e_ms"], 1.0)})
    ok = _rows_pass(rows)
    if th["congested"]:
        concl = (f"源 {p['src_pps']} pps > 捷径容量 {p['a_cap_pps']} pps："
                 f"最短时延路由死守捷径，丢包 {dly['loss']:.1%}、"
                 f"e2e {dly['e2e_ms']:.0f} ms；负载感知路由检测到排队后"
                 f"绕行长路，丢包 {la['loss']:.1%}、e2e {la['e2e_ms']:.0f} ms。")
    else:
        concl = (f"源 {p['src_pps']} pps ≤ 捷径容量 {p['a_cap_pps']} pps（畅通）："
                 f"两种路由均走捷径，丢包 0，负载感知无优势。"
                 f"把源速率调高重跑可观察拥塞差异。")
    return {"verdict": rows, "all_pass": ok,
            "conclusion": concl + ("" if ok else "（部分判据未通过）"),
            "measured": {"delay": dly, "load_aware": la}}


# ----------------------------------------------------------------------
# E6 星座规模探索（网格拓扑：跳数与端到端时延）
# ----------------------------------------------------------------------

E6_INPUTS = [
    {"key": "planes", "label": "轨道平面数 P", "type": "int", "min": 2, "max": 6,
     "step": 1, "default": 3, "unit": "个", "tip": " Walker 星座平面维度"},
    {"key": "sats_per_plane", "label": "每平面卫星数 M", "type": "int", "min": 4,
     "max": 12, "step": 1, "default": 6, "unit": "颗",
     "tip": "同平面环内相位分布"},
]

E6_ISL_MS = 8.0                    # 每跳 ISL 传播时延
E6_ACCESS_MS = 5.0                  # UAV 上行接入
E6_DOWN_MS = 6.0                    # GS 下行


def _e6_theory(p):
    planes, m = p["planes"], p["sats_per_plane"]
    hops = (planes - 1) + m // 2
    return {"hops": hops, "n_sats": planes * m,
            "e2e_ms": E6_ACCESS_MS + hops * E6_ISL_MS + E6_DOWN_MS}


async def _run_e6(p, on_progress, cancel_check, seed=6):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e6_theory(p)
    planes, m = p["planes"], p["sats_per_plane"]
    nodes = ["UAV", "GS"] + [f"S{q}_{r}" for q in range(planes)
                             for r in range(m)]
    edges = []
    for q in range(planes):
        for r in range(m):
            edges.append((f"S{q}_{r}", f"S{q}_{(r+1) % m}", "isl",
                          E6_ISL_MS / 1000.0))
            if q + 1 < planes:                       # 平面链（不闭合）
                edges.append((f"S{q}_{r}", f"S{q+1}_{r}", "isl",
                              E6_ISL_MS / 1000.0))
    edges.append(("UAV", "S0_0", "sul", E6_ACCESS_MS / 1000.0))
    edges.append((f"S{planes-1}_{m // 2}", "GS", "gsl", E6_DOWN_MS / 1000.0))

    eng = _mk_engine(seed=seed)
    eng.sync_topology(nodes, edges,
                      transit=[n for n in nodes if n.startswith("S")])
    eng.sync_flows({"UAV": "GS"}, {"UAV": 20.0})     # 轻载，隔离路由效应

    prog("warmup", 0.3, f"{planes}×{m} 网格拓扑（{planes * m} 星）")
    await _advance(eng, 10.0, cancel_check)
    prog("measuring", 0.9, "30 s 测量窗")
    await _advance(eng, 30.0, cancel_check)
    s = eng.snapshot(30.0)["summary"]
    e2e = s["avg_e2e_latency_ms"]
    hops_inf = round((e2e - E6_ACCESS_MS - E6_DOWN_MS) / E6_ISL_MS)

    rows = [
        {"label": "端到端平均时延", "theory": round(th["e2e_ms"], 2),
         "measured": round(e2e, 2), "unit": "ms",
         "error": round(e2e - th["e2e_ms"], 2), "tolerance": 1.0,
         "pass": abs(e2e - th["e2e_ms"]) <= 1.0},
        {"label": "ISL 跳数（曼哈顿最短路）", "theory": th["hops"],
         "measured": hops_inf, "unit": "跳",
         "error": hops_inf - th["hops"], "tolerance": 0,
         "pass": hops_inf == th["hops"]},
        {"label": "轻载零丢包", "theory": 0, "measured": s["pkts_dropped"],
         "unit": "包", "error": s["pkts_dropped"], "tolerance": 0,
         "pass": s["pkts_dropped"] == 0},
    ]
    ok = _rows_pass(rows)
    return {"verdict": rows, "all_pass": ok,
            "conclusion": (
                f"{planes}×{m} 网格（{planes * m} 星）：实测 e2e {e2e:.2f} ms"
                f" = 接入 {E6_ACCESS_MS:.0f} + {hops_inf} 跳 × {E6_ISL_MS:.0f}"
                f" + 下行 {E6_DOWN_MS:.0f}，与曼哈顿最短路"
                f"（{planes - 1} + ⌊{m}/2⌋ = {th['hops']} 跳）一致。"
                + ("" if ok else " 请检查拓扑规模设置。")),
            "measured": s}


# ----------------------------------------------------------------------
# E7 链路预算与雨衰（Ka 波段容量降级）
# ----------------------------------------------------------------------

E7_INPUTS = [
    {"key": "rain_db", "label": "雨衰深度", "type": "float", "min": 0, "max": 10,
     "step": 0.5, "default": 5, "unit": "dB", "tip": "Ka 波段（20/30 GHz）暴雨衰减"},
    {"key": "src_pps", "label": "业务速率", "type": "int", "min": 5000,
     "max": 40000, "step": 1000, "default": 20000, "unit": "pps",
     "tip": "晴天 GSL 容量 83 kpps"},
]

E7_GSL_CLEAR = 1e9                  # 晴天下行容量（bps）


def _e7_theory(p):
    c_eff = E7_GSL_CLEAR / (10.0 ** (p["rain_db"] / 10.0))
    cap_pps = c_eff / PKT_BITS
    lam = float(p["src_pps"])
    return {"cap_eff_bps": c_eff, "cap_eff_pps": cap_pps, "lam_pps": lam,
            "congested": lam > cap_pps,
            "loss": max(0.0, 1.0 - cap_pps / lam)}


async def _run_e7(p, on_progress, cancel_check, seed=8):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e7_theory(p)
    eng = _mk_engine(seed=seed,
                     capacity_extra={"gsl": th["cap_eff_bps"]})
    eng.sync_topology(["UAV", "Sat", "GS"],
                      [("UAV", "Sat", "sul", 0.003),
                       ("Sat", "GS", "gsl", 0.006)],
                      transit=["Sat"])
    eng.sync_flows({"UAV": "GS"}, {"UAV": float(p["src_pps"])})

    prog("warmup", 0.25, f"雨衰 {p['rain_db']} dB → 有效容量 "
                         f"{th['cap_eff_pps'] / 1000:.1f} kpps")
    await _advance(eng, 15.0, cancel_check)
    base_sent = eng.n_generated["UAV"]
    base_recv = eng.n_delivered["GS"]
    base_drop = eng.total_dropped
    prog("measuring", 0.9, "30 s 测量窗")
    await _advance(eng, 45.0, cancel_check)
    s = eng.snapshot(30.0)["summary"]
    sent = eng.n_generated["UAV"] - base_sent
    recv = eng.n_delivered["GS"] - base_recv
    drop = eng.total_dropped - base_drop
    loss = drop / sent if sent else 0.0
    e2e = s["avg_e2e_latency_ms"]

    if th["congested"]:
        rows = [
            {"label": "雨衰拥塞判定（实测丢包 > 0）", "theory": "拥塞",
             "measured": round(loss, 3), "unit": "",
             "error": None, "tolerance": None, "pass": loss > 0.001},
            {"label": "拥塞丢包率 1 − C_eff/λ", "theory": round(th["loss"], 3),
             "measured": round(loss, 3), "unit": "",
             "error": round(loss - th["loss"], 3), "tolerance": 0.05,
             "pass": abs(loss - th["loss"]) <= 0.05},
            {"label": "拥塞排队抬升时延（e2e > 传播 9 ms）", "theory": "> 9",
             "measured": round(e2e, 1), "unit": "ms",
             "error": None, "tolerance": None, "pass": e2e > 9.5},
        ]
        concl = (f"雨衰 {p['rain_db']} dB 把 1 Gbps 下行压到 "
                 f"{th['cap_eff_pps'] / 1000:.1f} kpps < 业务 {p['src_pps'] / 1000:.0f}"
                 f" kpps：丢包 {loss:.1%}（理论 {th['loss']:.1%}），"
                 f"排队使 e2e 升至 {e2e:.1f} ms。")
    else:
        rows = [
            {"label": "链路可用判定（实测零丢包）", "theory": 0,
             "measured": round(loss, 3), "unit": "",
             "error": None, "tolerance": None, "pass": loss <= 0.001},
            {"label": "畅通时延 ≈ 传播 9 ms", "theory": 9.0,
             "measured": round(e2e, 1), "unit": "ms",
             "error": round(e2e - 9.0, 1), "tolerance": 1.0,
             "pass": abs(e2e - 9.0) <= 1.0},
            {"label": "容量余量（λ / C_eff）", "theory": "≤ 1",
             "measured": round(th["lam_pps"] / th["cap_eff_pps"], 2),
             "unit": "", "error": None, "tolerance": None,
             "pass": th["lam_pps"] <= th["cap_eff_pps"]},
        ]
        concl = (f"雨衰 {p['rain_db']} dB 后有效容量 "
                 f"{th['cap_eff_pps'] / 1000:.1f} kpps 仍 ≥ 业务 "
                 f"{p['src_pps'] / 1000:.0f} kpps：链路可用，零丢包，"
                 f"e2e {e2e:.1f} ms。把雨衰调高重跑可找到中断门限。")
    ok = _rows_pass(rows)
    return {"verdict": rows, "all_pass": ok,
            "conclusion": concl + ("" if ok else "（部分判据未通过）"),
            "measured": {"loss": loss, "e2e_ms": e2e, "sent": sent,
                         "recv": recv}}


# ----------------------------------------------------------------------
# 实验注册表（含 guide / topology，随 catalog 下发给前端实验台）
# ----------------------------------------------------------------------

EXPERIMENTS = {
    "E1": {
        "id": "E1", "name": "时延分解与对账",
        "summary": "三跳链路轻载：端到端时延 = Σ传播时延 + Σ发送时延",
        "difficulty": "入门", "minutes": 45,
        "theory_note": "e2e ≈ 21 ms(传播) + 发送时延(随包长)",
        "inputs": E1_INPUTS, "runner": _run_e1, "theory": _e1_theory,
        "guide": {
            "objective": "掌握端到端时延的两个物理分量（传播 / 发送），"
                         "理解轻载链路下排队项趋近于零的工程近似。",
            "principle": "e2e ≈ Σ传播时延 + Σ发送时延\n"
                         "传播 21 ms 由拓扑固定（5+10+6 ms）；\n"
                         "发送时延 = 包长 × (1/500M + 1/10G + 1/1G) s/bit，"
                         "随包长线性增长。\n拖动包长滑杆，理论预览即时联动。",
            "steps": ["阅读原理，分清两个时延分量",
                      "调整业务速率与包长参数",
                      "运行实验，观察 30 s 仿真推进",
                      "对照对账表，误差应 ≤ 1 ms",
                      "下载报告并回答思考题"],
            "questions": ["把速率提到 50 pps 仍是轻载吗？排队项何时不可忽略？",
                          "包长 9000 B 时发送时延是多少？与 21 ms 传播相比呢？",
                          "为什么说卫星网络“带宽便宜、时延昂贵”？"],
        },
        "topology": {
            "nodes": [{"id": "UAV-1", "type": "uav"}, {"id": "Sat-A", "type": "sat"},
                      {"id": "Sat-B", "type": "sat"}, {"id": "GS-1", "type": "gs"}],
            "edges": [{"a": "UAV-1", "b": "Sat-A", "label": "SUL 5ms"},
                      {"a": "Sat-A", "b": "Sat-B", "label": "ISL 10ms"},
                      {"a": "Sat-B", "b": "GS-1", "label": "GSL 6ms"}],
        },
    },
    "E2": {
        "id": "E2", "name": "M/D/1 排队模型",
        "summary": "泊松到达 + 确定服务：Wq = ρs / 2(1−ρ)，稳态测量",
        "difficulty": "基础", "minutes": 45,
        "theory_note": "Wq = ρs/2(1−ρ)，e2e ≈ 传播6 + 服务6 + Wq",
        "inputs": E2_INPUTS, "runner": _run_e2, "theory": _e2_theory,
        "guide": {
            "objective": "验证 M/D/1 平均排队时延公式（Pollaczek–Khinchine 特例），"
                         "掌握“预热 + 测量窗”的稳态仿真方法学。",
            "principle": "瓶颈 2 Mbps、包 1500 B → 服务时间 s = 6 ms。\n"
                         "Wq = ρ·s / (2·(1−ρ))：ρ=0.8 → 12 ms，"
                         "ρ=0.95 → 57 ms（悬崖！）。\n"
                         "e2e ≈ 传播 6 + 服务 6 + Wq。\n"
                         "预热期数据被丢弃——排队系统稳态测量的标准做法。",
            "steps": ["设置 ρ 与测量窗口，观察理论预览联动",
                      "运行实验（60 s 预热 + 测量窗推进）",
                      "核对 e2e / 利用率 / 稳态丢包三行判据",
                      "把 ρ 拉到 0.95 重跑，体会 1/(1−ρ) 悬崖效应",
                      "下载报告并回答思考题"],
            "questions": ["预热期数据为何要丢弃？直接从 t=0 统计会偏大还是偏小？",
                          "ρ→1 时 Wq 如何发散？对容量规划有什么启示？",
                          "M/M/1 的 Wq 为什么是 M/D/1 的两倍？"],
        },
        "topology": {
            "nodes": [{"id": "src", "type": "uav"}, {"id": "R1", "type": "router"},
                      {"id": "GS", "type": "gs"}],
            "edges": [{"a": "src", "b": "R1", "label": "10Gbps"},
                      {"a": "R1", "b": "GS", "label": "瓶颈 2Mbps"}],
        },
    },
    "E3": {
        "id": "E3", "name": "链路切换丢包",
        "summary": "LEO 切换瞬间：尖峰 = 队列容量 + 在途 1 包",
        "difficulty": "进阶", "minutes": 45,
        "theory_note": "尖峰 = Q(队列) + 1(在途) = 201 包",
        "inputs": E3_INPUTS, "runner": _run_e3, "theory": _e3_theory,
        "guide": {
            "objective": "理解 LEO 卫星高速运动导致链路切换的丢包机理，"
                         "定量推导切换尖峰并区分 handover / congestion 口径。",
            "principle": "切换瞬间：在队 Q 包被拆除 + 在途 1 包丢失"
                         " → 尖峰 = Q + 1。\n超速源（远超 1 Mbps 上行）在 10 s 内"
                         "堆满队列后触发切换，流量改走 B 路。\n虚线为备选路径，"
                         "运行到切换阶段拓扑会自动切换。",
            "steps": ["设置队列容量与源速率",
                      "运行实验：堆积 → 切换 → 恢复三阶段",
                      "核对尖峰 = Q + 1 与 handover 口径一致性",
                      "把队列容量改 100 重跑，对比尖峰变化",
                      "下载报告并回答思考题"],
            "questions": ["队列容量减半，尖峰如何变？加大缓存是治本还是治标？",
                          "make-before-break（先建后断）切换如何消除尖峰？",
                          "切换丢包与拥塞丢包在统计口径上如何区分？"],
        },
        "topology": {
            "nodes": [{"id": "UAV-1", "type": "uav"}, {"id": "Sat-A", "type": "sat"},
                      {"id": "Sat-B", "type": "sat"}, {"id": "GS-1", "type": "gs"}],
            "edges": [{"a": "UAV-1", "b": "Sat-A", "label": "SUL"},
                      {"a": "Sat-A", "b": "GS-1", "label": "GSL"},
                      {"a": "UAV-1", "b": "Sat-B", "label": "SUL", "alt": True},
                      {"a": "Sat-B", "b": "GS-1", "label": "GSL", "alt": True}],
            "switch_label": "切换：A 路 → B 路",
        },
    },
    "E4": {
        "id": "E4", "name": "QoS 严格优先级",
        "summary": "共享瓶颈：高优先受保护，尽力流承担拥塞代价",
        "difficulty": "进阶", "minutes": 45,
        "theory_note": "总负载 > 容量 ⇒ 拥塞；HIGH 始终先发",
        "inputs": E4_INPUTS, "runner": _run_e4, "theory": _e4_theory,
        "guide": {
            "objective": "观察严格优先级调度在拥塞下的保护作用与代价，"
                         "理解 QoS 保障的边界：优先级不创造容量。",
            "principle": "瓶颈容量 C（pps）决定世界：\n"
                         "总负载 > C：拥塞 → BE 承担排队+丢包，HIGH 被保护；\n"
                         "总负载 ≤ C：畅通 → 两类流表现接近。\n"
                         "左侧理论预览会实时判定当前参数是否拥塞。",
            "steps": ["设置两流速率与瓶颈容量",
                      "看理论预览判断负载与容量的关系",
                      "运行实验（55 s 预热 + 5 s 测量）",
                      "对比 HIGH / BE 的时延与丢包",
                      "把 low_pps 调低到不拥塞，观察判据自动切换"],
            "questions": ["HIGH 80 pps + BE 40 pps @ 2 Mbps 还会拥塞吗？",
                          "什么情况下优先级失效？QoS 的本质是什么？",
                          "严格优先级会饿死 BE，WFQ 如何缓解？"],
        },
        "topology": {
            "nodes": [{"id": "srcH", "type": "uav"}, {"id": "srcL", "type": "uav"},
                      {"id": "R1", "type": "router"}, {"id": "GS", "type": "gs"}],
            "edges": [{"a": "srcH", "b": "R1", "label": "HIGH"},
                      {"a": "srcL", "b": "R1", "label": "BE"},
                      {"a": "R1", "b": "GS", "label": "共享瓶颈"}],
        },
    },
    "E5": {
        "id": "E5", "name": "路由算法对比",
        "summary": "最短时延 vs 负载感知：拥塞时绕行长路反而更快",
        "difficulty": "进阶", "minutes": 45,
        "theory_note": "λ > 捷径容量：最短时延丢 (λ−C)/λ，负载感知绕行近零",
        "inputs": E5_INPUTS, "runner": _run_e5, "theory": _e5_theory,
        "guide": {
            "objective": "理解静态最短路径路由在拥塞下的局限，"
                         "以及负载感知（拥塞敏感）路由如何用少量传播时延"
                         "换取排队时延与丢包的大幅下降。",
            "principle": "最短时延路由：Dijkstra 权重 = 传播时延，"
                         "对队列堆积视而不见。\n"
                         "负载感知路由：权重 = 传播 × (1 + 9×队列填充率)，"
                         "瓶颈半满时权重 ×5.5 → 绕行 3 跳长路。\n"
                         "结论：拥塞时「最短路」不等于「最快路」。",
            "steps": ["设置源速率与捷径容量（λ > C 才有对比意义）",
                      "运行实验：先跑最短时延、再跑负载感知",
                      "对比两行丢包率与 e2e 时延",
                      "把源速率调到 ≤ 捷径容量重跑，观察差异消失",
                      "下载报告并回答思考题"],
            "questions": ["为什么拥塞时「最短路」不再是「最快路」？",
                          "负载感知路由依赖什么信息？这些信息如何获得？",
                          "路由震荡（反复切换路径）如何避免？与刷新周期有何关系？"],
        },
        "topology": {
            "nodes": [{"id": "src", "type": "uav"}, {"id": "Ha", "type": "router"},
                      {"id": "Hb1", "type": "router"}, {"id": "Hb2", "type": "router"},
                      {"id": "GS", "type": "gs"}],
            "edges": [{"a": "src", "b": "Ha", "label": "2ms"},
                      {"a": "Ha", "b": "GS", "label": "捷径 4ms"},
                      {"a": "src", "b": "Hb1", "label": "3ms", "alt": True},
                      {"a": "Hb1", "b": "Hb2", "label": "4ms", "alt": True},
                      {"a": "Hb2", "b": "GS", "label": "4ms", "alt": True}],
        },
    },
    "E6": {
        "id": "E6", "name": "星座规模探索",
        "summary": "P×M 网格：跳数 = (P−1) + ⌊M/2⌋，e2e = 11 + 8×跳数",
        "difficulty": "基础", "minutes": 45,
        "theory_note": "规模决定路径跳数；环内取短弧使时延随 M 减半增长",
        "inputs": E6_INPUTS, "runner": _run_e6, "theory": _e6_theory,
        "guide": {
            "objective": "把 Walker 星座抽象为网格拓扑，理解轨道平面数 P 与"
                         "每平面卫星数 M 两个维度如何决定端到端路径跳数与时延。",
            "principle": "同平面星间链路成环（相位方向），跨平面相邻连接"
                         "（轨道方向，不闭合）。\n"
                         "UAV 接入 (0,0)，GS 接入 (P−1, M/2)：\n"
                         "最短跳数 = (P−1) + ⌊M/2⌋（环内走短弧）。\n"
                         "e2e = 接入 5 + 跳数×ISL 8 + 下行 6 ms。",
            "steps": ["设置 P 与 M，观察理论预览的跳数与 e2e",
                      "运行实验，核对时延与跳数反推值",
                      "把 M 翻倍（6→12）重跑：跳数只加 3 而非 6",
                      "把 P 翻倍（3→6）重跑：跳数线性加 3",
                      "下载报告并回答思考题"],
            "questions": ["为什么环内最短弧使时延随 M 只按 ⌊M/2⌋ 增长？",
                          "P 和 M 哪个维度对时延影响更大？对覆盖连续性呢？",
                          "真实 Walker 星座还有哪些因素影响跳数（如缝、仰角约束）？"],
        },
        "topology": {
            "nodes": [{"id": "UAV", "type": "uav"}, {"id": "S", "type": "sat"},
                      {"id": "GS", "type": "gs"}],
            "edges": [{"a": "UAV", "b": "S", "label": "接入 5ms"},
                      {"a": "S", "b": "S", "label": "网格 ISL 8ms/跳"},
                      {"a": "S", "b": "GS", "label": "下行 6ms"}],
            "grid_hint": "P×M 网格按参数动态生成，此处为示意",
        },
    },
    "E7": {
        "id": "E7", "name": "链路预算雨衰",
        "summary": "Ka 波段雨衰 a dB → 容量 ×10^(−a/10)，找中断门限",
        "difficulty": "进阶", "minutes": 45,
        "theory_note": "晴天 83 kpps；10 dB 雨衰只剩 8.3 kpps",
        "inputs": E7_INPUTS, "runner": _run_e7, "theory": _e7_theory,
        "guide": {
            "objective": "理解链路预算中功率余量与天气衰减的博弈，"
                         "定量计算雨衰导致的容量降级与可用性门限。",
            "principle": "Ka 波段（20/30 GHz）暴雨衰减可达 10 dB。\n"
                         "教学模型：C_eff = C_clear × 10^(−a/10)。\n"
                         "λ > C_eff → 拥塞：丢包率 = 1 − C_eff/λ，"
                         "排队抬升时延；λ ≤ C_eff → 链路可用。\n"
                         "容量规划需在「晴天余量浪费」与「雨天中断」间权衡。",
            "steps": ["设置雨衰深度与业务速率，看理论预览判定可用性",
                      "运行实验，核对拥塞判定与丢包率对账",
                      "逐步加大雨衰，找到本业务的中断门限 dB 值",
                      "把业务速率减半重跑，观察门限如何移动",
                      "下载报告并回答思考题"],
            "questions": ["为什么 Ka 波段的雨衰比 Ku 波段严重得多？",
                          "为对抗雨衰，工程上有哪些手段（功率余量/ ACM/更低频段）？",
                          "「可用性 99.9%」对容量规划意味着什么？"],
        },
        "topology": {
            "nodes": [{"id": "UAV", "type": "uav"}, {"id": "Sat", "type": "sat"},
                      {"id": "GS", "type": "gs"}],
            "edges": [{"a": "UAV", "b": "Sat", "label": "SUL 3ms"},
                      {"a": "Sat", "b": "GS", "label": "GSL 6ms（受雨衰）"}],
        },
    },
}


# ----------------------------------------------------------------------
# 预习测验题库（改进计划 W2）：答案/解析仅存核心侧，目录只下发题干与选项。
# ----------------------------------------------------------------------

QUIZZES = {
    "E1": [
        {"q": "端到端时延的两个主要物理分量是？",
         "options": ["传播时延 + 发送时延", "排队时延 + 处理时延",
                     "传播时延 + 报头开销", "发送时延 + 确认时延"],
         "answer": 0,
         "explain": "轻载链路下排队项趋于 0，e2e ≈ Σ传播 + Σ发送。"},
        {"q": "发送时延由什么决定？",
         "options": ["包长与链路带宽", "传播距离与光速",
                     "队列深度与缓存", "路由跳数"],
         "answer": 0,
         "explain": "发送时延 = 包长(bit) / 链路带宽，随包长线性增长。"},
        {"q": "为什么卫星网络常说“带宽便宜、时延昂贵”？",
         "options": ["光速有限导致传播时延大，而扩容只需加带宽",
                     "卫星带宽总是过剩", "地面站处理能力不足",
                     "包长无法增大"],
         "answer": 0,
         "explain": "数百至上千公里斜距使传播时延不可压缩，而带宽可工程扩容。"},
    ],
    "E2": [
        {"q": "M/D/1 的平均排队时延 Wq 公式是？",
         "options": ["ρs / 2(1−ρ)", "ρ / (1−ρ)", "s / (1−ρ)", "ρs / (1−ρ)"],
         "answer": 0,
         "explain": "Pollaczek–Khinchine 特例：确定服务比指数服务排队减半。"},
        {"q": "预热期数据为什么要丢弃？",
         "options": ["消除初始空系统的瞬态偏差，只测稳态",
                     "预热期没有丢包", "预热期包长不同",
                     "为了减少计算量"],
         "answer": 0,
         "explain": "从空队列开始的瞬态会低估排队时延，稳态测量须跳预热。"},
        {"q": "ρ → 1 时 Wq 如何变化？",
         "options": ["按 1/(1−ρ) 悬崖式发散", "线性增长",
                     "保持不变", "先升后降"],
         "answer": 0,
         "explain": "容量规划的要点：利用率不宜长期逼近 1。"},
    ],
    "E3": [
        {"q": "切换瞬间的丢包尖峰由什么组成？",
         "options": ["在队 Q 包 + 在途 1 包", "仅在途 1 包",
                     "重传超时包", "路由环路包"],
         "answer": 0,
         "explain": "链路拆除时在队包被 drain、在途包丢失，尖峰 = Q + 1。"},
        {"q": "handover 丢包与拥塞丢包的统计口径区别是？",
         "options": ["handover 计链路移除时被排空的包，拥塞计队列溢出",
                     "两者完全相同", "handover 只计在途包",
                     "拥塞丢包不计入总数"],
         "answer": 0,
         "explain": "分开计数才能定位丢包根因：切换事件还是过载。"},
        {"q": "哪种切换方式可以从机理上消除尖峰？",
         "options": ["make-before-break（先建后断）",
                     "break-before-make（先断后建）",
                     "直接丢弃队列", "增大包长"],
         "answer": 0,
         "explain": "新链路就绪后再排空旧队列，包可转移而非丢弃。"},
    ],
    "E4": [
        {"q": "严格优先级在拥塞下保护高优先流的机制是？",
         "options": ["高优先包始终先出队，缓冲满时低优先先丢",
                     "高优先包更小", "高优先走独立链路",
                     "低优先被限速"],
         "answer": 0,
         "explain": "多级优先队列 + 严格优先调度 + 低优先先丢。"},
        {"q": "QoS 优先级的本质边界是？",
         "options": ["重新分配拥塞代价，不创造容量",
                     "可以增加链路带宽", "可以消除排队",
                     "对所有流同等有效"],
         "answer": 0,
         "explain": "总负载 > 容量时总有人受损，优先级只决定谁受损。"},
        {"q": "严格优先级的主要副作用是？",
         "options": ["低优先流可能被饿死", "高优先时延变大",
                     "队列溢出更频繁", "路由震荡"],
         "answer": 0,
         "explain": "WFQ 等加权调度用带宽换公平来缓解饿死。"},
    ],
    "E5": [
        {"q": "拥塞时最短时延路由的问题在于？",
         "options": ["权重只算传播时延，对排队视而不见，流量持续涌入瓶颈",
                     "计算太慢", "不支持多跳", "不能转发 BE 流量"],
         "answer": 0,
         "explain": "静态权重不知道链路已拥塞，导致「最短路」变成「最慢路」。"},
        {"q": "负载感知路由缓解拥塞的机制是？",
         "options": ["把排队状态计入链路权重，拥塞路径权重升高促使绕行",
                     "增加链路带宽", "丢弃低优先包", "缩短传播距离"],
         "answer": 0,
         "explain": "权重 = 传播 × (1 + k×队列填充率)，瓶颈半满即显著绕行。"},
        {"q": "负载感知路由的典型副作用与对策是？",
         "options": ["路由震荡（流量迁移引起权重反转），需滞回/慢刷新抑制",
                     "时延必然上升", "无法收敛", "只能用于星型拓扑"],
         "answer": 0,
         "explain": "类似 E3 的刷新周期与 E5 的惩罚系数都是震荡控制手段。"},
    ],
    "E6": [
        {"q": "网格星座中端到端跳数由什么决定？",
         "options": ["两维坐标差的曼哈顿距离（环内取短弧）",
                     "卫星总数", "链路带宽", "业务速率"],
         "answer": 0,
         "explain": "跳数 = (P−1) + ⌊M/2⌋，环内最短弧使相位方向按半数增长。"},
        {"q": "每平面卫星数 M 翻倍，环内方向跳数如何变？",
         "options": ["约增一半（⌊2M/2⌋ − ⌊M/2⌋ ≈ M/2）", "翻倍",
                     "不变", "归零"],
         "answer": 0,
         "explain": "环内可双向走，最短弧长随 M 线性增长，故翻倍只加 M/2 跳。"},
        {"q": "增大星座规模对时延与覆盖的影响是？",
         "options": ["跳数与时延按维度缓慢增长，覆盖连续性与切换韧性提升",
                     "时延必然翻倍", "覆盖不变", "切换更少"],
         "answer": 0,
         "explain": "规模收益主要在覆盖/冗余；时延代价受网格几何约束。"},
    ],
    "E7": [
        {"q": "雨衰 a dB 使链路有效容量变为？",
         "options": ["C_clear × 10^(−a/10)", "C_clear − a", "C_clear × a",
                     "不变"],
         "answer": 0,
         "explain": "dB 是对数功率比，线性功率下降 10^(a/10) 倍。"},
        {"q": "业务速率固定时，链路中断的判据是？",
         "options": ["λ > C_eff（有效容量被雨衰压到业务速率以下）",
                     "雨衰 > 0 dB 就中断", "e2e > 10 ms 就中断",
                     "丢包率 > 50%"],
         "answer": 0,
         "explain": "中断门限随业务速率移动：速率减半，可容忍雨衰 +3 dB。"},
        {"q": "工程上对抗 Ka 波段雨衰的手段不包括？",
         "options": ["把卫星轨道降到雨云以下", "自适应编码调制 ACM",
                     "预留功率余量", "切换到低频段（Ku/C）"],
         "answer": 0,
         "explain": "轨道高度不可低到对流层；ACM/余量/频段切换都是常规手段。"},
    ],
}


def grade_quiz(exp_id, answers):
    """判预习测验。answers: {"0": idx, "1": idx, ...}（下标字符串 -> 选项序号）。"""
    qs = QUIZZES.get(exp_id)
    if qs is None:
        raise ExperimentNotFound(exp_id)
    answers = answers or {}
    detail = []
    n_correct = 0
    for i, q in enumerate(qs):
        try:
            picked = int(answers.get(str(i), answers.get(i, -1)))
        except (TypeError, ValueError):
            picked = -1
        ok = picked == q["answer"]
        n_correct += int(ok)
        detail.append({"index": i, "correct": ok,
                       "picked": picked, "answer": q["answer"],
                       "explain": q["explain"]})
    return {"score": round(SCORE_MAX["quiz"] * n_correct / len(qs), 1),
            "max": SCORE_MAX["quiz"], "n_correct": n_correct,
            "n_total": len(qs), "detail": detail}


def grade_questions(exp_id, answers):
    """判思考题：按非空作答比例给分。answers: {"0": 文本, ...}。"""
    spec = EXPERIMENTS.get(exp_id)
    if spec is None:
        raise ExperimentNotFound(exp_id)
    answers = answers or {}
    n_total = len(spec["guide"]["questions"])
    answered = sum(1 for i in range(n_total)
                   if str(answers.get(str(i), answers.get(i, "")) or "").strip())
    return {"score": round(SCORE_MAX["questions"] * answered / n_total, 1)
            if n_total else 0.0,
            "max": SCORE_MAX["questions"], "answered": answered,
            "n_total": n_total}


def compose_score(result, quiz_grade=None, question_grade=None):
    """合成 100 分制总分：仿真部分（已在 result 中）+ 预习 + 思考题。"""
    detail = list(result.get("score_detail", []))
    total = float(result.get("score", 0))
    if quiz_grade:
        detail.append({"item": "预习测验", "max": quiz_grade["max"],
                       "score": quiz_grade["score"]})
        total += quiz_grade["score"]
    if question_grade:
        detail.append({"item": "思考题作答", "max": question_grade["max"],
                       "score": question_grade["score"]})
        total += question_grade["score"]
    return {"score": round(min(total, 100.0), 1), "max": 100, "detail": detail}


def experiment_catalog():
    """目录（含 inputs/guide/topology/quiz 题干/评分权重），随 simulation_init 下发。"""
    out = []
    for spec in EXPERIMENTS.values():
        quiz = [{"q": q["q"], "options": list(q["options"])}
                for q in QUIZZES.get(spec["id"], [])]
        out.append({
            "exp_id": spec["id"], "name": spec["name"],
            "summary": spec["summary"], "theory_note": spec["theory_note"],
            "difficulty": spec["difficulty"], "minutes": spec["minutes"],
            "params": _default_params(spec["inputs"]),
            "inputs": spec["inputs"],
            "guide": spec["guide"], "topology": spec["topology"],
            "quiz": quiz, "score_max": SCORE_MAX,
        })
    return out


async def run_experiment(exp_id, run_params=None, on_progress=None,
                         cancel_check=None):
    """在沙箱中运行指定实验；run_params 越界自动夹紧到 Schema 范围。

    保留键 ``_seed``（考核模式由核心下发）覆盖该实验的默认随机种子；
    其余非 Schema 键被忽略。
    """
    spec = EXPERIMENTS.get(exp_id)
    if spec is None:
        raise ExperimentNotFound(exp_id)
    params = _sanitize_params(spec["inputs"], run_params)
    seed = None
    if isinstance(run_params, dict) and "_seed" in run_params:
        try:
            seed = int(run_params["_seed"])
        except (TypeError, ValueError):
            seed = None
    try:
        if seed is None:
            out = await spec["runner"](params, on_progress, cancel_check)
        else:
            out = await spec["runner"](params, on_progress, cancel_check,
                                       seed=seed)
    except ExperimentCancelled:
        raise
    v_pts = _score_verdict(out["verdict"])
    e_pts = _score_explore(spec["inputs"], params)
    result = {
        "exp_id": exp_id, "name": spec["name"], "summary": spec["summary"],
        "theory_note": spec["theory_note"],
        "params_used": params,
        "verdict": out["verdict"], "conclusion": out["conclusion"],
        "all_pass": out["all_pass"],
        "score": round(v_pts + e_pts, 1),
        "score_detail": [
            {"item": "对账判定", "max": SCORE_MAX["verdict"], "score": v_pts},
            {"item": "参数探索", "max": SCORE_MAX["explore"], "score": e_pts,
             "note": ("使用非默认参数运行" if e_pts else
                      "默认参数运行，改动参数重跑可获探索分")},
        ],
    }
    for key, val in out.items():
        if key not in result:
            result[key] = val
    return result
