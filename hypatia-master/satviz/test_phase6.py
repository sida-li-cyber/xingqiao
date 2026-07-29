"""
Phase 6 tests for the packet-level DES engine — reconciliation against
analytical theory and long-run / stress robustness.

  Test 5:  packet conservation - generated == delivered + dropped + in_flight
           (exact, global and per QoS priority) under churn + handovers.
  Test 6:  throughput vs theory - per-link tx_bps ~= offered load, delivery
           rate ~= source rate, utilization ~= lambda*pkt/capacity.
  Test 7:  per-hop latency decomposition - link latency ~= prop + serialization
           under light load, and e2e ~= sum of per-hop sojourns (additivity).
  Test 8:  M/D/1 queueing reconciliation - Poisson arrivals + deterministic
           service on a bottleneck: mean wait ~= rho*s/(2*(1-rho)).
  Test 9:  1-hour long-run stress with link flapping under congestion:
           no crash, exact conservation, bounded heap / in-flight, no leaks,
           no tick-time degradation, real handover drops.
  Test 10: backpressure safety valve - in_flight never exceeds max_in_flight
           under extreme overload.
  Test 11: full-pipeline long run (real DemoSimCore, 300 s sim) - conservation
           and stable tick rate.

Run:  python test_phase6.py          (all tests, ~2-3 min)
      python test_phase6.py --fast   (skip the two long runs, ~20 s)
"""

import sys
import time

import packet_sim as ps

PKT_BITS = 1500 * 8


def hdr(title):
    print("=" * 64)
    print(title)
    print("=" * 64)


# ----------------------------------------------------------------------
# Test 5: packet conservation (exact invariant)
# ----------------------------------------------------------------------

def test_conservation():
    hdr("Test 5: packet conservation under churn + handovers")

    # Throttled uplinks (1 Mbps) so queues are genuinely full when the
    # uplinks hand over -> real handover-drop spikes to reconcile against.
    eng = ps.PacketEngine(
        config={"queue_capacity_pkts": 30, "route_refresh_interval": 1.0,
                "capacity": {"isl": 1e10, "gsl": 1e9, "sul": 1e6, "ssl": 1e6}},
        seed=23)
    nodes = ["UAV-1", "Ship-1", "Sat-A", "Sat-B", "Sat-C", "GS-1"]

    topo_a = [
        ("UAV-1", "Sat-A", "sul", 0.005),
        ("Ship-1", "Sat-A", "ssl", 0.007),
        ("Sat-A", "Sat-B", "isl", 0.010),
        ("Sat-B", "GS-1", "gsl", 0.006),
    ]
    topo_b = [
        ("UAV-1", "Sat-C", "sul", 0.005),
        ("Ship-1", "Sat-C", "ssl", 0.007),
        ("Sat-C", "Sat-B", "isl", 0.010),
        ("Sat-B", "GS-1", "gsl", 0.006),
    ]
    # Heavy load so queues are non-empty when uplinks hand over.
    eng.sync_flows({"UAV-1": "GS-1", "Ship-1": "GS-1"},
                   {"UAV-1": 3000.0, "Ship-1": 3000.0},
                   {"UAV-1": ps.PRIO_HIGH, "Ship-1": ps.PRIO_BEST_EFFORT})

    t = 0.0
    for i in range(240):                       # 120 s, flap every 3 s
        topo = topo_a if (i // 6) % 2 == 0 else topo_b
        eng.sync_topology(nodes, topo)
        t += 0.5
        eng.advance(t)
        eng.snapshot(0.5)

    generated = sum(eng.n_generated.values())
    accounted = eng.total_delivered + eng.total_dropped + eng.in_flight
    # In-flight packets are not tracked per priority, so per priority we can
    # only assert generated - (delivered+dropped) >= 0, with the residuals
    # summing exactly to the global in-flight count.
    resid = [eng.n_generated_prio[p] -
             (eng.n_delivered_prio[p] + eng.n_dropped_prio[p])
             for p in range(ps.NUM_PRIO)]
    print(f"  generated={generated}  delivered={eng.total_delivered}  "
          f"dropped={eng.total_dropped}  in_flight={eng.in_flight}")
    print(f"  handover_dropped={eng.total_handover_dropped}")
    print(f"  per-priority residuals (in flight) = {resid}  "
          f"(sum {sum(resid)}, expect {eng.in_flight})")

    ok = (generated == accounted and
          all(r >= 0 for r in resid) and
          sum(resid) == eng.in_flight and
          eng.total_handover_dropped > 0 and
          eng.total_delivered > 0)
    print(f"  -> {'PASS' if ok else 'FAIL'}"
          f"  (invariant {'holds' if generated == accounted else 'VIOLATED'})")
    return ok


# ----------------------------------------------------------------------
# Test 6: throughput vs theory
# ----------------------------------------------------------------------

def test_throughput():
    hdr("Test 6: throughput / utilization vs offered load")

    lam = 10.0                                  # pps, light load
    eng = ps.PacketEngine(
        config={"default_rate_pps": lam, "route_refresh_interval": 1.0,
                "capacity": {"isl": 1e10, "gsl": 1e6, "sul": 5e8, "ssl": 5e8}},
        seed=42)
    nodes = ["UAV-1", "Sat-A", "Sat-B", "GS-1"]
    edges = [
        ("UAV-1", "Sat-A", "sul", 0.005),
        ("Sat-A", "Sat-B", "isl", 0.010),
        ("Sat-B", "GS-1", "gsl", 0.006),
    ]
    eng.sync_topology(nodes, edges)
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": lam})

    eng.advance(30.0)                           # warm-up
    eng.snapshot(30.0)
    d0 = eng.total_delivered
    eng.advance(90.0)                           # 60 s measurement window
    snap = eng.snapshot(60.0)
    rate = (eng.total_delivered - d0) / 60.0

    theory_bps = lam * PKT_BITS                 # 120 kbps on every hop
    print(f"  offered load       = {theory_bps / 1e3:.1f} kbps per hop "
          f"(lambda={lam} pps x {PKT_BITS} bits)")
    print(f"  delivery rate      = {rate:.2f} pps  (theory {lam})")

    ok = abs(rate - lam) / lam < 0.08
    for uk in (frozenset(("UAV-1", "Sat-A")),
               frozenset(("Sat-A", "Sat-B")),
               frozenset(("Sat-B", "GS-1"))):
        lm = snap["links"][uk]
        err = abs(lm["tx_bps"] - theory_bps) / theory_bps
        print(f"    {'-'.join(sorted(uk)):<18} tx={lm['tx_bps']/1e3:8.1f} kbps "
              f"err={err*100:4.1f}%  util={lm['utilization']:.4f}")
        ok = ok and err < 0.08

    gsl = snap["links"][frozenset(("Sat-B", "GS-1"))]
    theory_util = theory_bps / 1e6              # 0.12 on the 1 Mbps GSL
    print(f"  GSL utilization    = {gsl['utilization']:.4f}  "
          f"(theory {theory_util:.4f})")
    ok = ok and abs(gsl["utilization"] - theory_util) < 0.02
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 7: per-hop latency decomposition + additivity
# ----------------------------------------------------------------------

def test_latency_decomposition():
    hdr("Test 7: per-hop latency = propagation + serialization (light load)")

    lam = 5.0
    caps = {"isl": 1e10, "gsl": 2e6, "sul": 5e8, "ssl": 5e8}
    eng = ps.PacketEngine(
        config={"default_rate_pps": lam, "route_refresh_interval": 1.0,
                "capacity": caps},
        seed=5)
    nodes = ["UAV-1", "Sat-A", "Sat-B", "GS-1"]
    edges = [
        ("UAV-1", "Sat-A", "sul", 0.005),
        ("Sat-A", "Sat-B", "isl", 0.010),
        ("Sat-B", "GS-1", "gsl", 0.006),
    ]
    eng.sync_topology(nodes, edges)
    eng.sync_flows({"UAV-1": "GS-1"}, {"UAV-1": lam})

    eng.advance(30.0)
    eng.snapshot(30.0)
    eng.advance(90.0)
    snap = eng.snapshot(60.0)

    ok = True
    total_sojourn = 0.0
    for (a, b, ltype, prop_s) in edges:
        lm = snap["links"][frozenset((a, b))]
        ser_ms = PKT_BITS / caps[ltype] * 1000.0
        theory = prop_s * 1000.0 + ser_ms
        total_sojourn += lm["latency_ms"]
        err = abs(lm["latency_ms"] - theory)
        print(f"    {a}-{b:<6} lat={lm['latency_ms']:8.3f} ms  "
              f"theory={theory:8.3f} ms (prop {prop_s*1000:.1f} + ser {ser_ms:.3f})  "
              f"|err|={err:.3f} ms")
        ok = ok and err < 0.8

    e2e = snap["summary"]["avg_e2e_latency_ms"]
    add_err = abs(e2e - total_sojourn)
    print(f"  e2e = {e2e:.3f} ms   sum of per-hop sojourns = "
          f"{total_sojourn:.3f} ms   |err|={add_err:.3f} ms")
    ok = ok and add_err < 1.0
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 8: M/D/1 queueing reconciliation
# ----------------------------------------------------------------------

def test_md1_queueing():
    hdr("Test 8: M/D/1 queueing - mean wait ~= rho*s/(2*(1-rho))")

    cap = 2e6                                   # 2 Mbps bottleneck GSL
    s = PKT_BITS / cap                          # deterministic service: 6 ms
    lam = 0.8 / s                               # rho = 0.8  -> 133.33 pps
    rho = lam * s
    wq = rho * s / (2.0 * (1.0 - rho))          # M/D/1 mean queue wait: 12 ms
    prop = 0.001 + 0.005                        # upstream + bottleneck prop
    ser_up = PKT_BITS / 1e10                    # upstream 10 Gbps: 1.2 us
    theory = (prop + ser_up + s + wq) * 1000.0

    eng = ps.PacketEngine(
        config={"route_refresh_interval": 1.0,
                "capacity": {"isl": 1e10, "gsl": cap, "sul": 1e10, "ssl": 1e10}},
        seed=99)
    nodes = ["src", "R1", "GS"]
    edges = [("src", "R1", "sul", 0.001), ("R1", "GS", "gsl", 0.005)]
    eng.sync_topology(nodes, edges)
    eng.sync_flows({"src": "GS"}, {"src": lam})

    eng.advance(60.0)                           # warm-up to stationarity
    eng.snapshot(60.0)
    eng.advance(180.0)                          # 120 s measurement window
    snap = eng.snapshot(120.0)

    s_sum = snap["summary"]
    e2e = s_sum["avg_e2e_latency_ms"]
    gsl = snap["links"][frozenset(("R1", "GS"))]
    print(f"  lambda={lam:.2f} pps  s={s*1000:.1f} ms  rho={rho:.2f}  "
          f"theory Wq={wq*1000:.1f} ms")
    print(f"  theory e2e = {theory:.2f} ms   measured e2e = {e2e:.2f} ms   "
          f"|err| = {abs(e2e - theory):.2f} ms")
    print(f"  bottleneck util = {gsl['utilization']:.3f} (theory {rho:.2f})  "
          f"queue_depth = {gsl['queue_depth']}  dropped = {s_sum['pkts_dropped']}")

    ok = (abs(e2e - theory) < 3.0 and           # within 25% of the 12ms wait
          abs(gsl["utilization"] - rho) < 0.05 and
          s_sum["pkts_dropped"] == 0)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 9: 1-hour long-run stress with flapping + congestion
# ----------------------------------------------------------------------

def test_long_run():
    hdr("Test 9: long-run stress - 3600 s sim, flapping, congestion")

    eng = ps.PacketEngine(
        config={"queue_capacity_pkts": 200, "route_refresh_interval": 5.0,
                "capacity": {"isl": 5e6, "gsl": 5e6, "sul": 5e6, "ssl": 5e6}},
        seed=1234)
    nodes = ["A", "B", "C", "D", "E", "X"]
    topo_a = [("A", "B", "isl", 0.005), ("B", "C", "isl", 0.005),
              ("C", "D", "isl", 0.005), ("D", "E", "isl", 0.005)]
    topo_b = [("A", "B", "isl", 0.005), ("B", "C", "isl", 0.005),
              ("C", "X", "isl", 0.005), ("X", "D", "isl", 0.005),
              ("D", "E", "isl", 0.005)]
    eng.sync_flows({"A": "E"}, {"A": 400.0})    # 400 pps x 12 kbit = 4.8 Mbps

    dt = 0.2
    n_ticks = int(3600 / dt)                    # 18000 ticks = 1 h sim time
    t0 = time.time()
    first_block = last_block = None
    try:
        for i in range(n_ticks):
            if i % 1000 == 0:
                blk_t0 = time.time()
            eng.sync_topology(nodes, topo_a if (i // 50) % 2 == 0 else topo_b)
            eng.advance((i + 1) * dt)
            eng.snapshot(dt)
            if i % 1000 == 999:
                blk = time.time() - blk_t0
                if first_block is None:
                    first_block = blk
                last_block = blk
        wall = time.time() - t0
    except Exception as e:                      # noqa: BLE001
        print(f"  EXCEPTION after {i} ticks: {e!r}")
        print("  -> FAIL")
        return False

    generated = sum(eng.n_generated.values())
    accounted = eng.total_delivered + eng.total_dropped + eng.in_flight
    n_edges = len(topo_b)
    heap_bound = len(eng.sources) + len(eng.links) + eng.in_flight + 1000

    print(f"  wall={wall:.1f}s  ({n_ticks / wall:.0f} ticks/s)  "
          f"first1000={first_block:.2f}s  last1000={last_block:.2f}s")
    print(f"  delivered={eng.total_delivered}  dropped={eng.total_dropped}  "
          f"handover={eng.total_handover_dropped}  in_flight={eng.in_flight}")
    print(f"  conservation: generated={generated} == accounted={accounted}  "
          f"-> {generated == accounted}")
    print(f"  heap={len(eng.events)} (bound {heap_bound})  "
          f"links={len(eng.links)} (expect {2 * n_edges})  "
          f"ports={len(eng.ports)}")

    ok = (generated == accounted and
          eng.in_flight <= eng.cfg["max_in_flight"] and
          len(eng.events) <= heap_bound and
          len(eng.links) == 2 * n_edges and
          len(eng.ports) == len(eng.links) and
          eng.total_handover_dropped > 0 and
          eng.total_dropped > 0 and
          eng.total_delivered > 0 and
          last_block < 3.0 * first_block)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 10: backpressure safety valve
# ----------------------------------------------------------------------

def test_backpressure():
    hdr("Test 10: backpressure - in_flight capped at max_in_flight")

    # One slow link with a big buffer: without the valve the queue would grow
    # toward 500 packets; with max_in_flight=100 injection must throttle so
    # that in_flight plateaus at exactly the cap.
    max_in_flight = 100
    eng = ps.PacketEngine(
        config={"queue_capacity_pkts": 500, "max_in_flight": max_in_flight,
                "route_refresh_interval": 1.0,
                "capacity": {"isl": 1e5, "gsl": 1e5, "sul": 1e5, "ssl": 1e5}},
        seed=8)
    nodes = ["A", "B"]
    edges = [("A", "B", "isl", 0.001)]
    eng.sync_topology(nodes, edges)
    eng.sync_flows({"A": "B"}, {"A": 500.0})    # absurd overload: 6 Mbps into 100 kbps

    peak = 0
    t = 0.0
    for _ in range(240):                        # 120 s
        t += 0.5
        eng.advance(t)
        eng.snapshot(0.5)
        peak = max(peak, eng.in_flight)

    generated = sum(eng.n_generated.values())
    accounted = eng.total_delivered + eng.total_dropped + eng.in_flight
    print(f"  peak in_flight = {peak}  (cap {max_in_flight}, "
          f"buffer would allow 500)")
    print(f"  delivered={eng.total_delivered}  dropped={eng.total_dropped}  "
          f"generated={generated}  conservation={generated == accounted}")

    ok = (99 <= peak <= max_in_flight and
          generated == accounted and
          eng.total_delivered > 0)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 11: full-pipeline long run (real DemoSimCore)
# ----------------------------------------------------------------------

def test_full_pipeline_long_run():
    hdr("Test 11: full pipeline - 300 s sim through the real DemoSimCore")

    from demo_sim_core import DemoSimCore

    core = DemoSimCore()
    core.is_playing = True
    core.speed = 1.0

    n_ticks = 1500                              # 1500 * 0.2 s = 300 s sim
    t0 = time.time()
    first_block = last_block = None
    for i in range(n_ticks):
        if i % 100 == 0:
            blk_t0 = time.time()
        core.sim_time += core.update_interval * core.speed
        core.get_state_update()
        if i % 100 == 99:
            blk = time.time() - blk_t0
            if first_block is None:
                first_block = blk
            last_block = blk
    wall = time.time() - t0

    eng = core.engine
    generated = sum(eng.n_generated.values())
    accounted = eng.total_delivered + eng.total_dropped + eng.in_flight
    print(f"  sim_time={core.sim_time:.0f}s  wall={wall:.1f}s  "
          f"({n_ticks / wall:.0f} ticks/s)  "
          f"first100={first_block:.2f}s  last100={last_block:.2f}s")
    print(f"  delivered={eng.total_delivered}  dropped={eng.total_dropped}  "
          f"handover={eng.total_handover_dropped}  in_flight={eng.in_flight}")
    print(f"  conservation: generated={generated} == accounted={accounted}")

    ok = (generated == accounted and
          eng.total_delivered > 0 and
          eng.in_flight < eng.cfg["max_in_flight"] and
          last_block < 3.0 * first_block)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------

if __name__ == "__main__":
    fast = "--fast" in sys.argv
    results = [
        ("5  conservation", test_conservation()),
        ("6  throughput", test_throughput()),
        ("7  latency-decomp", test_latency_decomposition()),
        ("8  M/D/1", test_md1_queueing()),
        ("9  long-run", test_long_run()),
        ("10 backpressure", test_backpressure()),
    ]
    if not fast:
        results.append(("11 full-pipeline", test_full_pipeline_long_run()))

    print()
    print("=" * 64)
    for name, ok in results:
        print(f"  Test {name:18s} {'PASS' if ok else 'FAIL'}")
    print("=" * 64)
    print("ALL PASS" if all(r for _, r in results) else "SOME FAILED")
    sys.exit(0 if all(r for _, r in results) else 1)
