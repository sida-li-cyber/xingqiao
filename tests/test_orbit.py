"""
Phase 8 (v4) tests — orbital realism: SGP4 ephemeris, synthetic & real TLE,
geometric ISL, and regression of the circular-orbit baseline.

  Test O1: SGP4 vs Vallado reference vectors - the canonical 1958-002B
           (Vanguard) test case from SGP4-VER.TST reproduced through our
           JD/epoch pipeline at t=0 / 360 / 1440 min.
  Test O2: ECI -> geodetic conversion sanity - equator / pole points and
           GMST drift (~15 deg/hour) of a fixed ECI vector.
  Test O3: synthetic TLE writer - 69-char lines, valid modulo-10 checksums,
           elements survive a parse round-trip within format precision.
  Test O4: circular vs synthetic-SGP4 divergence - bounded model spread at
           t=0 (J2 osculating terms + WGS84 vs spherical coordinates).
  Test O5: determinism - identical epoch => bit-identical trajectories.
  Test O6: full pipeline offline in SGP4 mode - exact packet conservation,
           real delivery, tick headroom.
  Test O7: real TLE sample + geometric ISL - 100 Starlink satellites from
           the bundled Celestrak subset, k-NN ISL degree / range sanity,
           conservation across a topology recompute.
  Test O8: (--long only) 1584-sat synthetic SGP4 stress - >= 20 ticks/s.

Run:  python test_orbit.py          (O1-O7, ~1 min)
      python test_orbit.py --long   (adds O8)
"""

import math
import os
import sys
import time
from datetime import datetime, timezone

import pytest

# Engine modules live in <root>/hypatia-master/satviz; tests in <root>/tests.
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

import ephemeris as eph
from ephemeris import (
    SGP4Provider, CircularProvider,
    make_tle_lines, attach_synthetic_sgp4,
    eci_to_geodetic, gmst_rad, datetime_to_jd,
)
from demo_sim_core import (
    DemoSimCore, create_constellation,
    haversine_km,
)
from sgp4.api import Satrec, WGS72

SAMPLE_TLE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz",
    "data", "starlink_sample.tle"))

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def hdr(title):
    print("=" * 64)
    print(title)
    print("=" * 64)


def step(core):
    core.sim_time += core.update_interval * core.speed
    return core.get_state_update()


def conservation_ok(eng):
    generated = sum(eng.n_generated.values())
    accounted = eng.total_delivered + eng.total_dropped + eng.in_flight
    return accounted == generated, generated, accounted


# ----------------------------------------------------------------------
# Test O1: Vallado reference vectors
# ----------------------------------------------------------------------

VALLADO_L1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
VALLADO_L2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"

# Published SGP4-VER.TST position vectors (km, TEME) for this TLE.
VALLADO_REF = {
    0.0:    (7022.46529743, -1400.08295143, 0.03996296),
    360.0:  (-7154.03119008, -3783.17683548, -3536.19412821),
    1440.0: (-938.55922047, -6268.18748939, -4294.02924502),
}


def test_vallado_vectors():
    hdr("Test O1: SGP4 vs Vallado reference vectors (1958-002B)")
    satrec = Satrec.twoline2rv(VALLADO_L1, VALLADO_L2, WGS72)
    prov = SGP4Provider(satrec)

    ok = True
    for minutes, ref in VALLADO_REF.items():
        r, _v = prov.get_state_eci(minutes * 60.0)
        err = math.sqrt(sum((a - b) ** 2 for a, b in zip(r, ref)))
        tol = 1e-6 if minutes == 0.0 else 1e-3
        line_ok = err < tol
        ok &= line_ok
        print(f"  t={minutes:6.0f} min  r=[{r[0]:15.8f}, {r[1]:15.8f}, "
              f"{r[2]:15.8f}]  err={err:.2e} km  "
              f"-> {'PASS' if line_ok else 'FAIL'}")

    # Geodetic output must be finite and at plausible Vanguard altitude
    # (perigee ~654 km, apogee ~3969 km).
    lat, lon, alt_m = prov.get_position(0.0)
    alt_km = alt_m / 1000.0
    geo_ok = (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and
              300.0 < alt_km < 4500.0)
    ok &= geo_ok
    print(f"  geodetic @t=0: lat={lat:.4f} lon={lon:.4f} alt={alt_km:.1f} km"
          f"  -> {'PASS' if geo_ok else 'FAIL'}")
    assert ok, "Vallado reference vectors mismatch"


# ----------------------------------------------------------------------
# Test O2: ECI -> geodetic conversion
# ----------------------------------------------------------------------

def test_eci_to_geodetic():
    hdr("Test O2: ECI -> geodetic conversion sanity")
    ok = True

    jd = datetime_to_jd(EPOCH)

    # Point on the equator in ECEF: the ECI vector (r*cos(th), r*sin(th), 0)
    # lands exactly on the ECEF x-axis after the GMST rotation, so its
    # geodetic longitude must be 0.
    r_eq = 6378.137 + 500.0
    th = gmst_rad(jd)
    r_eci = (r_eq * math.cos(th), r_eq * math.sin(th), 0.0)
    lat, lon, alt = eci_to_geodetic(r_eci, jd)
    eq_ok = abs(lat) < 1e-6 and abs(lon) < 1e-6 and abs(alt - 500.0) < 1e-6
    ok &= eq_ok
    print(f"  equator point: lat={lat:.2e} lon={lon:.2e} alt={alt:.6f} km"
          f"  -> {'PASS' if eq_ok else 'FAIL'}")

    # North pole: z-axis point, geodetic altitude = z - b (polar radius).
    b = 6378.137 * math.sqrt(1.0 - eph.WGS84_E2)
    z = b + 700.0
    lat, lon, alt = eci_to_geodetic((0.0, 0.0, z), jd)
    pole_ok = abs(lat - 90.0) < 1e-6 and abs(alt - 700.0) < 1e-3
    ok &= pole_ok
    print(f"  pole point:    lat={lat:.6f} alt={alt:.6f} km"
          f"  -> {'PASS' if pole_ok else 'FAIL'}")

    # GMST drift: a fixed ECI vector's sub-satellite longitude must shift
    # westward by ~360.985 deg per sidereal day as the Earth rotates.
    r_fixed = (7000.0, 0.0, 0.0)
    _, lon0, _ = eci_to_geodetic(r_fixed, jd)
    _, lon1, _ = eci_to_geodetic(r_fixed, jd + 1.0 / 24.0)  # +1 hour
    dlon = ((lon1 - lon0 + 540.0) % 360.0) - 180.0
    drift_ok = abs(dlon - (-360.98564736629 / 24.0)) < 1e-3
    ok &= drift_ok
    print(f"  GMST drift:    dlon/hour={dlon:.6f} deg (expect "
          f"{-360.98564736629 / 24.0:.6f})  -> {'PASS' if drift_ok else 'FAIL'}")
    assert ok, "ECI -> geodetic conversion failed"


# ----------------------------------------------------------------------
# Test O3: synthetic TLE writer
# ----------------------------------------------------------------------

def test_synthetic_tle_format():
    hdr("Test O3: synthetic TLE writer format & round-trip")
    ok = True

    l1, l2 = make_tle_lines(80001, 53.0, 10.0, 35.0, 550.0, EPOCH,
                            intl_year=26, intl_launch=1, piece_idx=0)

    fmt_ok = len(l1) == 69 and len(l2) == 69
    ok &= fmt_ok
    print(f"  line lengths: {len(l1)}, {len(l2)}  "
          f"-> {'PASS' if fmt_ok else 'FAIL'}")

    def cksum_valid(line):
        body, digit = line[:-1], int(line[-1])
        total = sum(int(c) for c in body if c.isdigit()) + body.count("-")
        return total % 10 == digit

    ck_ok = cksum_valid(l1) and cksum_valid(l2)
    ok &= ck_ok
    print(f"  checksums:    L1={cksum_valid(l1)} L2={cksum_valid(l2)}  "
          f"-> {'PASS' if ck_ok else 'FAIL'}")

    satrec = Satrec.twoline2rv(l1, l2, WGS72)
    inc = math.degrees(satrec.inclo)
    raan = math.degrees(satrec.nodeo)
    ma = math.degrees(satrec.mo)
    n_rev_day = satrec.no_kozai * 1440.0 / (2.0 * math.pi)
    n_expected = eph.mean_motion_rev_day(550.0)

    rt_ok = (abs(inc - 53.0) < 1e-4 and abs(raan - 10.0) < 1e-4 and
             abs(ma - 35.0) < 1e-4 and abs(n_rev_day - n_expected) < 1e-4)
    ok &= rt_ok
    print(f"  round-trip:   inc={inc:.4f} raan={raan:.4f} M={ma:.4f} "
          f"n={n_rev_day:.6f} (expect {n_expected:.6f})  "
          f"-> {'PASS' if rt_ok else 'FAIL'}")
    assert ok, "synthetic TLE format/round-trip failed"


# ----------------------------------------------------------------------
# Test O4: circular vs synthetic-SGP4 divergence
# ----------------------------------------------------------------------

def test_circular_vs_sgp4():
    hdr("Test O4: circular vs synthetic-SGP4 divergence at t=0")

    sats = create_constellation(6, 12)
    circ_pos = [CircularProvider(
        s.altitude_km, s.inclination_rad, s.raan_rad,
        s.mean_anomaly_rad).get_position(0.0) for s in sats]
    attach_synthetic_sgp4(sats, EPOCH)
    sgp4_pos = [s.get_position(0.0) for s in sats]

    max_chord = 0.0
    max_dalt = 0.0
    # The circular model treats RAAN as earth-fixed at t=0 (it ignores
    # GMST), so every SGP4 longitude carries one constant frame-rotation
    # offset equal to GMST at the epoch. Remove that global rotation
    # (circular mean of the per-satellite longitude differences) before
    # bounding the genuine per-satellite model spread.
    sins = sum(math.sin(math.radians(s[1] - c[1]))
               for c, s in zip(circ_pos, sgp4_pos))
    coss = sum(math.cos(math.radians(s[1] - c[1]))
               for c, s in zip(circ_pos, sgp4_pos))
    lon_offset = math.degrees(math.atan2(sins, coss))
    for (clat, clon, _ca), (slat, slon, salt) in zip(circ_pos, sgp4_pos):
        clon_aligned = ((clon + lon_offset + 540.0) % 360.0) - 180.0
        # 3D chord through the shell (haversine on the mean sphere + the
        # radial difference), good enough to bound the model spread.
        ground = haversine_km(clat, clon_aligned, slat, slon)
        radial = abs(salt / 1000.0 - 550.0)
        chord = math.sqrt(ground ** 2 + radial ** 2)
        max_chord = max(max_chord, chord)
        max_dalt = max(max_dalt, abs(salt / 1000.0 - 550.0))

    # Expected residual spread: J2 mean->osculating periodic terms (~km) +
    # WGS84 geodetic vs spherical geocentric latitude (<= ~22 km) + the
    # 7 km radius offset between the two Earth models. 50 km is a
    # generous envelope; this is model difference, not propagation error
    # (which Test O1 bounds against Vallado's reference vectors).
    ok = max_chord < 50.0 and max_dalt < 25.0
    print(f"  frame offset removed: {lon_offset:.3f} deg (= GMST at epoch)")
    print(f"  72 sats @t=0: max position spread = {max_chord:.2f} km, "
          f"max altitude deviation = {max_dalt:.2f} km")
    print(f"  envelope: chord < 50 km, |dalt| < 25 km  "
          f"-> {'PASS' if ok else 'FAIL'}")

    # J2 nodal precession must appear over time (RAAN drifts westward for
    # prograde orbits): after one day the SGP4 ground track is far from the
    # circular prediction, proving perturbations are genuinely active.
    t_day = 86400.0
    drift = haversine_km(*circ_pos[0][:2], *sats[0].get_position(t_day)[:2])
    drift_ok = drift > 100.0
    ok &= drift_ok
    print(f"  J2 activity:  1-day ground-track separation = {drift:.0f} km"
          f"  (>100 km expected)  -> {'PASS' if drift_ok else 'FAIL'}")
    assert ok, "circular vs SGP4 divergence out of bounds"


# ----------------------------------------------------------------------
# Test O5: determinism
# ----------------------------------------------------------------------

def test_determinism():
    hdr("Test O5: synthetic SGP4 determinism (same epoch => same orbit)")

    def run():
        sats = create_constellation(6, 12)
        attach_synthetic_sgp4(sats, EPOCH)
        return [s.get_position(1234.5) for s in sats]

    a, b = run(), run()
    ok = all(pa == pb for pa, pb in zip(a, b))
    print(f"  72 sats @t=1234.5 s: bit-identical across runs = {ok}  "
          f"-> {'PASS' if ok else 'FAIL'}")
    assert ok, "synthetic SGP4 not deterministic"


# ----------------------------------------------------------------------
# Test O6: full pipeline offline in SGP4 mode
# ----------------------------------------------------------------------

def test_pipeline_sgp4():
    hdr("Test O6: full pipeline offline (72 sat, SGP4, 60 s sim)")

    core = DemoSimCore(scale=72, ephemeris="sgp4", epoch=EPOCH)
    eng = core.engine

    t0 = time.perf_counter()
    ticks = 0
    while core.sim_time < 60.0:
        step(core)
        ticks += 1
    elapsed = time.perf_counter() - t0

    cons_ok, generated, accounted = conservation_ok(eng)
    delivered = eng.total_delivered
    rate = ticks / elapsed

    ok = (cons_ok and delivered > 0 and rate >= 20.0)
    print(f"  {ticks} ticks in {elapsed:.2f} s ({rate:.1f} ticks/s), "
          f"delivered={delivered}, dropped={eng.total_dropped}, "
          f"in_flight={eng.in_flight}")
    print(f"  conservation: generated={generated} == "
          f"accounted={accounted} -> {'PASS' if cons_ok else 'FAIL'}")
    print(f"  delivery > 0 and rate >= 20 ticks/s  "
          f"-> {'PASS' if delivered > 0 and rate >= 20.0 else 'FAIL'}")
    assert ok, "pipeline SGP4 offline failed"


# ----------------------------------------------------------------------
# Test O7: real TLE sample + geometric ISL
# ----------------------------------------------------------------------

def test_real_tle_geometric_isl():
    hdr("Test O7: real Starlink TLE sample + geometric ISL")
    ok = True

    core = DemoSimCore(tle_file=SAMPLE_TLE, epoch=EPOCH)
    n = len(core.satellites)
    n_ok = n == 100 and all(s.shell < 0 for s in core.satellites) and \
        all(isinstance(s.provider, SGP4Provider) for s in core.satellites)
    ok &= n_ok
    print(f"  loaded {n} satellites (unstructured, SGP4)  "
          f"-> {'PASS' if n_ok else 'FAIL'}")

    # Geometric ISL sanity: undirected, within range, no self-links, and
    # every satellite reaches at least 2 neighbours (k=4 selection, sparse
    # catalog may prune some).
    pairs = core.isl_links
    ids = {s.id for s in core.satellites}
    deg = {sid: 0 for sid in ids}
    range_ok = True
    pos0 = {}
    for s in core.satellites:
        lat, lon, _alt = s.get_position(0.0)
        pos0[s.id] = (lat, lon)
    for a, b in pairs:
        if a == b or a not in ids or b not in ids:
            range_ok = False
            continue
        deg[a] += 1
        deg[b] += 1
        d = haversine_km(*pos0[a], *pos0[b])
        if d > 6000.0:
            range_ok = False
    min_deg = min(deg.values())
    isl_ok = range_ok and len(pairs) > 0 and min_deg >= 2
    ok &= isl_ok
    print(f"  geometric ISL: {len(pairs)} pairs, min degree = {min_deg}, "
          f"all within 6000 km = {range_ok}  "
          f"-> {'PASS' if isl_ok else 'FAIL'}")

    # init frame advertises geometric ISL + sgp4 metadata.
    init = core.get_init_message()["payload"]
    meta_ok = (init["version"] == "3.2" and
               init["ephemeris"]["mode"] == "sgp4" and
               init["ephemeris"]["isl"] == "geometric" and
               len(init["sat_order"]) == 100)
    ok &= meta_ok
    print(f"  init: version={init['version']} ephemeris={init['ephemeris']}"
          f"  -> {'PASS' if meta_ok else 'FAIL'}")

    # Run across one topology-recompute boundary (interval = 60 s) and
    # verify packet conservation stays exact through the ISL churn.
    eng = core.engine
    while core.sim_time < 130.0:
        step(core)
    cons_ok, generated, accounted = conservation_ok(eng)
    ok &= cons_ok and eng.total_delivered > 0
    print(f"  130 s sim across recompute boundary: delivered="
          f"{eng.total_delivered}, dropped={eng.total_dropped} "
          f"(handover={eng.total_handover_dropped}), "
          f"generated={generated} == accounted={accounted}  "
          f"-> {'PASS' if cons_ok and eng.total_delivered > 0 else 'FAIL'}")
    assert ok, "real TLE + geometric ISL failed"


# ----------------------------------------------------------------------
# Test O8: 1584-sat synthetic SGP4 stress (--long)
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_sgp4_1584_stress():
    hdr("Test O8: 1584-sat synthetic SGP4 stress (300 ticks)")

    core = DemoSimCore(scale=1584, ephemeris="sgp4", epoch=EPOCH)
    eng = core.engine

    t0 = time.perf_counter()
    ticks = 0
    while ticks < 300:
        step(core)
        ticks += 1
    elapsed = time.perf_counter() - t0

    cons_ok, generated, accounted = conservation_ok(eng)
    rate = ticks / elapsed
    ok = cons_ok and rate >= 20.0
    print(f"  {ticks} ticks in {elapsed:.2f} s ({rate:.1f} ticks/s, "
          f"target >= 20), delivered={eng.total_delivered}")
    print(f"  conservation: generated={generated} == "
          f"accounted={accounted}  -> {'PASS' if cons_ok else 'FAIL'}")
    print(f"  tick rate  -> {'PASS' if rate >= 20.0 else 'FAIL'}")
    assert ok, "1584-sat SGP4 stress failed"
