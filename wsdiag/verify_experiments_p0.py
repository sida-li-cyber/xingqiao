"""快速验证：E1-E4 默认参数 + 参数化运行 + Schema 工具。"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

import experiments as xp


async def main():
    cat = xp.experiment_catalog()
    print("catalog:", [e["exp_id"] for e in cat])
    assert all("inputs" in e and "guide" in e and "topology" in e for e in cat)

    cases = [
        ("E1", None),
        ("E1", {"pkt_bytes": 9000, "pps": 20}),
        ("E2", None),
        ("E2", {"rho": 0.6, "window_s": 60}),
        ("E3", None),
        ("E3", {"queue_pkts": 100, "src_pps": 5000}),
        ("E4", None),
        ("E4", {"high_pps": 20, "low_pps": 20, "bottleneck_mbps": 2.0}),
    ]
    for exp_id, params in cases:
        t0 = time.perf_counter()
        r = await xp.run_experiment(exp_id, run_params=params)
        dt = time.perf_counter() - t0
        mark = "PASS" if r["all_pass"] else "FAIL"
        print(f"{exp_id} {str(params):<55} {mark}  {dt*1000:6.0f} ms  "
              f"params_used={r['params_used']}")
        print("   ", r["conclusion"])
        for row in r["verdict"]:
            print(f"    - {row['label']}: theory={row['theory']} "
                  f"measured={row['measured']} pass={row['pass']}")

    # 参数夹紧
    r = await xp.run_experiment("E3", run_params={"queue_pkts": 99999,
                                                  "src_pps": 1})
    assert r["params_used"]["queue_pkts"] == 400
    assert r["params_used"]["src_pps"] == 1000
    print("\nclamp OK:", r["params_used"])


if __name__ == "__main__":
    asyncio.run(main())
