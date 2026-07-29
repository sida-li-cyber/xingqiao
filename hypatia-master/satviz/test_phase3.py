"""
Phase 3 tests for the packet-level DES engine:

  Test 3: handover loss  - removing an active link drops its in-flight packets
                           and they are accounted as handover drops (a real,
                           observable loss spike), while traffic reroutes over a
                           surviving link.
  Test 4: QoS strict priority - under congestion on a shared bottleneck, a
                           high-priority flow sees far lower latency and far
                           fewer drops than a best-effort flow.

Run:  python test_phase3.py
"""

from packet_sim import PacketEngine, PRIO_HIGH, PRIO_BEST_EFFORT


def test_handover_loss():
    print("=" * 60)
    print("Test 3: handover loss -> in-flight packets dropped on link removal")
    print("=" * 60)

    eng = PacketEngine(seed=7)
    nodes = ["UAV-1", "Sat-A", "Sat-B", "GS-1"]

    # Single uplink UAV-1 -> Sat-A -> GS-1, with the SUL throttled below the
    # source rate so a queue builds up on the uplink (realistic congestion that
    # a handover will interrupt).
    edges_a = [
        ("UAV-1", "Sat-A", "sul", 0.005),
        ("Sat-A", "GS-1", "gsl", 0.006),
    ]
    eng.sync_topology(nodes, edges_a)
    for key in (("UAV-1", "Sat-A"), ("Sat-A", "UAV-1")):
        eng.links[key].capacity_bps = 1e6      # 1 Mbps SUL < 3000 pps * 12 kbit
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": 3000.0})

    for _ in range(20):                       # 0 -> 10 s, queue builds up
        eng.advance(eng.now + 0.5)
        eng.snapshot(0.5)

    qlen = eng.ports[("UAV-1", "Sat-A")].queued
    before = eng.total_dropped
    print(f"  uplink queue just before handover = {qlen} packets")

    # Hand over: remove the Sat-A uplink, bring up Sat-B instead.
    edges_b = [
        ("UAV-1", "Sat-B", "sul", 0.005),
        ("Sat-B", "GS-1", "gsl", 0.006),
    ]
    eng.sync_topology(nodes, edges_b)

    spike = eng.total_dropped - before
    handover = eng.total_handover_dropped
    print(f"  drop spike at handover        = {spike}")
    print(f"  cumulative handover drops     = {handover}")

    # Traffic must keep flowing over the new uplink.
    eng.advance(eng.now + 0.5)
    delivered_before = eng.total_delivered
    for _ in range(20):                       # 10 -> 20 s
        eng.advance(eng.now + 0.5)
        eng.snapshot(0.5)
    delivered_after = eng.total_delivered - delivered_before
    print(f"  delivered after handover (10s) = {delivered_after}")

    ok = (spike > 50 and spike == handover and
          qlen <= spike <= qlen + 1 and delivered_after > 0)
    print("  -> PASS" if ok else "  -> FAIL")
    return ok


def test_qos_priority():
    print("=" * 60)
    print("Test 4: QoS strict priority under congestion")
    print("=" * 60)

    # Two sources merge onto a shared bottleneck R1 -> GS. The bottleneck is
    # sized so the high-priority flow (60 pps ~ 0.72 Mbps) fits comfortably but
    # the best-effort flow cannot also fit, so it queues, runs late and drops.
    cfg = {"queue_capacity_pkts": 30}
    eng = PacketEngine(cfg, seed=11)
    nodes = ["srcH", "srcL", "R1", "GS"]
    edges = [
        ("srcH", "R1", "sul", 0.001),
        ("srcL", "R1", "sul", 0.001),
        ("R1", "GS", "gsl", 0.005),
    ]
    eng.sync_topology(nodes, edges)
    # Force the bottleneck capacity low (override the type default).
    eng.links[("R1", "GS")].capacity_bps = 1.0e6
    eng.links[("GS", "R1")].capacity_bps = 1.0e6

    eng.sync_flows(
        {"srcH": "GS", "srcL": "GS"},
        {"srcH": 60.0, "srcL": 60.0},
        {"srcH": PRIO_HIGH, "srcL": PRIO_BEST_EFFORT},
    )

    for _ in range(110):                      # 0 -> 55 s warmup
        eng.advance(eng.now + 0.5)
        eng.snapshot(0.5)
    eng.advance(eng.now + 5.0)
    snap = eng.snapshot(5.0)                  # 5 s measurement window
    nh = snap["nodes"]["srcH"]
    nl = snap["nodes"]["srcL"]
    qos = snap["summary"]["qos"]
    qh = qos[str(PRIO_HIGH)]
    ql = qos[str(PRIO_BEST_EFFORT)]
    high_loss = qh["dropped"] / max(qh["generated"], 1)
    low_loss = ql["dropped"] / max(ql["generated"], 1)
    print(f"  HIGH  gen={qh['generated']} del={qh['delivered']} "
          f"drop={qh['dropped']} loss={high_loss:.3f} e2e={nh['e2e_latency_ms']:.2f}ms")
    print(f"  LOW   gen={ql['generated']} del={ql['delivered']} "
          f"drop={ql['dropped']} loss={low_loss:.3f} e2e={nl['e2e_latency_ms']:.2f}ms")

    ok = (nh["e2e_latency_ms"] > 0 and nl["e2e_latency_ms"] > 0 and
          nh["e2e_latency_ms"] < nl["e2e_latency_ms"] and
          high_loss < low_loss and low_loss > 0)
    print("  -> PASS" if ok else "  -> FAIL")
    return ok


if __name__ == "__main__":
    results = [test_handover_loss(), test_qos_priority()]
    print("=" * 60)
    print("ALL PASS" if all(results) else "SOME FAILED")
    print("=" * 60)
