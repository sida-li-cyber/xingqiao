"""Standalone validation for packet_sim.PacketEngine (Phase 2).

Test 1 (light load): a 3-hop chain UAV->Sat->Sat->GS. Verify delivered
packets' end-to-end latency ~ sum of propagation delays + serialization.

Test 2 (congestion): bottleneck a link with low capacity + high arrival
rate. Verify queues build, loss appears, and e2e latency rises above the
pure-propagation floor.
"""
import os
import sys

# Engine modules live in <root>/hypatia-master/satviz; tests in <root>/tests.
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

import packet_sim as ps


def run(engine, nodes, edges, seconds, dt=0.2):
    t = 0.0
    last = None
    for _ in range(int(seconds / dt)):
        t += dt
        engine.sync_topology(nodes, edges)
        engine.advance(t)
        last = engine.snapshot(dt)
    return last


def test_light_load():
    print("=" * 60)
    print("Test 1: light load — e2e latency vs theory")
    print("=" * 60)
    eng = ps.PacketEngine(
        config={"default_rate_pps": 10, "route_refresh_interval": 1.0},
        seed=42)
    nodes = ["UAV-1", "Sat-A", "Sat-B", "GS-1"]
    # propagation delays (s): 5ms + 10ms + 6ms = 21ms geometric floor
    edges = [
        ("UAV-1", "Sat-A", "sul", 0.005),
        ("Sat-A", "Sat-B", "isl", 0.010),
        ("Sat-B", "GS-1", "gsl", 0.006),
    ]
    eng.sync_topology(nodes, edges)
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": 10})

    last = run(eng, nodes, edges, seconds=30)
    s = last["summary"]
    uav = last["nodes"]["UAV-1"]

    # serialization floor: 1500B on sul(500M)+isl(10G)+gsl(1G)
    ser_ms = 1500 * 8 * (1 / 5e8 + 1 / 1e10 + 1 / 1e9) * 1000
    theory_ms = 21.0 + ser_ms

    print(f"  delivered={s['pkts_delivered']}  dropped={s['pkts_dropped']}  "
          f"in_flight={s['pkts_in_flight']}")
    print(f"  UAV-1 sent={uav['pkts_sent']}  recv={uav['pkts_recv']}")
    print(f"  theory e2e ~ {theory_ms:.3f} ms  (21ms prop + {ser_ms:.3f}ms ser)")
    print(f"  measured avg_e2e = {s['avg_e2e_latency_ms']:.3f} ms")
    for uk, lm in sorted(last["links"].items(), key=lambda x: sorted(x[0])):
        print(f"    {'-'.join(sorted(uk)):<18} tx={lm['tx_bps']:>10.0f} bps "
              f"util={lm['utilization']:.3f} prop={lm['propagation_ms']:.2f}ms "
              f"lat={lm['latency_ms']:.3f}ms")

    ok = (s["pkts_delivered"] > 200 and s["pkts_dropped"] == 0 and
          abs(s["avg_e2e_latency_ms"] - theory_ms) < 1.0)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def test_congestion():
    print("=" * 60)
    print("Test 2: congestion — queueing + loss")
    print("=" * 60)
    eng = ps.PacketEngine(config={
        "default_rate_pps": 200,
        "queue_capacity_pkts": 20,
        "route_refresh_interval": 1.0,
        # bottleneck the GSL to 100 kbps
        "capacity": {"isl": 1e6, "gsl": 1e5, "sul": 1e6, "ssl": 1e6},
    }, seed=7)
    nodes = ["UAV-1", "Sat-A", "GS-1"]
    edges = [
        ("UAV-1", "Sat-A", "sul", 0.005),
        ("Sat-A", "GS-1", "gsl", 0.006),
    ]
    eng.sync_topology(nodes, edges)
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": 200})

    last = run(eng, nodes, edges, seconds=30)
    s = last["summary"]
    print(f"  delivered={s['pkts_delivered']}  dropped={s['pkts_dropped']}  "
          f"in_flight={s['pkts_in_flight']}")
    print(f"  avg_e2e = {s['avg_e2e_latency_ms']:.2f} ms (prop floor ~11ms)")
    gsl = last["links"].get(frozenset(("Sat-A", "GS-1")), {})
    print(f"  GSL queue_depth={gsl.get('queue_depth')}  "
          f"loss_rate={gsl.get('loss_rate', 0):.3f}  util={gsl.get('utilization', 0):.3f}")

    ok = (s["pkts_dropped"] > 0 and
          s["avg_e2e_latency_ms"] > 11.0 and
          gsl.get("loss_rate", 0) > 0)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    r1 = test_light_load()
    print()
    r2 = test_congestion()
    print()
    print("ALL PASS" if (r1 and r2) else "SOME FAILED")
