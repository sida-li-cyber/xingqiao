"""
Phase 7 tests — thousand-satellite scale: constellation generation sanity,
spatial-grid correctness, protocol 3.1 frame structure, and offline stress.

  Test 12: generation sanity - preset counts / unique IDs / ISL = 2N /
           min degree >= 4; legacy 72 geometry identical to the default
           generator; multi-shell 3-part IDs.
  Test 13: spatial grid == brute force - grid_candidates prefilter yields
           exactly the same visible set as the full scan, incl. antimeridian
           and polar observers.
  Test 14: protocol 3.1 frame structure - init carries sat_order +
           isl_topology; state frames carry aligned sat_pos arrays, dynamic-
           only positions, short-key links, links_full / links_removed.
  Test 15: 440-sat offline stress (120 s sim) - exact conservation, real
           delivery, >= 20 ticks/s, steady frames < 100 KB, no degradation.
  Test 16: (--long only) 1584-sat offline stress (600 s sim) - same
           invariants plus observed link churn and handover accounting.

Run:  python test_phase7.py          (tests 12-15, ~40 s)
      python test_phase7.py --fast   (skip the 440 stress, ~15 s)
      python test_phase7.py --long   (add the 1584 / 600 s run, ~2 min)
"""

import json
import sys
import time

import demo_sim_core as dsc
from demo_sim_core import DemoSimCore, create_constellation


def hdr(title):
    print("=" * 64)
    print(title)
    print("=" * 64)


def step(core):
    core.sim_time += core.update_interval * core.speed
    return core.get_state_update()


# ----------------------------------------------------------------------
# Test 12: generation sanity
# ----------------------------------------------------------------------

def test_generation_sanity():
    hdr("Test 12: constellation generation sanity")
    ok = True

    for preset in (72, 440, 1584):
        core = DemoSimCore(scale=preset)
        sats = core.satellites
        ids = [s.id for s in sats]
        n = len(sats)

        # degree from the static ISL topology
        deg = {}
        for a, b in core.isl_links:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        min_deg = min(deg.values()) if deg else 0
        # "Sat-{plane}-{idx}" -> 3 dash tokens; multi-shell adds the shell.
        parts = {len(i.split("-")) for i in ids}

        c_ok = (n == preset and
                len(set(ids)) == n and
                len(core.isl_links) == 2 * preset and
                min_deg >= 4 and
                parts == {3})          # single shell -> Sat-plane-idx
        ok &= c_ok
        print(f"  preset {preset:5d}: n={n} isl={len(core.isl_links)} "
              f"min_deg={min_deg} id_parts={sorted(parts)} "
              f"-> {'PASS' if c_ok else 'FAIL'}")

    # Legacy compatibility: scale=72 must reproduce the default generator
    # geometry exactly (same IDs, same positions at arbitrary times).
    legacy = DemoSimCore()                 # _shells=None path
    preset72 = DemoSimCore(scale=72)       # SCALE_PRESETS[72] (legacy_stagger)
    lid = [s.id for s in legacy.satellites]
    pid = [s.id for s in preset72.satellites]
    same = lid == pid
    for t in (0.0, 123.45):
        for a, b in zip(legacy.satellites, preset72.satellites):
            if a.get_position(t) != b.get_position(t):
                same = False
                break
    ok &= same
    print(f"  legacy 72 == default generator (ids+positions): "
          f"-> {'PASS' if same else 'FAIL'}")

    # Multi-shell generator -> 3-part IDs and summed counts.
    shells = [
        {"planes": 4, "sats_per_plane": 6, "altitude_km": 550.0,
         "inclination_deg": 53.0},
        {"planes": 4, "sats_per_plane": 6, "altitude_km": 1000.0,
         "inclination_deg": 70.0},
    ]
    multi = create_constellation(shells=shells)
    m_ids = [s.id for s in multi]
    m_ok = (len(multi) == 48 and
            len(set(m_ids)) == 48 and
            {len(i.split("-")) for i in m_ids} == {4} and
            m_ids[0] == "Sat-0-0-0" and m_ids[-1] == "Sat-1-3-5")
    ok &= m_ok
    print(f"  multi-shell 3-part IDs (n={len(multi)}, "
          f"first={m_ids[0]}, last={m_ids[-1]}): "
          f"-> {'PASS' if m_ok else 'FAIL'}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 13: spatial grid == brute force
# ----------------------------------------------------------------------

def test_grid_equals_brute_force():
    hdr("Test 13: spatial-grid prefilter == full scan")

    core = DemoSimCore(scale=440)
    observers = [(gs[0], gs[1]) for gs in core.ground_stations.values()]
    observers += [(0.0, 179.5), (60.0, -179.9), (-85.0, 10.0),
                  (85.0, 10.0), (23.5, 114.1), (-33.9, 18.4)]

    mismatches = 0
    checked = 0
    for t in (0.0, 37.7, 123.45, 500.0):
        sat_pos = {}
        for s in core.satellites:
            lat, lon, alt = s.get_position(t)
            sat_pos[s.id] = {"lat": lat, "lon": lon, "alt": alt}
        grid = dsc.build_sat_grid(sat_pos)
        for olat, olon in observers:
            for elev in (5.0, 10.0):
                full = dsc.visible_satellites(olat, olon, sat_pos, elev)
                cand = dsc.grid_candidates(grid, olat, olon)
                gridded = dsc.visible_satellites(olat, olon, sat_pos, elev,
                                                 candidates=cand)
                checked += 1
                # Compare membership, not order: exact distance ties sort
                # differently because the two paths feed different input
                # orders (dict order vs grid-cell order).
                if sorted(i for i, _ in full) != sorted(i for i, _ in gridded):
                    mismatches += 1
                    print(f"    MISMATCH t={t} obs=({olat},{olon}) "
                          f"elev={elev}: full={len(full)} grid={len(gridded)}")

    ok = mismatches == 0 and checked > 0
    print(f"  checked {checked} observer/elev/time combos, "
          f"mismatches={mismatches}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Test 14: protocol 3.1 frame structure
# ----------------------------------------------------------------------

def test_protocol_31_structure():
    hdr("Test 14: protocol 3.1 frame structure")

    core = DemoSimCore(scale=72)
    init = core.get_init_message()["payload"]
    gs_names = set(core.ground_stations.keys())

    init_ok = (init["version"] == "3.1" and
               len(init["sat_order"]) == 72 and
               len(init["isl_topology"]) == 144 and
               all("capacity_bps" in lt
                   for lt in init["link_types"].values()))
    print(f"  init: version={init['version']} "
          f"sat_order={len(init['sat_order'])} "
          f"isl_topology={len(init['isl_topology'])} "
          f"-> {'PASS' if init_ok else 'FAIL'}")

    f1 = step(core)["payload"]
    short_keys = {"t", "u", "l", "d", "tx", "q", "p"}
    pos_keys = set(f1["positions"].keys())
    f1_ok = (f1["links_full"] is True and
             f1["links_removed"] == [] and
             len(f1["sat_pos"]) == 72 and
             all(isinstance(p, list) and len(p) == 2
                 for p in f1["sat_pos"]) and
             len(f1["links"]) > 0 and
             all(set(lk.keys()) <= short_keys
                 for lk in f1["links"].values()) and
             not any(k.startswith("Sat-") for k in pos_keys) and
             not (pos_keys & gs_names))
    print(f"  frame1: links_full={f1['links_full']} "
          f"sat_pos={len(f1['sat_pos'])} links={len(f1['links'])} "
          f"dyn_positions={sorted(pos_keys)[:3]}... "
          f"-> {'PASS' if f1_ok else 'FAIL'}")

    f2 = step(core)["payload"]
    f2_ok = (f2["links_full"] is False and
             isinstance(f2["links_removed"], list) and
             all(set(lk.keys()) <= short_keys
                 for lk in f2["links"].values()))
    print(f"  frame2: links_full={f2['links_full']} "
          f"links={len(f2['links'])} removed={len(f2['links_removed'])} "
          f"-> {'PASS' if f2_ok else 'FAIL'}")

    ok = init_ok and f1_ok and f2_ok
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ----------------------------------------------------------------------
# Offline stress harness
# ----------------------------------------------------------------------

def run_offline(scale, n_ticks, label):
    hdr(label)
    core = DemoSimCore(scale=scale)
    core.get_init_message()

    max_frame = 0
    churn = 0
    first_block = last_block = None
    blk_t0 = time.time()
    t0 = time.time()
    for i in range(n_ticks):
        if i % 100 == 0:
            blk_t0 = time.time()
        su = step(core)
        blob = json.dumps(su)
        if len(blob) > max_frame:
            max_frame = len(blob)
        churn += len(su["payload"].get("links_removed", []))
        if i % 100 == 99:
            blk = time.time() - blk_t0
            if first_block is None:
                first_block = blk
            last_block = blk
    wall = time.time() - t0
    rate = n_ticks / wall

    eng = core.engine
    generated = sum(eng.n_generated.values())
    accounted = eng.total_delivered + eng.total_dropped + eng.in_flight
    print(f"  sim_time={core.sim_time:.0f}s  wall={wall:.1f}s  "
          f"rate={rate:.1f} ticks/s")
    print(f"  max_frame={max_frame / 1024:.1f} KB  links_removed={churn}")
    print(f"  delivered={eng.total_delivered}  dropped={eng.total_dropped}  "
          f"handover={eng.total_handover_dropped}  in_flight={eng.in_flight}")
    print(f"  conservation: generated={generated} == accounted={accounted}")
    print(f"  first100={first_block:.2f}s  last100={last_block:.2f}s")

    ok = (generated == accounted and
          eng.total_delivered > 0 and
          rate >= 20.0 and
          max_frame < 100 * 1024 and
          eng.in_flight < eng.cfg["max_in_flight"] and
          last_block < 3.0 * first_block)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok, churn


def test_stress_440():
    ok, _ = run_offline(440, 600, "Test 15: 440-sat offline stress (120 s)")
    return ok


def test_stress_1584():
    ok, churn = run_offline(1584, 3000,
                            "Test 16: 1584-sat offline stress (600 s)")
    if churn == 0:
        print("  WARN: no link churn observed over 600 s sim")
    return ok and churn > 0


# ----------------------------------------------------------------------

if __name__ == "__main__":
    fast = "--fast" in sys.argv
    long = "--long" in sys.argv

    results = [
        ("12 generation", test_generation_sanity()),
        ("13 grid==brute", test_grid_equals_brute_force()),
        ("14 protocol-3.1", test_protocol_31_structure()),
    ]
    if not fast:
        results.append(("15 stress-440", test_stress_440()))
    if long:
        results.append(("16 stress-1584", test_stress_1584()))

    print()
    print("=" * 64)
    for name, ok in results:
        print(f"  Test {name:18s} {'PASS' if ok else 'FAIL'}")
    print("=" * 64)
    print("ALL PASS" if all(r for _, r in results) else "SOME FAILED")
    sys.exit(0 if all(r for _, r in results) else 1)
