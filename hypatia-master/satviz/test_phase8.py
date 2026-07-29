"""
Milestone A tests — file-transfer control plane (protocol v3.2 groundwork).

Validates the DES file-transfer model added in packet_sim.py: a file is split
into abstract chunks that are routed like ordinary packets, with timeout-driven
selective-repeat ARQ. No real payload bytes are handled here (that is the
backend data plane, Milestone B); these tests verify the control-plane behaviour
and, above all, that the global packet-conservation invariant stays EXACT.

  Test 17: chunking & lossless completion - ceil() chunk count, short last
           chunk, all chunks delivered, progress == 1.0, retx == 0, exact
           conservation, file_started / file_chunk_delivered / file_complete
           events, file_states structure + route path.
  Test 18: ARQ under heavy loss - 30% link error rate; the file still completes
           byte-for-byte, retx > 0, duplicate deliveries deduped, conservation
           exact (retransmissions count as freshly generated packets).
  Test 19: no-route recovery - file starts while the source is isolated; chunks
           are dropped (no route) then recovered once the topology appears,
           conservation exact throughout.
  Test 20: coexistence with background Poisson traffic - a file transfer runs
           alongside UAV/ship flows; global conservation exact, background still
           delivers, file completes.
  Test 21: multi-file concurrency + QoS priority + cancel - three concurrent
           files through a congested bottleneck; the high-priority file finishes
           first, cancel stops a transfer, conservation exact across the run.

Run:  python test_phase8.py          (tests 17-21, ~10 s)
      python test_phase8.py --fast   (alias; same set, kept for symmetry)
"""

import sys

import packet_sim as ps
from packet_sim import PacketEngine


def hdr(title):
    print("=" * 64)
    print(title)
    print("=" * 64)


def conservation(e):
    """Global invariant: generated == delivered + dropped + in_flight."""
    gen = sum(e.n_generated.values())
    acc = e.total_delivered + e.total_dropped + e.in_flight
    return gen == acc, gen, acc


def run(e, steps, dt=0.01, until_complete=None):
    """Advance the engine, snapshotting each tick. Optionally stop when a
    transfer reaches COMPLETE."""
    t = e.now
    for _ in range(steps):
        t += dt
        e.advance(t)
        e.snapshot(dt)
        if until_complete is not None:
            ft = e.files.get(until_complete)
            if ft is not None and ft.state == ps.FT_COMPLETE:
                break
    return t


LINEAR_NODES = ["UAV-0", "Sat-0", "Sat-1", "GS-0"]
LINEAR_EDGES = [
    ("UAV-0", "Sat-0", "sul", 0.004),
    ("Sat-0", "Sat-1", "isl", 0.008),
    ("Sat-1", "GS-0", "gsl", 0.004),
]
LINEAR_TRANSIT = {"Sat-0", "Sat-1"}


# ----------------------------------------------------------------------
# Test 17: chunking & lossless completion
# ----------------------------------------------------------------------

def test_file_basic():
    hdr("Test 17: file transfer — chunking & lossless completion")
    ok = True

    e = PacketEngine(seed=11)
    e.sync_topology(LINEAR_NODES, LINEAR_EDGES, transit=LINEAR_TRANSIT)
    e.sync_flows({})

    total = 100000
    cs = 16384
    ft = e.start_file("f1", "basic.bin", "UAV-0", "GS-0",
                      total_bytes=total, chunk_size=cs, rate_cap_bps=2e6)

    exp_chunks = -(-total // cs)
    chk = ft.total_chunks == exp_chunks
    ok &= chk
    print(f"  total_chunks={ft.total_chunks} expected={exp_chunks} "
          f"-> {'PASS' if chk else 'FAIL'}")

    exp_last = total - (exp_chunks - 1) * cs
    last = ft.chunk_bytes(exp_chunks - 1)
    chk = last == exp_last
    ok &= chk
    print(f"  last_chunk_bytes={last} expected={exp_last} "
          f"-> {'PASS' if chk else 'FAIL'}")

    run(e, 4000, until_complete="f1")

    chk = (ft.state == ps.FT_COMPLETE and len(ft.delivered) == ft.total_chunks
           and ft.retx_count == 0 and ft.delivered_bytes == total)
    ok &= chk
    print(f"  state={ft.state} delivered={len(ft.delivered)}/{ft.total_chunks} "
          f"retx={ft.retx_count} delivered_bytes={ft.delivered_bytes} "
          f"-> {'PASS' if chk else 'FAIL'}")

    cok, gen, acc = conservation(e)
    ok &= cok
    print(f"  conservation gen={gen} acc={acc} -> {'PASS' if cok else 'FAIL'}")

    types = [x["type"] for x in e.drain_file_events()]
    chk = ("file_started" in types and "file_complete" in types
           and types.count("file_chunk_delivered") == exp_chunks)
    ok &= chk
    print(f"  events started/complete present, delivered-events="
          f"{types.count('file_chunk_delivered')} (exp {exp_chunks}) "
          f"-> {'PASS' if chk else 'FAIL'}")

    st = e.file_states()["f1"]
    need = {"name", "src", "dst", "state", "progress", "delivered_bytes",
            "total_bytes", "eta_s", "throughput_bps", "path", "in_flight", "retx"}
    chk = need.issubset(st.keys()) and st["state"] == "COMPLETE" \
        and abs(st["progress"] - 1.0) < 1e-9 \
        and st["path"][0] == "UAV-0" and st["path"][-1] == "GS-0"
    ok &= chk
    print(f"  file_states keys+progress+path={st['path']} "
          f"-> {'PASS' if chk else 'FAIL'}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 18: ARQ under heavy loss
# ----------------------------------------------------------------------

def test_file_arq_loss():
    hdr("Test 18: ARQ retransmission under 30% link error rate")
    ok = True

    e = PacketEngine({"link_error_rate": 0.3, "file_rto_s": 0.1}, seed=7)
    e.sync_topology(LINEAR_NODES, LINEAR_EDGES, transit=LINEAR_TRANSIT)
    e.sync_flows({})

    total = 200000
    ft = e.start_file("f1", "lossy.bin", "UAV-0", "GS-0",
                      total_bytes=total, chunk_size=16384, rate_cap_bps=2e6)
    run(e, 8000, until_complete="f1")

    chk = (ft.state == ps.FT_COMPLETE and len(ft.delivered) == ft.total_chunks
           and ft.delivered_bytes == total)
    ok &= chk
    print(f"  state={ft.state} delivered={len(ft.delivered)}/{ft.total_chunks} "
          f"delivered_bytes={ft.delivered_bytes} -> {'PASS' if chk else 'FAIL'}")

    chk = ft.retx_count > 0
    ok &= chk
    print(f"  retx={ft.retx_count} (>0 expected) -> {'PASS' if chk else 'FAIL'}")

    # Duplicates delivered by late originals must be deduped by the delivered set.
    chk = len(ft.delivered) == ft.total_chunks
    ok &= chk
    print(f"  delivered-set dedup size={len(ft.delivered)} "
          f"-> {'PASS' if chk else 'FAIL'}")

    cok, gen, acc = conservation(e)
    ok &= cok
    print(f"  conservation gen={gen} acc={acc} (retx inflate generated) "
          f"-> {'PASS' if cok else 'FAIL'}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 19: no-route recovery
# ----------------------------------------------------------------------

def test_file_no_route_recovery():
    hdr("Test 19: no-route drop then recovery once topology appears")
    ok = True

    e = PacketEngine({"route_refresh_interval": 0.3, "file_rto_s": 0.1}, seed=3)
    # Start with the source isolated: no links at all.
    e.sync_topology(LINEAR_NODES, [], transit=LINEAR_TRANSIT)
    e.sync_flows({})

    total = 80000
    ft = e.start_file("f1", "recover.bin", "UAV-0", "GS-0",
                      total_bytes=total, chunk_size=16384, rate_cap_bps=2e6)

    # Run a while with no route: chunks are dropped, ARQ keeps retrying.
    run(e, 100)
    drops_before = e.total_dropped
    chk = drops_before > 0 and ft.state == ps.FT_TRANSFERRING
    ok &= chk
    print(f"  isolated-phase drops={drops_before} state={ft.state} "
          f"-> {'PASS' if chk else 'FAIL'}")

    # Now the topology appears.
    e.sync_topology(LINEAR_NODES, LINEAR_EDGES, transit=LINEAR_TRANSIT)
    e._last_route_refresh = -float("inf")  # force an immediate route refresh
    run(e, 8000, until_complete="f1")

    chk = (ft.state == ps.FT_COMPLETE and ft.delivered_bytes == total
           and ft.retx_count > 0)
    ok &= chk
    print(f"  recovered state={ft.state} delivered_bytes={ft.delivered_bytes} "
          f"retx={ft.retx_count} -> {'PASS' if chk else 'FAIL'}")

    cok, gen, acc = conservation(e)
    ok &= cok
    print(f"  conservation gen={gen} acc={acc} -> {'PASS' if cok else 'FAIL'}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 20: coexistence with background Poisson traffic
# ----------------------------------------------------------------------

def test_file_background_coexistence():
    hdr("Test 20: file transfer coexists with background Poisson flows")
    ok = True

    e = PacketEngine(seed=23)
    nodes = ["UAV-0", "Ship-0", "Sat-0", "Sat-1", "GS-0"]
    edges = [
        ("UAV-0", "Sat-0", "sul", 0.004),
        ("Ship-0", "Sat-0", "ssl", 0.005),
        ("Sat-0", "Sat-1", "isl", 0.008),
        ("Sat-1", "GS-0", "gsl", 0.004),
        ("Sat-0", "GS-0", "gsl", 0.006),
    ]
    e.sync_topology(nodes, edges, transit={"Sat-0", "Sat-1"})
    e.sync_flows(
        {"UAV-0": "GS-0", "Ship-0": "GS-0"},
        flow_rate={"UAV-0": 300.0, "Ship-0": 200.0},
        flow_prio={"UAV-0": ps.PRIO_HIGH, "Ship-0": ps.PRIO_BEST_EFFORT},
    )

    total = 120000
    ft = e.start_file("f1", "coexist.bin", "UAV-0", "GS-0",
                      total_bytes=total, chunk_size=16384, rate_cap_bps=2e6)
    run(e, 4000, until_complete="f1")

    chk = ft.state == ps.FT_COMPLETE and ft.delivered_bytes == total
    ok &= chk
    print(f"  file state={ft.state} delivered_bytes={ft.delivered_bytes} "
          f"-> {'PASS' if chk else 'FAIL'}")

    # Background traffic must also have been delivered (well beyond the file's
    # own chunk count), proving the two planes share the engine without harm.
    chk = e.total_delivered > ft.total_chunks + 100
    ok &= chk
    print(f"  total_delivered={e.total_delivered} (file chunks="
          f"{ft.total_chunks}, background contributed) "
          f"-> {'PASS' if chk else 'FAIL'}")

    cok, gen, acc = conservation(e)
    ok &= cok
    print(f"  conservation gen={gen} acc={acc} -> {'PASS' if cok else 'FAIL'}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 21: multi-file concurrency + QoS priority + cancel
# ----------------------------------------------------------------------

def test_file_multi_priority_cancel():
    hdr("Test 21: multi-file concurrency, QoS priority ordering, cancel")
    ok = True

    # Congested bottleneck: a tiny-queue, low-capacity downlink both files share.
    cfg = {
        "queue_capacity_pkts": 4,
        "file_rto_s": 0.15,
        "route_refresh_interval": 1.0,
        "capacity": {"isl": 1e10, "gsl": 8e5, "sul": 5e8, "ssl": 5e8},
    }
    e = PacketEngine(cfg, seed=31)
    e.sync_topology(LINEAR_NODES, LINEAR_EDGES, transit=LINEAR_TRANSIT)
    e.sync_flows({})

    size = 160000
    hi = e.start_file("hi", "hi.bin", "UAV-0", "GS-0", total_bytes=size,
                      chunk_size=16384, prio=ps.PRIO_HIGH, rate_cap_bps=4e6)
    lo = e.start_file("lo", "lo.bin", "UAV-0", "GS-0", total_bytes=size,
                      chunk_size=16384, prio=ps.PRIO_BEST_EFFORT, rate_cap_bps=4e6)
    cx = e.start_file("cx", "cx.bin", "UAV-0", "GS-0", total_bytes=size,
                      chunk_size=16384, prio=ps.PRIO_BEST_EFFORT, rate_cap_bps=4e6)

    # Let the cancelled transfer get partway, then cancel it.
    run(e, 60)
    e.cancel_file("cx")
    chk = cx.state == ps.FT_CANCELLED
    ok &= chk
    print(f"  cancel state={cx.state} -> {'PASS' if chk else 'FAIL'}")

    run(e, 12000)

    chk = (hi.state == ps.FT_COMPLETE and lo.state == ps.FT_COMPLETE
           and hi.delivered_bytes == size and lo.delivered_bytes == size)
    ok &= chk
    print(f"  hi={hi.state}/{hi.delivered_bytes}B lo={lo.state}/"
          f"{lo.delivered_bytes}B -> {'PASS' if chk else 'FAIL'}")

    # Strict-priority scheduling: the high-priority file must not finish later
    # than the best-effort one through the same congested bottleneck.
    chk = (hi.complete_time is not None and lo.complete_time is not None
           and hi.complete_time <= lo.complete_time + 1e-9)
    ok &= chk
    print(f"  priority order hi.t={hi.complete_time:.3f} lo.t="
          f"{lo.complete_time:.3f} -> {'PASS' if chk else 'FAIL'}")

    chk = cx.state == ps.FT_CANCELLED  # stayed cancelled, never completed
    ok &= chk
    print(f"  cancelled stayed CANCELLED -> {'PASS' if chk else 'FAIL'}")

    states = e.file_states()
    chk = {"hi", "lo", "cx"}.issubset(states.keys()) \
        and states["hi"]["state"] == "COMPLETE" \
        and states["cx"]["state"] == "CANCELLED"
    ok &= chk
    print(f"  file_states lists all 3 with correct states "
          f"-> {'PASS' if chk else 'FAIL'}")

    cok, gen, acc = conservation(e)
    ok &= cok
    print(f"  conservation gen={gen} acc={acc} -> {'PASS' if cok else 'FAIL'}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    results = [
        ("17 file-basic", test_file_basic()),
        ("18 file-arq", test_file_arq_loss()),
        ("19 file-noroute", test_file_no_route_recovery()),
        ("20 file-coexist", test_file_background_coexistence()),
        ("21 file-multi", test_file_multi_priority_cancel()),
    ]

    print()
    print("=" * 64)
    for name, ok in results:
        print(f"  Test {name:18s} {'PASS' if ok else 'FAIL'}")
    print("=" * 64)
    print("ALL PASS" if all(r for _, r in results) else "SOME FAILED")
    sys.exit(0 if all(r for _, r in results) else 1)
