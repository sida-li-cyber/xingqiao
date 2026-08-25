"""星桥教学实验运行器 v2（P0 参数化版）。

在独立沙箱 PacketEngine 中构造 E1~E4 教学实验场景，支持：

  * 声明式 inputs Schema（范围/步进/默认值/单位/提示），
    experiment_run 命令可携带 params 覆盖默认值（越界自动夹紧）；
  * 理论值随参数动态计算，对账容差随统计难度自适应放宽；
  * 每实验附带 guide（目的/原理/步骤/思考题）与 topology
    （SVG 拓扑数据）随 simulation_init.experiments 下发，
    前端据此自动生成输入表单与实验台界面。

实验目录（默认参数）：

  E1 时延分解与对账   e2e ≈ 21 ms(传播) + 0.037 ms(发送) ≈ 21.037 ms
  E2 M/D/1 排队模型   Wq = ρs/2(1−ρ) = 12 ms，e2e ≈ 24 ms，ρ ≈ 0.8
  E3 链路切换丢包     尖峰 = 队列 200 + 在途 1 = 201 包
  E4 QoS 严格优先级   拥塞时 HIGH 时延/丢包 < BE 且 BE loss > 0

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


def _rows_pass(rows):
    return all(r["pass"] for r in rows)


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


async def _run_e1(p, on_progress, cancel_check):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e1_theory(p)
    eng = _mk_engine(seed=42, packet_size_bytes=p["pkt_bytes"])
    eng.sync_topology(
        ["UAV-1", "Sat-A", "Sat-B", "GS-1"],
        [("UAV-1", "Sat-A", "sul", 0.005),
         ("Sat-A", "Sat-B", "isl", 0.010),
         ("Sat-B", "GS-1", "gsl", 0.006)],
        transit=["Sat-A", "Sat-B"])
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": float(p["pps"])})

    prog("warmup", 0.1)
    eng.advance(10.0)
    await _checkpoint(cancel_check)
    prog("simulating", 0.6)
    eng.advance(20.0)
    await _checkpoint(cancel_check)
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


async def _run_e2(p, on_progress, cancel_check):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e2_theory(p)
    tol = max(3.0, 0.22 * th["wq_ms"])        # ρ 高时涨落大，容差放宽

    eng = _mk_engine(seed=99, capacity_extra={"bn": 2e6},
                     packet_size_bytes=1500)
    eng.sync_topology(
        ["src", "R1", "GS"],
        [("src", "R1", "isl", 0.001),          # 轻载接入
         ("R1", "GS", "bn", 0.005)],           # 2 Mbps 瓶颈 → s = 6 ms
        transit=["R1"])
    eng.sync_flows({"src": "GS"}, {"src": th["lam_pps"]})

    prog("warmup", 0.05, "预热 60 s（统计丢弃）")
    eng.advance(60.0)
    await _checkpoint(cancel_check)
    eng.snapshot(0.0)                          # 清空预热窗口统计
    base_drop = eng.total_dropped
    base_sent = eng.n_generated["src"]

    chunks, total = 12, p["window_s"]
    for i in range(chunks):
        eng.advance(60.0 + total * (i + 1) / chunks)
        await _checkpoint(cancel_check)
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


async def _run_e3(p, on_progress, cancel_check):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e3_theory(p)
    # route_refresh_interval=0：切换后路由立即重算，避免 link-gone 丢包
    # 混入统计、破坏 handover 口径。
    eng = _mk_engine(seed=7, capacity_extra={"up": 1e6},
                     queue_capacity_pkts=p["queue_pkts"],
                     route_refresh_interval=0.0)
    eng.sync_topology(E3_NODES, E3_EDGES_A + E3_EDGES_B,
                      transit=["Sat-A", "Sat-B"])
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": float(p["src_pps"])})

    prog("queueing", 0.3, "超速源堆积队列（10 s）")
    eng.advance(10.0)
    await _checkpoint(cancel_check)
    base_drop = eng.total_dropped
    base_handover = eng.total_handover_dropped
    base_delivered = eng.total_delivered

    eng.sync_topology(E3_NODES, E3_EDGES_B)    # 切换：A 路拆除
    # 突发结束：流量回落到常态 40 pps（B 路可承载），
    # 使切换后的丢包仅为切换尖峰本身，保证口径纯净。
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": 40.0})
    eng.advance(10.5)                          # 切换后 0.5 s（新路由恢复）
    await _checkpoint(cancel_check)
    handover_spike = eng.total_handover_dropped - base_handover
    drops_at_switch = eng.total_dropped - base_drop

    prog("restoring", 0.7, "切换完成，恢复期 10 s")
    eng.advance(20.5)
    await _checkpoint(cancel_check)
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


async def _run_e4(p, on_progress, cancel_check):
    def prog(stage, frac, note=""):
        if on_progress:
            on_progress({"stage": stage, "progress": frac, "note": note})

    th = _e4_theory(p)
    eng = _mk_engine(seed=11, capacity_extra={"bn": p["bottleneck_mbps"] * 1e6},
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
    eng.advance(55.0)
    await _checkpoint(cancel_check)
    eng.snapshot(0.0)                           # 清窗
    base = {s: eng.n_generated[s] for s in ("srcH", "srcL")}
    prio_base = {PRIO_HIGH: eng.n_delivered_prio[PRIO_HIGH],
                 PRIO_BEST_EFFORT: eng.n_delivered_prio[PRIO_BEST_EFFORT]}

    prog("measuring", 0.85, "测量窗 5 s")
    eng.advance(60.0)
    await _checkpoint(cancel_check)
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
}


def experiment_catalog():
    """目录（含 inputs/guide/topology），随 simulation_init 下发。"""
    out = []
    for spec in EXPERIMENTS.values():
        out.append({
            "exp_id": spec["id"], "name": spec["name"],
            "summary": spec["summary"], "theory_note": spec["theory_note"],
            "difficulty": spec["difficulty"], "minutes": spec["minutes"],
            "params": _default_params(spec["inputs"]),
            "inputs": spec["inputs"],
            "guide": spec["guide"], "topology": spec["topology"],
        })
    return out


async def run_experiment(exp_id, run_params=None, on_progress=None,
                         cancel_check=None):
    """在沙箱中运行指定实验；run_params 越界自动夹紧到 Schema 范围。"""
    spec = EXPERIMENTS.get(exp_id)
    if spec is None:
        raise ExperimentNotFound(exp_id)
    params = _sanitize_params(spec["inputs"], run_params)
    try:
        out = await spec["runner"](params, on_progress, cancel_check)
    except ExperimentCancelled:
        raise
    result = {
        "exp_id": exp_id, "name": spec["name"], "summary": spec["summary"],
        "theory_note": spec["theory_note"],
        "params_used": params,
        "verdict": out["verdict"], "conclusion": out["conclusion"],
        "all_pass": out["all_pass"],
    }
    for key, val in out.items():
        if key not in result:
            result[key] = val
    return result
