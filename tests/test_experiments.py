"""改进 #2 实验运行器验证：E1~E7 理论对账 + P0 参数化 + 评分闭环。

场景与判据源自（并应保持一致于）：
  E1 tests/test_packet_sim.py Test 1
  E2 tests/test_phase6.py     Test 8
  E3 tests/test_phase3.py     Test 3
  E4 tests/test_phase3.py     Test 4
  E5 tests/test_experiments.py（负载感知路由 vs 最短时延，双模式）
  E6 tests/test_experiments.py（网格拓扑跳数 = (P−1) + ⌊M/2⌋）
  E7 tests/test_experiments.py（雨衰容量 ×10^(−a/10) → 丢包 1−C_eff/λ）

P0 参数化：inputs Schema（范围/步进/默认）、params 越界夹紧、
理论值随参数动态计算（E1 包长 → 发送时延、E2 ρ → Wq、E3 队列 → 尖峰、
E4 负载/容量 → 拥塞判定双模式）。

评分闭环（改进计划阶段一）：对账判定 70 + 参数探索 10 + 预习测验 10 +
思考题 10 = 100 分；题库答案不下发目录；grade_quiz / grade_questions /
compose_score 判分可复现（固定种子）。

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


def _run(exp_id, params=None):
    progress = []

    def on_progress(update):
        progress.append((update.get("stage"), update.get("progress")))

    result = asyncio.run(run_experiment(
        exp_id, run_params=params, on_progress=on_progress))
    return result, progress


def _show(result):
    print(f"\n{result['exp_id']} {result['name']}: {result['conclusion']}")
    for r in result["verdict"]:
        print(f"  {r['label']}: theory={r['theory']} "
              f"measured={r['measured']} pass={r['pass']}")


def test_catalog_complete():
    cat = experiment_catalog()
    assert [c["exp_id"] for c in cat] == [
        "E1", "E2", "E3", "E4", "E5", "E6", "E7"]
    for c in cat:
        assert c["name"] and c["summary"] and c["theory_note"]
        assert isinstance(c["params"], dict) and c["params"]
        # P0: Schema / 指导书 / 拓扑齐备，供实验台自动生成界面
        for f in c["inputs"]:
            assert f["min"] <= f["default"] <= f["max"]
            assert f["step"] > 0 and f["label"]
        g = c["guide"]
        assert g["objective"] and g["principle"] and len(g["steps"]) >= 4
        assert len(g["questions"]) >= 3
        t = c["topology"]
        assert len(t["nodes"]) >= 3 and len(t["edges"]) >= 2
        # 评分闭环（阶段一）：目录携带题库题干与评分权重，且不下发答案/解析
        assert c["score_max"] == SCORE_MAX
        assert len(c["quiz"]) == len(QUIZZES[c["exp_id"]]) >= 3
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
    # 非默认参数 → 参数探索分 10，总分 80（固定种子可复现）
    assert result["score"] == 80.0


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
        asyncio.run(run_experiment("E9"))
    except ExperimentNotFound:
        pass
    else:
        raise AssertionError("E9 should not exist")


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
        grade_quiz("E9", {})
    except ExperimentNotFound:
        pass
    else:
        raise AssertionError("E9 should not exist")


def test_grade_questions_by_ratio():
    n = len(EXPERIMENTS["E4"]["guide"]["questions"])
    g = grade_questions("E4", {str(i): "答案" for i in range(n)})
    assert g == {"score": 10.0, "max": 10, "answered": n, "n_total": n}
    g2 = grade_questions("E4", {"0": "  "})               # 空白不算作答
    assert g2["score"] == 0.0 and g2["answered"] == 0


def test_compose_score_full_and_partial():
    result, _ = _run("E1", {"pkt_bytes": 9000})           # 仿真部分 80 分
    quiz = {"score": 10.0, "max": 10}
    qs = {"score": 10.0, "max": 10}
    total = compose_score(result, quiz, qs)
    assert total["score"] == 100.0 and total["max"] == 100
    items = [d["item"] for d in total["detail"]]
    assert items == ["对账判定", "参数探索", "预习测验", "思考题作答"]
    # 预习未做 / 思考题未答 → 只拿仿真部分 80 分（封顶 100）
    total2 = compose_score(result)
    assert total2["score"] == 80.0
