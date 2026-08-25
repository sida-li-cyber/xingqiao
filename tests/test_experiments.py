"""改进 #2 实验运行器验证：E1~E4 理论对账 + P0 参数化。

场景与判据源自（并应保持一致于）：
  E1 tests/test_packet_sim.py Test 1
  E2 tests/test_phase6.py     Test 8
  E3 tests/test_phase3.py     Test 3
  E4 tests/test_phase3.py     Test 4

P0 参数化：inputs Schema（范围/步进/默认）、params 越界夹紧、
理论值随参数动态计算（E1 包长 → 发送时延、E2 ρ → Wq、E3 队列 → 尖峰、
E4 负载/容量 → 拥塞判定双模式）。

Run:  pytest tests/test_experiments.py -v     (~15 s)
"""
import asyncio
import os
import sys

# Engine modules live in <root>/hypatia-master/satviz; tests in <root>/tests.
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

from experiments import (
    EXPERIMENTS, ExperimentNotFound, experiment_catalog, run_experiment,
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
    assert [c["exp_id"] for c in cat] == ["E1", "E2", "E3", "E4"]
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


def test_e1_latency_decomposition():
    result, progress = _run("E1")
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert abs(result["verdict"][0]["measured"] - 21.037) < 1.0
    assert progress


def test_e1_parametrized_packet_size():
    result, _ = _run("E1", {"pkt_bytes": 9000})
    _show(result)
    assert result["all_pass"], result["conclusion"]
    assert result["params_used"]["pkt_bytes"] == 9000
    # 9000 B → 发送时延 0.223 ms，e2e ≈ 21.223
    assert abs(result["verdict"][0]["measured"] - 21.223) < 1.0


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
