"""改进 #2 实验运行器验证：E1~E9 理论对账 + P0 参数化 + 评分闭环。

场景与判据源自（并应保持一致于）：
  E1 tests/test_packet_sim.py Test 1
  E2 tests/test_phase6.py     Test 8
  E3 tests/test_phase3.py    Test 3
  E4 tests/test_phase3.py    Test 4
  E5 tests/test_experiments.py（负载感知路由 vs 最短时延，双模式）
  E6 tests/test_experiments.py（网格拓扑跳数 = (P−1) + ⌊M/2⌋）
  E7 tests/test_experiments.py（雨衰容量 ×10^(−a/10) → 丢包 1−C_eff/λ）
  E8 tests/test_experiments.py（P0 故障诊断：探测观测边界 → 根因提交）
  E9 tests/test_experiments.py（P2 逆向设计：目标约束达标 + 迭代方案数）

P0 参数化：inputs Schema（范围/步进/默认）、params 越界夹紧、
理论值随参数动态计算（E1 包长 → 发送时延、E2 ρ → Wq、E3 队列 → 尖峰、
E4 负载/容量 → 拥塞判定双模式）；E8 为 str/text 文本型输入（仅清洗）。

评分闭环（改进计划阶段一）：对账判定 70（行可带权重，E8 诊断型 7/3/0）+
参数扫描 10 + 预习测验 10 + 思考题 10 = 100 分；题库答案不下发目录；
grade_quiz / grade_questions / compose_score 判分可复现（固定种子）。

P1（评分诚实性）：参数探索分按 sweep_key 取值覆盖度分档（默认单次 0 /
非默认单值 2 / 2 值 4 / 3 值 6 / ≥4 值且跨度 ≥40% 区间 10），历史
attempts 并入覆盖集合；run_experiment 结果携带 attempt 存档摘要。

I3（阶段三内容扩充）：E5~E7 + 考核种子保留键 ``_seed`` 覆盖默认种子。

Run:  pytest tests/test_experiments.py -v     (~40 s)
"""
import asyncio
import os
import sys

# Engine modules live in <root>/hypatia-master/satviz; tests in <root>/tests.
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

from experiments import (
    EXPERIMENTS, ExperimentNotFound, QUIZZES, SCORE_MAX,
    compose_score, experiment_catalog, grade_questions, grade_quiz,
    run_experiment,
)


def _run(exp_id, params=None, prior_attempts=None):
    progress = []

    def on_progress(update):
        progress.append((update.get("stage"), update.get("progress")))

    result = asyncio.run(run_experiment(
        exp_id, run_params=params, on_progress=on_progress,
        prior_attempts=prior_attempts))
    return result, progress


def _show(result):
    print(f"\n{result['exp_id']} {result['name']}: {result['conclusion']}")
    for r in result["verdict"]:
        print(f"  {r['label']}: theory={r['theory']} "
              f"measured={r['measured']} pass={r['pass']}")


def test_catalog_complete():
    cat = experiment_catalog()
    assert [c["exp_id"] for c in cat] == [
        "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"]
    for c in cat:
        assert c["name"] and c["summary"] and c["theory_note"]
        assert isinstance(c["params"], dict) and c["params"]
        # P0: Schema / 指导书 / 拓扑齐备，供实验台自动生成界面
        for f in c["inputs"]:
            if f.get("type") in ("str", "text"):
                # E8 文本型输入：默认值为字符串，无 min/max/step 夹紧
                assert isinstance(f["default"], str) and f["label"]
                continue
            assert f["min"] <= f["default"] <= f["max"]
            assert f["step"] > 0 and f["label"]
        g = c["guide"]
        assert g["objective"] and g["principle"] and len(g["steps"]) >= 4
        assert len(g["questions"]) >= 2        # E8/E9 思考题 2 题，其余 3 题
        t = c["topology"]
        assert len(t["nodes"]) >= 3 and len(t["edges"]) >= 2
        # 评分闭环（阶段一）：目录携带题库题干与评分权重，且不下发答案/解析
        assert c["score_max"] == SCORE_MAX
        assert len(c["quiz"]) == len(QUIZZES[c["exp_id"]]) >= 2  # E9 2 题
        for q in c["quiz"]:
            assert set(q) == {"q", "options"}
            assert len(q["options"]) >= 3


def test_e1_latency_decomposition():
    result, progress = _run("E1")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert abs(result["verdict"][0]["measured"] - 21.037) < 1.0
    assert progress
    # 评分（W1）：默认参数全通过 → 对账 70 + 探索 0
    assert result["score"] == 70.0
    detail = {d["item"]: d for d in result["score_detail"]}
    assert detail["对账判定"]["score"] == 70.0
    assert detail["参数探索"]["score"] == 0


def test_e1_parametrized_packet_size():
    result, _ = _run("E1", {"pkt_bytes": 9000})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert result["params_used"]["pkt_bytes"] == 9000
    # 9000 B → 发送时延 0.223 ms，e2e ≈ 21.223
    assert abs(result["verdict"][0]["measured"] - 21.223) < 1.0
    # P1 扫描评分：E1 的 sweep_key 是 pps，改包长不计入扫描覆盖
    #（旧“改任一参数即 10 分”语义已废弃）→ 探索 0 分，总分仍 70
    assert result["score"] == 70.0


def test_e2_md1_queueing():
    result, progress = _run("E2")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    # 与 tests/test_phase6.py Test 8 同源：实测应落在理论 24.0ms 附近
    assert abs(result["verdict"][0]["measured"] - 24.0) < 3.0


def test_e2_parametrized_rho():
    result, _ = _run("E2", {"rho": 0.6, "window_s": 60})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    # ρ=0.6 → Wq=4.5ms，e2e ≈ 16.5ms
    assert abs(result["verdict"][0]["theory"] - 16.51) < 0.1
    assert abs(result["verdict"][1]["measured"] - 0.6) < 0.05


def test_e3_handover_spike():
    result, progress = _run("E3")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    # 尖峰 = 队列容量 200 + 在途 1 = 201（tests/test_phase3.py Test 3 基线）
    assert result["verdict"][0]["measured"] == 201


def test_e3_parametrized_queue():
    result, _ = _run("E3", {"queue_pkts": 100})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert result["verdict"][0]["measured"] == 101


def test_e4_qos_priority():
    result, progress = _run("E4")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    # 高优先流在 60 pps/1 Mbps 瓶颈下丢包率应远低于尽力流
    m = result["measured"]
    assert m["high"]["loss"] < m["be"]["loss"]
    assert m["high"]["e2e_ms"] < m["be"]["e2e_ms"]


def test_e4_uncongested_mode():
    result, _ = _run("E4", {"high_pps": 20, "low_pps": 20,
                            "bottleneck_mbps": 2.0})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    m = result["measured"]
    assert m["high"]["loss"] == 0.0 and m["be"]["loss"] == 0.0


# ----------------------------------------------------------------------
# I3（阶段三内容扩充）：E5 路由对比 / E6 星座规模 / E7 链路预算雨衰
# ----------------------------------------------------------------------

def test_e5_routing_comparison_congested():
    """默认 600 pps > 捷径 400 pps：最短时延丢 (λ−C)/λ=33%，负载感知近零。"""
    result, progress = _run("E5")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    m = result["measured"]
    assert abs(m["delay"]["loss"] - 1 / 3) <= 0.05
    assert m["load_aware"]["loss"] <= 0.02
    assert m["load_aware"]["e2e_ms"] < m["delay"]["e2e_ms"]
    labels = [v["label"] for v in result["verdict"]]
    assert "负载感知 e2e < 最短时延 e2e" in labels
    stages = [s for s, _ in progress]
    assert "run_delay" in stages and "run_load_aware" in stages


def test_e5_uncongested_mode():
    """畅通（300 ≤ 400 pps）：两路由同走捷径，e2e 相当、零丢包。"""
    result, _ = _run("E5", {"src_pps": 300})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    m = result["measured"]
    assert m["delay"]["loss"] == 0.0 and m["load_aware"]["loss"] == 0.0
    labels = [v["label"] for v in result["verdict"]]
    assert "畅通：两路由 e2e 相当（±5%）" in labels


def test_e6_constellation_scale():
    """默认 3×6 = 18 星：跳数 = (P−1)+⌊M/2⌋ = 5，e2e = 5+5×8+6 = 51 ms。"""
    result, _ = _run("E6")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert result["verdict"][0]["theory"] == 51.0
    assert abs(result["verdict"][0]["measured"] - 51.0) <= 1.0
    assert result["verdict"][1]["measured"] == 5


def test_e6_parametrized_grid():
    """4×8 = 32 星：跳数 = 3 + 4 = 7，e2e = 5 + 7×8 + 6 = 67 ms。"""
    result, _ = _run("E6", {"planes": 4, "sats_per_plane": 8})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert result["verdict"][0]["theory"] == 67.0
    assert result["verdict"][1]["measured"] == 7
    assert result["params_used"]["planes"] == 4


def test_e7_rain_fade_clear_sky():
    """默认雨衰 5 dB：容量 26.4 kpps ≥ 业务 20 kpps → 链路可用零丢包。"""
    result, _ = _run("E7")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    labels = [v["label"] for v in result["verdict"]]
    assert "链路可用判定（实测零丢包）" in labels


def test_e7_rain_fade_congested():
    """雨衰 8 dB：容量 13.2 kpps < 业务 20 kpps → 丢包 1−C_eff/λ ≈ 34%。"""
    result, _ = _run("E7", {"rain_db": 8.0})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    loss_row = next(v for v in result["verdict"]
                    if v["label"] == "拥塞丢包率 1 − C_eff/λ")
    assert abs(loss_row["theory"] - 0.34) < 0.01
    assert loss_row["pass"]


def test_seed_reserved_key_overrides_default():
    """I4 考核：保留键 _seed 覆盖实验默认种子，且不进入 params_used。"""
    result, _ = _run("E1", {"pkt_bytes": 9000, "_seed": 777})
    assert result["all_pass"], result["conclusion"]
    assert "_seed" not in result["params_used"]
    assert abs(result["verdict"][0]["measured"] - 21.223) < 1.0
    # 同种子重跑结果一致（可复现评分）
    result2, _ = _run("E1", {"pkt_bytes": 9000, "_seed": 777})
    assert result2["verdict"][0]["measured"] == result["verdict"][0]["measured"]


def test_params_clamped_to_schema():
    result, _ = _run("E3", {"queue_pkts": 99999, "src_pps": 1})
    assert result["params_used"]["queue_pkts"] == 400   # max
    assert result["params_used"]["src_pps"] == 1000     # min
    # 400 容量同样应在 10 s 内堆满（±1：切换时刻可能无在途包）
    assert abs(result["verdict"][0]["measured"] - 401) <= 1


def test_unknown_experiment():
    try:
        asyncio.run(run_experiment("E99"))
    except ExperimentNotFound:
        pass
    else:
        raise AssertionError("E99 should not exist")


def test_cancel():
    from experiments import ExperimentCancelled

    def cancel_check():
        return True

    try:
        asyncio.run(run_experiment("E1", cancel_check=cancel_check))
    except ExperimentCancelled:
        pass
    else:
        raise AssertionError("cancel_check should abort the run")


# ----------------------------------------------------------------------
# 评分闭环：预习测验 / 思考题 / 总分合成（改进计划阶段一）
# ----------------------------------------------------------------------

def test_grade_quiz_all_correct():
    answers = {str(i): q["answer"] for i, q in enumerate(QUIZZES["E2"])}
    g = grade_quiz("E2", answers)
    assert g["n_correct"] == g["n_total"] == 3
    assert g["score"] == 10.0 and g["max"] == 10
    assert all(d["correct"] for d in g["detail"])
    # 解析随行返回（判分后才可见）
    assert all(d["explain"] for d in g["detail"])


def test_grade_quiz_partial_and_invalid():
    qs = QUIZZES["E3"]
    answers = {"0": qs[0]["answer"]}                       # 只答对一题，其余未答/错答
    answers["1"] = (qs[1]["answer"] + 1) % len(qs[1]["options"])
    answers["2"] = "not-an-int"
    g = grade_quiz("E3", answers)
    assert g["n_correct"] == 1
    assert g["score"] == round(10 / 3, 1)


def test_grade_quiz_unknown_experiment():
    try:
        grade_quiz("E99", {})
    except ExperimentNotFound:
        pass
    else:
        raise AssertionError("E99 should not exist")


def test_grade_questions_by_ratio():
    n = len(EXPERIMENTS["E4"]["guide"]["questions"])
    g = grade_questions("E4", {str(i): "答案" for i in range(n)})
    assert g == {"score": 10.0, "max": 10, "answered": n, "n_total": n}
    g2 = grade_questions("E4", {"0": "  "})               # 空白不算作答
    assert g2["score"] == 0.0 and g2["answered"] == 0


def test_compose_score_full_and_partial():
    result, _ = _run("E1", {"pkt_bytes": 9000})           # 仿真部分 70 分
    quiz = {"score": 10.0, "max": 10}
    qs = {"score": 10.0, "max": 10}
    total = compose_score(result, quiz, qs)
    assert total["score"] == 90.0 and total["max"] == 100
    items = [d["item"] for d in total["detail"]]
    assert items == ["对账判定", "参数探索", "预习测验", "思考题作答"]
    # 预习未做 / 思考题未答 → 只拿仿真部分 70 分（封顶 100）
    total2 = compose_score(result)
    assert total2["score"] == 70.0


# ----------------------------------------------------------------------
# P0 E8 链路故障诊断：探测观测 → 边界 → guess 提交 → 证据链判分
# 默认种子 8 → fault 下标 1+8%3=3 → L4=(S3,S4)（仅内部链路 L2/L3/L4）
# ----------------------------------------------------------------------

E8_PARAMS = {"probes": "S2,S4", "guess": "L4",
             "evidence": "S2 上游丢包 0% 干净，S4 下游丢包高，对比分界定位"}


def _e8_rows(result):
    return {r["label"]: r for r in result["verdict"]}


def test_e8_fault_located():
    """命中：S2 干净 / S4 劣化，guess=L4 正确 + 证据链有效 + 预算达标。"""
    result, progress = _run("E8", dict(E8_PARAMS))
    _show(result)
    assert result["all_pass"], result["conclusion"]
    obs = {o["node"]: o for o in result["observations"]}
    assert obs["S2"]["loss_pct"] < 5                 # 故障上游：干净
    assert obs["S4"]["loss_pct"] > 50                # 故障下游：劣化
    assert obs["S4"]["e2e_ms"] > obs["S2"]["e2e_ms"] * 10
    assert obs["S2"]["pkts_dropped"] == 0
    rows = _e8_rows(result)
    assert rows["根因链路定位"]["pass"]
    assert rows["根因链路定位"]["weight"] == 7
    assert rows["证据链完整性"]["pass"] and rows["证据链完整性"]["weight"] == 3
    assert rows["探测预算（≤3 个节点）"]["pass"]
    # 权重制判分：7+3 满权 → 70；E8 无 sweep_key → 探索 0
    assert result["score"] == 70.0
    d = next(d for d in result["score_detail"] if d["item"] == "参数探索")
    assert d["score"] == 0 and "诊断" in d["note"]
    # conclusion 描述观测边界但不泄露故障链路名；两阶段进度可见
    assert "L4" not in result["conclusion"] and "边界" in result["conclusion"]
    stages = [s for s, _ in progress]
    assert "warmup" in stages and "measuring" in stages


def test_e8_fault_missed():
    """未命中：根因行 fail（theory/引导语不泄露答案），仅证据链 3/10 权重。"""
    result, _ = _run("E8", dict(E8_PARAMS, guess="L5"))
    _show(result)
    assert not result["all_pass"]
    rows = _e8_rows(result)
    root = rows["根因链路定位"]
    assert not root["pass"]
    assert root["measured"] == "L5"
    assert root["theory"] == "由观测边界推断"
    assert "分界" in root["hint"]            # 引导语指向重新观测，不含答案
    assert "L4" not in root["hint"]
    assert result["score"] == 21.0           # 70 × (3 / (7+3))


def test_e8_evidence_missing():
    """guess 命中但证据链为空：证据行 fail，总分 70×7/10 = 49。"""
    result, _ = _run("E8", dict(E8_PARAMS, evidence=""))
    _show(result)
    rows = _e8_rows(result)
    assert rows["根因链路定位"]["pass"]
    assert not rows["证据链完整性"]["pass"]
    assert rows["证据链完整性"]["measured"] == "未提交"
    assert not result["all_pass"]
    assert result["score"] == 49.0


def test_e8_evidence_without_reference():
    """证据非空但既无上游/下游/对比表述也不引用探测节点：判 fail。"""
    result, _ = _run("E8", dict(E8_PARAMS, evidence="我觉得就是它"))
    rows = _e8_rows(result)
    assert not rows["证据链完整性"]["pass"]
    assert rows["证据链完整性"]["measured"] == "已提交但缺对比/节点引用"


def test_e8_probe_budget_exceeded():
    """4 个探测节点：预算行 fail（weight 0 不扣分）但 all_pass=False。"""
    result, _ = _run("E8", dict(E8_PARAMS, probes="S1,S2,S3,S4"))
    _show(result)
    rows = _e8_rows(result)
    budget = rows["探测预算（≤3 个节点）"]
    assert budget["measured"] == 4 and not budget["pass"]
    assert len(result["observations"]) == 4   # 按链路顺序全部给出观测
    assert [o["node"] for o in result["observations"]] == [
        "S1", "S2", "S3", "S4"]
    assert result["score"] == 70.0            # 命中 + 证据满权，预算不扣分
    assert not result["all_pass"]             # 但预算超限影响 all_pass


def test_e8_seed_selects_fault_deterministically():
    """_seed 覆盖：seed=9 → fault=L2（S2/S4 均劣化）；命中仍拿满分。"""
    result, _ = _run("E8", dict(E8_PARAMS, guess="L2", _seed=9))
    _show(result)
    assert result["all_pass"]
    obs = {o["node"]: o for o in result["observations"]}
    assert obs["S2"]["loss_pct"] > 50 and obs["S4"]["loss_pct"] > 50
    assert result["score"] == 70.0
    # 同种子可复现（考核模式）
    result2, _ = _run("E8", dict(E8_PARAMS, guess="L2", _seed=9))
    assert [o["loss_pct"] for o in result2["observations"]] == [
        o["loss_pct"] for o in result["observations"]]


def test_e8_without_probes():
    """无探测：根因凭空猜不可验证证据链（observations 为空，引导补探测）。"""
    result, _ = _run("E8", dict(E8_PARAMS, probes=""))
    assert result["observations"] == []
    assert "未部署探测" in result["conclusion"]


# ----------------------------------------------------------------------
# P1 参数扫描评分（_score_explore 档位）+ attempts 存档链路
# ----------------------------------------------------------------------

def _explore_detail(result):
    return next(d for d in result["score_detail"] if d["item"] == "参数探索")


def test_score_explore_tiers():
    """档位全覆盖（E2 sweep_key=rho ∈ [0.3, 0.95]，区间 40% = 0.26）。"""
    from experiments import _score_explore
    spec = EXPERIMENTS["E2"]
    # 默认值单次 → 0
    pts, note = _score_explore(spec, {"rho": 0.8, "window_s": 120}, None)
    assert pts == 0 and "1 个取值" in note and "参数扫描" in note
    # 非默认但仅 1 个取值 → 2
    pts, _ = _score_explore(spec, {"rho": 0.6}, [])
    assert pts == 2
    # 2 个取值 → 4；3 个取值 → 6
    pts, _ = _score_explore(spec, {"rho": 0.6}, [{"params": {"rho": 0.8}}])
    assert pts == 4
    pts, _ = _score_explore(spec, {"rho": 0.6},
                            [{"params": {"rho": 0.8}},
                             {"params": {"rho": 0.5}}])
    assert pts == 6
    # ≥4 个取值且跨度 ≥ 40% 区间（0.95−0.3=0.65 ≥ 0.26）→ 10
    pts, _ = _score_explore(spec, {"rho": 0.95},
                            [{"params": {"rho": 0.3}},
                             {"params": {"rho": 0.5}},
                             {"params": {"rho": 0.7}}])
    assert pts == 10
    # ≥4 个取值但跨度不足（0.95−0.8=0.15 < 0.26）→ 8
    pts, _ = _score_explore(spec, {"rho": 0.95},
                            [{"params": {"rho": 0.8}},
                             {"params": {"rho": 0.85}},
                             {"params": {"rho": 0.9}}])
    assert pts == 8
    # 历史无效值（越界 / 非数值 / 缺键）不计入覆盖
    pts, _ = _score_explore(spec, {"rho": 0.6},
                            [{"params": {"rho": 9.9}},
                             {"params": {"rho": "abc"}},
                             {"params": {}},
                             "not-a-dict"])
    assert pts == 2
    # E8 无 sweep_key：诊断型不计扫描分
    pts, note = _score_explore(EXPERIMENTS["E8"], {"probes": "S2"}, [])
    assert pts == 0 and "诊断" in note


def test_score_explore_sweep_keys_declared():
    """E1~E7 各声明合法 sweep_key（指向自身数值输入），E8/E9 不设。"""
    for eid, key in (("E1", "pps"), ("E2", "rho"), ("E3", "src_pps"),
                     ("E4", "low_pps"), ("E5", "src_pps"),
                     ("E6", "sats_per_plane"), ("E7", "rain_db")):
        spec = EXPERIMENTS[eid]
        assert spec["sweep_key"] == key
        field = next(f for f in spec["inputs"] if f["key"] == key)
        assert field["type"] in ("int", "float")
    assert "sweep_key" not in EXPERIMENTS["E8"]
    assert "sweep_key" not in EXPERIMENTS["E9"]


def test_run_experiment_prior_attempts_sweep():
    """run_experiment 透传 prior_attempts：历史运行并入扫描覆盖计分。"""
    atts = [{"params": {"rho": 0.5}, "score": 70.0, "all_pass": True},
            {"params": {"rho": 0.7}, "score": 70.0, "all_pass": True}]
    result, _ = _run("E2", {"rho": 0.6, "window_s": 30}, atts)
    d = _explore_detail(result)
    assert d["score"] == 6                    # 0.5/0.6/0.7 → 3 个取值
    assert "3 个取值" in d["note"]


def test_attempt_field_in_result():
    """结果携带 attempt 存档摘要（params/score/all_pass/ts/metrics）。"""
    result, _ = _run("E1")
    att = result["attempt"]
    assert att["params"] == result["params_used"]
    assert att["score"] == result["score"] == 70.0
    assert att["all_pass"] is True
    assert att["ts"]                          # ISO 时间戳
    assert 1 <= len(att["metrics"]) <= 3      # 前 3 个数值型 measured
    assert all(isinstance(v, (int, float)) for v in att["metrics"].values())
    # E8：文本型 measured 不入 metrics，数值型（预算）入
    r8, _ = _run("E8", dict(E8_PARAMS))
    m8 = r8["attempt"]["metrics"]
    assert set(m8) == {"探测预算（≤3 个节点）"} and m8["探测预算（≤3 个节点）"] == 2


# ----------------------------------------------------------------------
# P2 E9 星座设计（逆向设计性）：目标约束达标 + 迭代方案数判定
# 判据权重 4/2/3/1（时延/丢包/跳数/迭代）；跳数 = (P−1) + ⌊M/2⌋，
# e2e = 11 + 8×跳数 → P1M8 = 43 ms 超标、P2M4/P2M5 = 35 ms 达标
# ----------------------------------------------------------------------

# 两步历史（模拟学生已试 P1M8 与 P2M4；metrics 为 attempt 存档真实格式）
E9_HIST2 = [
    {"params": {"planes": 1, "sats_per_plane": 8, "src_pps": 200},
     "score": 35.0, "all_pass": False, "ts": "2026-08-27T10:00:00",
     "metrics": {"时延达标 e2e ≤ 40 ms": 43.0, "丢包达标 loss ≤ 1%": 0.0,
                 "跳数紧凑 hops ≤ 4": 4}},
    {"params": {"planes": 2, "sats_per_plane": 4, "src_pps": 200},
     "score": 63.0, "all_pass": False, "ts": "2026-08-27T10:05:00",
     "metrics": {"时延达标 e2e ≤ 40 ms": 35.0, "丢包达标 loss ≤ 1%": 0.0,
                 "跳数紧凑 hops ≤ 4": 3}},
]


def test_e9_design_meets_targets():
    """P2M5 + 2 组历史：三项性能达标且累计 3 组不同方案 → 判据全过 70 分。"""
    result, progress = _run("E9", {"planes": 2, "sats_per_plane": 5,
                                   "src_pps": 200}, E9_HIST2)
    _show(result)
    assert result["all_pass"], result["conclusion"]
    rows = {r["label"]: r for r in result["verdict"]}
    e2e_row = rows["时延达标 e2e ≤ 40 ms"]
    assert e2e_row["pass"] and e2e_row["weight"] == 4
    assert abs(e2e_row["measured"] - 35.0) <= 1.0     # 11 + 8×3
    assert rows["丢包达标 loss ≤ 1%"]["pass"]
    assert rows["丢包达标 loss ≤ 1%"]["measured"] == 0.0
    assert rows["跳数紧凑 hops ≤ 4"]["pass"]
    assert rows["跳数紧凑 hops ≤ 4"]["measured"] == 3
    assert rows["设计迭代 ≥ 3 次不同方案"]["pass"]
    assert rows["设计迭代 ≥ 3 次不同方案"]["measured"] == 3
    # 权重 4/2/3/1 全过 → 70；E9 无 sweep_key → 探索 0（设计型计分说明）
    assert result["score"] == 70.0
    d = next(d for d in result["score_detail"] if d["item"] == "参数探索")
    assert d["score"] == 0 and "设计" in d["note"]
    # targets / history 随结果下发（前端徽标条 + 对比表数据源）
    assert result["targets"] == {"e2e_max_ms": 40.0, "loss_max": 0.01,
                                 "hops_max": 4}
    hist = result["history"]
    assert len(hist) == 3                       # 2 条历史 + 本次（末位）
    assert hist[0]["planes"] == 1 and hist[0]["hops"] == 4
    assert abs(hist[0]["e2e_ms"] - 43.0) <= 1.0  # 从历史 metrics 提取
    assert hist[0]["score"] == 35.0
    assert hist[1]["planes"] == 2 and hist[1]["hops"] == 3
    assert hist[-1]["planes"] == 2 and hist[-1]["sats_per_plane"] == 5
    assert hist[-1]["hops"] == 3 and hist[-1]["score"] is None  # 本次未入档
    stages = [s for s, _ in progress]
    assert "warmup" in stages and "measuring" in stages


def test_e9_design_fails_targets():
    """P1M8 无历史：跳数 4 达标但 e2e = 11+32 = 43 > 40 → 时延行 fail +
    迭代行 fail（hint 引导），conclusion 给出差距；得分 70×(2+3)/10 = 35。"""
    result, _ = _run("E9", {"planes": 1, "sats_per_plane": 8})
    _show(result)
    assert not result["all_pass"]
    rows = {r["label"]: r for r in result["verdict"]}
    e2e_row = rows["时延达标 e2e ≤ 40 ms"]
    assert not e2e_row["pass"] and e2e_row["measured"] > 40
    assert rows["丢包达标 loss ≤ 1%"]["pass"]
    hops_row = rows["跳数紧凑 hops ≤ 4"]
    assert hops_row["pass"] and hops_row["measured"] == 4   # 4 ≤ 4 达标
    iter_row = rows["设计迭代 ≥ 3 次不同方案"]
    assert not iter_row["pass"] and iter_row["measured"] == 1
    assert "P×M" in iter_row["hint"]            # 引导语不含最优解
    # conclusion：实测与达标线差距 + 引导（不直接给答案）
    assert "43" in result["conclusion"] and "40" in result["conclusion"]
    assert "迭代引导" in result["conclusion"]
    assert result["score"] == 35.0              # 70 × (2+3) / (4+2+3+1)
    assert len(result["history"]) == 1          # 仅本次
    assert result["history"][0]["hops"] == 4


def test_e9_iteration_criterion():
    """迭代判据：无历史 1 组 fail；2 组不同历史 + 本次 = 3 组 pass；
    历史/本次同 (P, M) 不重复计数。"""
    r1, _ = _run("E9")                          # 默认 P2M4，无历史
    rows = {r["label"]: r for r in r1["verdict"]}
    assert rows["设计迭代 ≥ 3 次不同方案"]["measured"] == 1
    assert not rows["设计迭代 ≥ 3 次不同方案"]["pass"]

    r2, _ = _run("E9", {"planes": 2, "sats_per_plane": 5}, E9_HIST2)
    rows2 = {r["label"]: r for r in r2["verdict"]}
    assert rows2["设计迭代 ≥ 3 次不同方案"]["measured"] == 3
    assert rows2["设计迭代 ≥ 3 次不同方案"]["pass"]
    assert r2["all_pass"]

    # 历史与本次均为 (2,5)（源速率不同）：组合去重 → 仅 1 组，fail
    same = [dict(E9_HIST2[0], params={"planes": 2, "sats_per_plane": 5,
                                      "src_pps": 200}),
            dict(E9_HIST2[1], params={"planes": 2, "sats_per_plane": 5,
                                      "src_pps": 240})]
    r3, _ = _run("E9", {"planes": 2, "sats_per_plane": 5}, same)
    rows3 = {r["label"]: r for r in r3["verdict"]}
    assert rows3["设计迭代 ≥ 3 次不同方案"]["measured"] == 1
    assert not rows3["设计迭代 ≥ 3 次不同方案"]["pass"]
    assert len(r3["history"]) == 3               # 两条历史仍进对比表
    # 无效历史（缺 planes / 非字典）不计入组合
    r4, _ = _run("E9", {"planes": 3, "sats_per_plane": 3},
                 [{"params": {"src_pps": 100}}, "junk", {}])
    rows4 = {r["label"]: r for r in r4["verdict"]}
    assert rows4["设计迭代 ≥ 3 次不同方案"]["measured"] == 1
    assert len(r4["history"]) == 1              # 无效历史不进对比表
