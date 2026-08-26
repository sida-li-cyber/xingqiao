"""
Demo Simulation Core v2 — 多域节点（卫星 + 无人机 + 船舶 + 地面站）

Simulates a Starlink-like LEO constellation, UAV formations over the
South China Sea, and ships along major maritime routes. Streams state
updates to the realtime_backend via WebSocket using protocol v2.

Usage:
    # Start the realtime backend first:
    python -m realtime_backend.run

    # Then in another terminal:
    python demo_sim_core.py

    # Options:
    python demo_sim_core.py --host localhost --port 8000
    python demo_sim_core.py --num-uavs 8 --num-ships 10
"""

import asyncio
import argparse
import json
import math
import time
import random
import sys
from datetime import datetime, timezone

try:
    import websockets
except ImportError:
    print("Error: websockets library not installed.")
    print("Install it with: pip install websockets")
    sys.exit(1)

# Protocol v3 Phase 2: packet-level discrete-event simulation engine
from packet_sim import PacketEngine, PRIO_HIGH, PRIO_BEST_EFFORT

# 真实船舶图层：AIS 轨迹回放（tools/ais_tools.py 生成的轨迹 JSON）
from ais_replay import load_ais_tracks

# Phase 8 (v3): pluggable ephemeris providers (circular / SGP4) and TLE
# tooling, copied verbatim from the v4 research codebase. CircularProvider
# reproduces the previous inline Keplerian math exactly, so the default
# mode stays regression-identical.
from ephemeris import (
    CircularProvider, SGP4Provider, attach_synthetic_sgp4,
    load_tle_file, satrec_nominal_altitude_km,
)
# 教学实验沙箱（改进 #2 / 阶段 C + 改进计划 W/S）：独立 PacketEngine，
# 与主仿真互不干扰；支持最多 MAX_CONCURRENT_EXPERIMENTS 个实验并行（多客户端机房场景）。
from experiments import (
    experiment_catalog, grade_quiz, run_experiment,
    ExperimentCancelled, ExperimentNotFound,
)

MAX_CONCURRENT_EXPERIMENTS = 4


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0
EARTH_MU = 398600.4418  # km^3/s^2
EARTH_ROTATION_RATE = 360.0 / 86400.0  # deg/s

# GSL: each ground station keeps feeder links to its K nearest *visible*
# satellites (10 deg elevation mask ~= 2060 km footprint at 550 km altitude,
# close to the historical 2000 km connect threshold). Capping fan-in forces
# traffic to traverse the ISL mesh toward a feeder satellite instead of
# always taking a two-hop direct downlink, and keeps the GSL count (and the
# state frame) small at thousand-satellite scale.
GSL_MAX_LINKS = 4          # feeder links per ground station
GSL_MIN_ELEV_DEG = 10.0    # minimum satellite elevation for a feeder link

# SUL / SSL: each UAV / ship uplinks to its K nearest *visible* satellites.
# A satellite is "visible" when it sits above a minimum elevation angle
# (real line-of-sight mask), so links are geometrically plausible and
# hand over naturally as the constellation sweeps overhead. This guarantees
# every UAV / ship always has at least one live uplink, unlike a fixed
# distance threshold which the sparse 72-sat constellation rarely satisfies.
SUL_MAX_LINKS = 2        # uplinks per UAV
SSL_MAX_LINKS = 2        # uplinks per ship
SUL_MIN_ELEV_DEG = 5.0   # minimum satellite elevation for a usable uplink
SSL_MIN_ELEV_DEG = 5.0

# --- Protocol v3: packet-level telemetry baselines (Phase 1 placeholders) ---
# Phase 1 emits these as constants / geometry-derived values; Phase 2 replaces
# them with measurements emerging from the discrete-event packet simulator.
SPEED_OF_LIGHT_KM_S = 299792.458

LINK_CAPACITY_BPS = {
    "isl": 1e10,   # 10 Gbps inter-satellite laser link
    "gsl": 1e9,    # 1 Gbps feeder / user link
    "sul": 5e8,    # 500 Mbps UAV uplink
    "ssl": 5e8,    # 500 Mbps ship uplink
}
PACKET_QUEUE_CAPACITY = 200    # per-output-port queue depth (packets)

# --- Protocol v3 Phase 2: DES traffic model (Poisson sources) ---
# UAVs and ships generate packets that are routed (hop-by-hop, store-and-
# forward) through the constellation to the nearest ground station.
# Phase 7 tuning: packets are aggregated 6000-byte units at lower per-source
# rates. Multi-hop ISL paths multiply the event volume per packet, so the
# lower pps keeps the DES affordable at thousand-satellite scale while the
# larger packets preserve visible utilization (~9.6% SUL / ~5.8% SSL).
PACKET_SIZE_BYTES = 6000       # aggregated packet size
UAV_FLOW_RATE_PPS = 1000.0     # packets/sec generated per UAV
SHIP_FLOW_RATE_PPS = 600.0     # packets/sec generated per ship
MAX_DES_STEP = 2.0             # larger sim-time jump => flush DES state

# --- Phase 7: thousand-satellite scale presets (single Walker-delta shells) ---
# 72   = legacy demo constellation (6 planes x 12, staggered), the default.
# 440  = 20 planes x 22, a medium-density shell.
# 1584 = Starlink Gen1 shell 1 (72 planes x 22 @ 550 km / 53 deg).
# Single-shell IDs stay "Sat-{plane}-{idx}" (backward compatible); only
# genuinely multi-shell constellations use "Sat-{shell}-{plane}-{idx}".
SCALE_PRESETS = {
    72: {"planes": 6, "sats_per_plane": 12,
         "altitude_km": 550.0, "inclination_deg": 53.0,
         "legacy_stagger": True},
    440: {"planes": 20, "sats_per_plane": 22,
          "altitude_km": 550.0, "inclination_deg": 53.0},
    1584: {"planes": 72, "sats_per_plane": 22,
           "altitude_km": 550.0, "inclination_deg": 53.0},
}

# --- Selectable constellations (named Walker-delta presets) ---
# demo72 / demo440 / starlink mirror the --scale presets above; kuiper
# and telesat reproduce the FCC filing geometries used by the Hypatia
# paper scripts (main_kuiper_630.py / main_telesat_1015.py). Each entry
# carries a display label plus a shell list for create_constellation().
CONSTELLATION_PRESETS = {
    "demo72": {"label": "演示星座 72 星",
               "shells": [dict(SCALE_PRESETS[72])]},
    "demo440": {"label": "演示星座 440 星",
                "shells": [dict(SCALE_PRESETS[440])]},
    "starlink": {"label": "Starlink Gen1 壳层1 (1584 星)",
                 "shells": [dict(SCALE_PRESETS[1584])]},
    "kuiper": {"label": "Kuiper-630 (1156 星)",
               "shells": [{"planes": 34, "sats_per_plane": 34,
                           "altitude_km": 630.0,
                           "inclination_deg": 51.9}]},
    "telesat": {"label": "Telesat Lightspeed (351 星)",
                "shells": [{"planes": 27, "sats_per_plane": 13,
                            "altitude_km": 1015.0,
                            "inclination_deg": 98.98}]},
}

# Legacy --scale values map onto the named presets (backward compat).
SCALE_TO_CONSTELLATION = {72: "demo72", 440: "demo440", 1584: "starlink"}


def validate_custom_shell(spec):
    """Validate a user-supplied single-shell spec (set_constellation).

    Returns a normalised shell dict for create_constellation(); raises
    ValueError with a human-readable reason when the spec is invalid.
    """
    def _num(key, lo, hi, cast=float):
        raw = spec.get(key)
        if raw is None:
            raise ValueError(f"custom shell missing '{key}'")
        try:
            val = cast(raw)
        except (TypeError, ValueError):
            raise ValueError(f"custom shell '{key}' is not a number")
        if not (lo <= val <= hi):
            raise ValueError(
                f"custom shell '{key}' out of range [{lo}, {hi}]")
        return val

    planes = int(_num("planes", 1, 80, int))
    spp = int(_num("sats_per_plane", 1, 40, int))
    alt = _num("altitude_km", 300.0, 2000.0)
    inc = _num("inclination_deg", 0.0, 120.0)
    if planes * spp > 1600:
        raise ValueError(
            f"custom shell too large: {planes * spp} sats (max 1600)")
    return {"planes": planes, "sats_per_plane": spp,
            "altitude_km": alt, "inclination_deg": inc}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in km."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return EARTH_RADIUS_KM * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_lon(lon):
    """Normalize longitude to [-180, 180]."""
    return ((lon + 180.0) % 360.0) - 180.0


def satellite_elevation_deg(obs_lat, obs_lon, sat_lat, sat_lon, sat_alt_km):
    """Elevation angle (deg) of a satellite as seen from an observer.

    Uses the standard single-shell visibility relation:
        tan(el) = (cos λ − ρ) / sin λ
    where λ is the great-circle (central) angle between observer and the
    satellite sub-point, and ρ = Re / (Re + h).
    """
    d = haversine_km(obs_lat, obs_lon, sat_lat, sat_lon)
    lam = d / EARTH_RADIUS_KM  # central angle in radians
    rho = EARTH_RADIUS_KM / (EARTH_RADIUS_KM + sat_alt_km)
    if abs(math.sin(lam)) < 1e-9:
        return 90.0  # satellite directly overhead
    return math.degrees(math.atan2(math.cos(lam) - rho, math.sin(lam)))


def visible_satellites(obs_lat, obs_lon, sat_pos, min_elev_deg,
                       candidates=None):
    """Return [(sat_id, ground_dist_km)] for satellites above min_elev_deg,
    sorted nearest-first.

    candidates: optional iterable of sat IDs to restrict the search to
    (Phase 7 spatial-grid prefilter); defaults to all of sat_pos.
    """
    ids = candidates if candidates is not None else sat_pos.keys()
    out = []
    for sat_id in ids:
        sp = sat_pos.get(sat_id)
        if sp is None:
            continue
        sat_alt_km = sp.get("alt", 550000.0) / 1000.0
        el = satellite_elevation_deg(obs_lat, obs_lon,
                                     sp["lat"], sp["lon"], sat_alt_km)
        if el >= min_elev_deg:
            out.append((sat_id,
                        haversine_km(obs_lat, obs_lon, sp["lat"], sp["lon"])))
    out.sort(key=lambda x: x[1])
    return out


# --- Phase 7: spatial grid prefilter for thousand-satellite constellations ---
# Buckets satellites into 10 deg x 10 deg lat/lon cells so visibility and
# ground-station queries only examine nearby satellites. A margin of 3 cells
# (30 deg) covers both the ~20 deg visibility cone at 550 km / 5 deg elevation
# mask and the 2200 km GSL disconnect range.
GRID_CELL_DEG = 10.0
GRID_MARGIN_CELLS = 3


def _grid_key(lat, lon):
    return (int(math.floor(lat / GRID_CELL_DEG)),
            int(math.floor(lon / GRID_CELL_DEG)))


def build_sat_grid(sat_pos):
    """Bucket satellite positions into 10 deg lat/lon cells."""
    grid = {}
    for sid, sp in sat_pos.items():
        key = _grid_key(sp["lat"], sp["lon"])
        grid.setdefault(key, []).append(sid)
    return grid


def grid_candidates(grid, lat, lon, margin=GRID_MARGIN_CELLS):
    """Satellite IDs in cells within `margin` cells of (lat, lon).

    Longitude cells wrap around the antimeridian; latitude cells clamp at
    the poles.
    """
    ci, cj = _grid_key(lat, lon)
    out = []
    for di in range(-margin, margin + 1):
        ii = ci + di
        if ii < -9 or ii > 8:          # latitude range [-90, 90)
            continue
        for dj in range(-margin, margin + 1):
            jj = ((cj + dj + 18) % 36) - 18   # longitude wrap [-180, 180)
            cell = grid.get((ii, jj))
            if cell:
                out.extend(cell)
    return out


def slant_range_km(lat1, lon1, alt1_m, lat2, lon2, alt2_m):
    """3D straight-line distance (km) between two lat/lon/alt points.

    Law of cosines in the plane containing both points and the Earth's
    centre: d = sqrt(r1² + r2² − 2·r1·r2·cos λ), where λ is the central
    angle (ground distance / Re) and rᵢ = Re + altᵢ.
    """
    ground = haversine_km(lat1, lon1, lat2, lon2)
    lam = ground / EARTH_RADIUS_KM  # central angle (rad)
    r1 = EARTH_RADIUS_KM + alt1_m / 1000.0
    r2 = EARTH_RADIUS_KM + alt2_m / 1000.0
    return math.sqrt(r1 * r1 + r2 * r2 - 2.0 * r1 * r2 * math.cos(lam))


def propagation_delay_ms(lat1, lon1, alt1_m, lat2, lon2, alt2_m):
    """Geometric propagation delay (ms) = slant range / speed of light."""
    return slant_range_km(lat1, lon1, alt1_m, lat2, lon2, alt2_m) \
        / SPEED_OF_LIGHT_KM_S * 1000.0


# ---------------------------------------------------------------------------
# Satellite
# ---------------------------------------------------------------------------

class Satellite:
    """A constellation satellite delegating propagation to an ephemeris
    provider (Phase 8 / v4).

    The default provider is CircularProvider — the v1-v3 Keplerian
    circular-orbit model, byte-identical to the old inline math. SGP4 mode
    (real or synthetic TLE) swaps in an SGP4Provider; nothing else in the
    core changes, since every consumer only calls get_position(t).
    """

    def __init__(self, sat_id, altitude_km, inclination_deg, raan_deg,
                 mean_anomaly_deg, shell=0, plane=0, idx=0, provider=None):
        self.id = sat_id
        # Structured constellation coordinates (Phase 7): topology is built
        # from these instead of parsing the ID string. Satellites loaded
        # from a real TLE file carry -1 (unknown structure) and fall back
        # to geometric nearest-neighbour ISL.
        self.shell = shell
        self.plane = plane
        self.idx = idx
        self.altitude_km = altitude_km
        self.inclination_rad = math.radians(inclination_deg)
        self.raan_rad = math.radians(raan_deg)
        self.mean_anomaly_rad = math.radians(mean_anomaly_deg)

        # Phase 8: pluggable propagation backend.
        if provider is None:
            provider = CircularProvider(
                altitude_km, self.inclination_rad, self.raan_rad,
                self.mean_anomaly_rad)
        self.provider = provider

        r = EARTH_RADIUS_KM + altitude_km
        self.period = 2.0 * math.pi * math.sqrt(r ** 3 / EARTH_MU)
        self.angular_velocity = 2.0 * math.pi / self.period

    def get_position(self, t):
        """Return (lat_deg, lon_deg, alt_m) at simulation time t."""
        return self.provider.get_position(t)


def create_constellation(num_orbits=6, sats_per_orbit=12,
                         altitude_km=550.0, inclination_deg=53.0,
                         shells=None):
    """Create satellites from a list of Walker-delta shells.

    shells: optional list of shell specs (dicts) with keys
        planes, sats_per_plane, altitude_km, inclination_deg,
        phase_factor (Walker F, default planes // 2),
        raan_offset (deg, default 0),
        legacy_stagger (bool: reproduce the original v3 geometry).
    When omitted, a single legacy shell is built from num_orbits /
    sats_per_orbit so the default 72-sat constellation is identical to v3.

    IDs: a single shell keeps the backward-compatible "Sat-{plane}-{idx}";
    multiple shells use "Sat-{shell}-{plane}-{idx}".
    """
    if shells is None:
        shells = [{
            "planes": num_orbits,
            "sats_per_plane": sats_per_orbit,
            "altitude_km": altitude_km,
            "inclination_deg": inclination_deg,
            "legacy_stagger": True,
        }]

    multi = len(shells) > 1
    satellites = []
    for sh, spec in enumerate(shells):
        planes = spec["planes"]
        spp = spec["sats_per_plane"]
        alt = spec.get("altitude_km", 550.0)
        inc = spec.get("inclination_deg", 53.0)
        raan0 = spec.get("raan_offset", 0.0)
        phase_factor = spec.get("phase_factor", planes // 2)
        legacy = spec.get("legacy_stagger", False)

        for plane in range(planes):
            if legacy:
                raan = plane * 180.0 / planes       # original v3 layout
            else:
                raan = raan0 + plane * 360.0 / planes
            for idx in range(spp):
                if legacy:
                    ma = idx * 360.0 / spp
                    if plane % 2 == 1:
                        ma += 180.0 / spp
                else:
                    # Walker-delta inter-plane phasing: F * 360 / (P * S)
                    ma = idx * 360.0 / spp + \
                        plane * phase_factor * 360.0 / (planes * spp)
                if multi:
                    sat_id = f"Sat-{sh}-{plane}-{idx}"
                else:
                    sat_id = f"Sat-{plane}-{idx}"
                satellites.append(Satellite(
                    sat_id, alt, inc, raan, ma,
                    shell=sh, plane=plane, idx=idx,
                ))
    return satellites


def satellites_from_tle_records(records, t0_jd=None):
    """Build satellites from parsed (name, Satrec) pairs (shared builder).

    ``t0_jd`` shifts the simulation time origin away from each TLE's own
    epoch so every satellite shares one reference instant.
    """
    satellites = []
    for name, satrec in records:
        sat_id = f"Sat-{name}"
        alt_km = satrec_nominal_altitude_km(satrec)
        inc_deg = math.degrees(satrec.inclo)
        # RAAN / mean anomaly at epoch (deg) — kept for reference only;
        # propagation is fully owned by the SGP4 provider.
        raan_deg = math.degrees(satrec.nodeo)
        ma_deg = math.degrees(satrec.mo)
        satellites.append(Satellite(
            sat_id, alt_km, inc_deg, raan_deg, ma_deg,
            shell=-1, plane=-1, idx=-1,
            provider=SGP4Provider(satrec, t0_jd=t0_jd),
        ))
    return satellites


def create_constellation_from_tle(tle_path, t0_jd=None):
    """Create satellites from a real TLE file (Phase 8 / v4).

    Each satellite is propagated by SGP4 from its own TLE. IDs keep the
    "Sat-" prefix so every prefix-based filter in the core and frontend
    keeps working: "Sat-{TLE name}" (e.g. "Sat-STARLINK-3041").

    shell/plane/idx are set to -1 (unknown structure for a real catalog),
    which makes DemoSimCore fall back to geometric nearest-neighbour ISL
    instead of the structured plane/index adjacency.
    """
    satellites = satellites_from_tle_records(load_tle_file(tle_path),
                                             t0_jd=t0_jd)
    if not satellites:
        raise ValueError(f"No TLE entries parsed from {tle_path}")
    return satellites


# ---------------------------------------------------------------------------
# UAV
# ---------------------------------------------------------------------------

class UAV:
    """
    Parametric flight path (circular or figure-8) over a fixed region.
    Not physically precise — just needs to look plausible.
    """

    def __init__(self, uav_id, center_lat, center_lon, radius_km,
                 base_alt_m, period_s, phase_offset, pattern="circle",
                 speed_kmh=300.0, group="alpha"):
        self.id = uav_id
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius_km = radius_km
        self.base_alt_m = base_alt_m
        self.period_s = period_s
        self.phase_offset = phase_offset
        self.pattern = pattern
        self.speed_kmh = speed_kmh
        self.group = group

    def get_position(self, t):
        """Return (lat_deg, lon_deg, alt_m, heading_deg)."""
        angle = 2.0 * math.pi * t / self.period_s + self.phase_offset

        if self.pattern == "figure8":
            # Lemniscate-like parametric curve
            x = self.radius_km * math.sin(angle)
            y = self.radius_km * math.sin(angle) * math.cos(angle)
            # Heading: derivative direction
            dx = self.radius_km * math.cos(angle)
            dy = self.radius_km * (math.cos(2 * angle))
            heading = math.degrees(math.atan2(dx, dy))
        else:
            # Circular orbit
            x = self.radius_km * math.cos(angle)
            y = self.radius_km * math.sin(angle)
            heading = math.degrees(angle) + 90.0

        # Convert local km offsets to lat/lon (small-angle approx)
        lat = self.center_lat + y / 111.0
        lon = self.center_lon + x / (111.0 * math.cos(math.radians(self.center_lat)))
        lon = normalize_lon(lon)

        # Gentle altitude oscillation
        alt = self.base_alt_m + 500.0 * math.sin(angle * 2)
        heading = heading % 360.0

        return lat, lon, alt, heading


def create_uav_formation(num_uavs=8):
    """Create a UAV formation over the South China Sea."""
    center_lat, center_lon = 18.0, 116.0
    uavs = []
    for i in range(num_uavs):
        phase = i * 2.0 * math.pi / num_uavs
        # Alternate between two concentric rings and patterns
        if i < num_uavs // 2:
            radius = 40.0
            pattern = "circle"
            alt = 8000.0
            group = "alpha"
        else:
            radius = 70.0
            pattern = "figure8"
            alt = 12000.0
            group = "bravo"

        uavs.append(UAV(
            uav_id=f"UAV-{i+1:02d}",
            center_lat=center_lat,
            center_lon=center_lon,
            radius_km=radius,
            base_alt_m=alt,
            period_s=90.0 + i * 10.0,  # slightly different periods
            phase_offset=phase,
            pattern=pattern,
            speed_kmh=250.0 + i * 20.0,
            group=group,
        ))
    return uavs


# ---------------------------------------------------------------------------
# Ship
# ---------------------------------------------------------------------------

SHIP_ROUTES = [
    {
        "name": "Shanghai-Singapore",
        "waypoints": [(31.23, 121.47), (22.30, 114.17), (10.00, 110.00), (1.35, 103.82)],
        "speed_knots": 18,
    },
    {
        "name": "Shenzhen-Colombo",
        "waypoints": [(22.54, 114.06), (15.00, 112.00), (6.93, 79.85)],
        "speed_knots": 20,
    },
    {
        "name": "Tokyo-Sydney",
        "waypoints": [(35.68, 139.65), (25.00, 140.00), (10.00, 145.00), (-10.00, 150.00), (-33.87, 151.21)],
        "speed_knots": 22,
    },
    {
        "name": "Singapore-Rotterdam",
        "waypoints": [(1.35, 103.82), (6.00, 80.00), (12.50, 45.00), (30.00, 32.50), (37.00, 15.00), (51.90, 4.50)],
        "speed_knots": 21,
    },
    {
        "name": "Shanghai-Busan",
        "waypoints": [(31.23, 121.47), (33.00, 124.00), (35.10, 129.04)],
        "speed_knots": 16,
    },
]


class Ship:
    """Moves along a predefined waypoint route at constant speed."""

    def __init__(self, ship_id, route_name, waypoints, speed_knots):
        self.id = ship_id
        self.route_name = route_name
        self.waypoints = waypoints
        self.speed_kmh = speed_knots * 1.852

        # Pre-compute segment lengths (km)
        self.segment_lengths = []
        self.total_length = 0.0
        for i in range(len(waypoints) - 1):
            d = haversine_km(waypoints[i][0], waypoints[i][1],
                             waypoints[i+1][0], waypoints[i+1][1])
            self.segment_lengths.append(d)
            self.total_length += d

    def get_position(self, t):
        """Return (lat_deg, lon_deg, alt_m, heading_deg)."""
        dist = (self.speed_kmh * t / 3600.0) % self.total_length

        # Find which segment we're on
        accumulated = 0.0
        for i, seg_len in enumerate(self.segment_lengths):
            if accumulated + seg_len >= dist:
                # Interpolate within this segment
                frac = (dist - accumulated) / seg_len if seg_len > 0 else 0
                lat = self.waypoints[i][0] + frac * (self.waypoints[i+1][0] - self.waypoints[i][0])
                lon = self.waypoints[i][1] + frac * (self.waypoints[i+1][1] - self.waypoints[i][1])
                # Heading from segment direction
                dlat = self.waypoints[i+1][0] - self.waypoints[i][0]
                dlon = self.waypoints[i+1][1] - self.waypoints[i][1]
                heading = math.degrees(math.atan2(dlon, dlat)) % 360.0
                return lat, normalize_lon(lon), 0.0, heading
            accumulated += seg_len

        # Fallback: last waypoint
        return self.waypoints[-1][0], self.waypoints[-1][1], 0.0, 0.0


def create_ships(num_ships=10):
    """Distribute ships across available routes."""
    ships = []
    for i in range(num_ships):
        route = SHIP_ROUTES[i % len(SHIP_ROUTES)]
        # Offset start time so ships aren't all at the same position
        ships.append(Ship(
            ship_id=f"Ship-{i+1:02d}",
            route_name=route["name"],
            waypoints=route["waypoints"],
            speed_knots=route["speed_knots"] + random.uniform(-2, 2),
        ))
        # Give each ship a time offset by shifting its internal clock
        ships[-1]._time_offset = i * 3600.0  # 1 hour apart
    return ships


# ---------------------------------------------------------------------------
# Ground Stations
# ---------------------------------------------------------------------------

GROUND_STATIONS = {
    "Beijing":     (39.9042, 116.4074, "北京"),
    "Shanghai":    (31.2304, 121.4737, "上海"),
    "Tokyo":       (35.6762, 139.6503, "东京"),
    "Singapore":   (1.3521, 103.8198, "新加坡"),
    "London":      (51.5074, -0.1278, "伦敦"),
    "Paris":       (48.8566, 2.3522, "巴黎"),
    "New York":    (40.7128, -74.0060, "纽约"),
    "Los Angeles": (34.0522, -118.2437, "洛杉矶"),
    "Sydney":      (-33.8688, 151.2093, "悉尼"),
    "Moscow":      (55.7558, 37.6173, "莫斯科"),
    "Delhi":       (28.6139, 77.2090, "德里"),
    "Dubai":       (25.2048, 55.2708, "迪拜"),
    "Sao Paulo":   (-23.5505, -46.6333, "圣保罗"),
    "Cairo":       (30.0444, 31.2357, "开罗"),
    "Cape Town":   (-33.9249, 18.4241, "开普敦"),
}


# ---------------------------------------------------------------------------
# Simulation Core
# ---------------------------------------------------------------------------

class DemoSimCore:
    """Multi-domain simulation core streaming v2 protocol."""

    def __init__(self, host="localhost", port=8000, num_orbits=6,
                 sats_per_orbit=12, num_uavs=8, num_ships=10,
                 scale=None, ephemeris="circular", tle_file=None,
                 epoch=None, constellation=None,
                 ais_file=None, ais_max_ships=20):
        self.host = host
        self.port = port
        self.uri = f"ws://{host}:{port}/ws/core"

        # Phase 8 (v4): propagation backend selection.
        #   ephemeris="circular"          -- v3 Keplerian model (default,
        #                                    byte-identical regression base)
        #   ephemeris="sgp4" (no tle_file) -- synthetic TLE generated from
        #                                    the Walker elements; structured
        #                                    ISL stays valid
        #   tle_file=<path>               -- real TLE catalog; satellites are
        #                                    unstructured -> geometric ISL
        self.ephemeris_mode = "sgp4" if tle_file else ephemeris
        self.tle_source = tle_file if tle_file else (
            "synthetic" if ephemeris == "sgp4" else None)
        self.sim_epoch = epoch if epoch is not None else datetime.now(
            timezone.utc)

        # Real TLE catalogs cannot be hot-swapped at runtime.
        self._tle_catalog = bool(tle_file)

        if tle_file:
            # Real TLE catalog overrides the Walker generator entirely.
            self._shells = None
            self._constellation_name = "tle"
            self.satellites = create_constellation_from_tle(tle_file)
            self.scale = len(self.satellites)
        else:
            # Selectable constellations: --constellation picks a named
            # preset; legacy --scale values map onto the same presets.
            # Without either, fall back to the single-shell legacy
            # generator (identical geometry to v3).
            preset_name = constellation
            if preset_name is None and scale is not None:
                if scale not in SCALE_TO_CONSTELLATION:
                    raise ValueError(
                        f"Unknown scale preset {scale}; "
                        f"choose from {sorted(SCALE_TO_CONSTELLATION)}")
                preset_name = SCALE_TO_CONSTELLATION[scale]

            if preset_name is not None:
                if preset_name not in CONSTELLATION_PRESETS:
                    raise ValueError(
                        f"Unknown constellation preset {preset_name}; "
                        f"choose from {sorted(CONSTELLATION_PRESETS)}")
                self._shells = [dict(s) for s in
                                CONSTELLATION_PRESETS[preset_name]["shells"]]
                self._constellation_name = preset_name
                self.satellites = create_constellation(shells=self._shells)
                self.scale = len(self.satellites)
            else:
                self._shells = None
                self._constellation_name = (
                    "demo72" if (num_orbits, sats_per_orbit) == (6, 12)
                    else "custom")
                self.scale = num_orbits * sats_per_orbit

                # Create entities
                self.satellites = create_constellation(
                    num_orbits, sats_per_orbit, shells=self._shells)
            if ephemeris == "sgp4":
                attach_synthetic_sgp4(self.satellites, self.sim_epoch)

        self.uavs = create_uav_formation(num_uavs)
        self.ships = create_ships(num_ships)
        self.ground_stations = GROUND_STATIONS

        # 真实船舶图层（AIS 回放）：与合成船舶共存，前端可运行时开关。
        # RShip- 前缀使 SSL/DES 逻辑经由前缀匹配零改动复用。
        self.real_ships = []
        self.ais_meta = None
        self.real_ship_enabled = False
        if ais_file:
            try:
                self.real_ships, self.ais_meta = load_ais_tracks(
                    ais_file, max_ships=ais_max_ships)
                self.real_ship_enabled = bool(self.real_ships)
                if not self.real_ships:
                    print(f"  [ais] warning: no usable tracks in {ais_file}")
            except (OSError, ValueError) as exc:
                print(f"  [ais] failed to load {ais_file}: {exc}")

        # ISL topology: structured plane/index adjacency for Walker shells;
        # geometric nearest-neighbour for unstructured (real TLE) catalogs,
        # recomputed periodically as the true geometry drifts.
        self._isl_geometric = any(s.shell < 0 for s in self.satellites)
        if self._isl_geometric:
            init_pos = {}
            for sat in self.satellites:
                lat, lon, alt = sat.get_position(0.0)
                init_pos[sat.id] = {"lat": lat, "lon": lon, "alt": alt}
            self.isl_links = self._compute_geometric_isl(init_pos)
            self.isl_recompute_interval = 60.0
        else:
            self.isl_links = self._compute_isl_topology()
            self.isl_recompute_interval = None
        self._last_isl_recompute = 0.0

        # Dynamic link state (with hysteresis)
        self._active_gsl = set()
        self._active_sul = set()
        self._active_ssl = set()

        # Simulation state
        self.sim_time = 0.0
        self.duration = 600.0
        self.is_playing = True
        self.speed = 1.0
        self.metrics_mode = "bandwidth"

        self.ws = None
        self.running = False

        # Protocol v3 Phase 2: packet-level DES engine + its clock tracker
        self.engine = PacketEngine(
            seed=42, config={"packet_size_bytes": PACKET_SIZE_BYTES})
        self._des_last_t = 0.0
        self.update_interval = 0.2  # 5 Hz state tick (also the DES snapshot dt)

        # Phase 7: dynamic links (GSL/SUL/SSL) and ISL propagation delays are
        # recomputed at 1 Hz instead of every 5 Hz tick. Geometry drifts far
        # slower than the tick rate, so this cuts the per-tick O(N) work by
        # ~5x while handovers remain observable. The cache holds (src, tgt)
        # -> prop_s for every static ISL.
        self.link_update_interval = 1.0
        self._last_link_update = -1e9
        self._isl_prop = {}

        # Phase 7 / protocol 3.1: state frames carry *delta* link sets.
        # _last_link_keys remembers what the previous frame announced so we
        # can emit links_removed; every links_full_every ticks a complete
        # active set is sent so late-joining clients resynchronise.
        self._last_link_keys = set()
        self._tick_count = 0
        self.links_full_every = 25

        # 教学实验沙箱状态：最多 4 个实验并行（每客户端一个），结果经
        # outbox 在主循环里随 5 Hz tick 广播（experiment_update 帧，带 run_id）。
        self._experiment_runs = {}      # run_id -> asyncio.Task
        self._experiment_cancels = {}   # run_id -> bool
        self._experiment_seq = 0
        self._experiment_outbox = []
        # 并发满时排队（S5）：[{run_id, exp_id, params}]，FIFO。
        self._experiment_queue = []

    # ------------------------------------------------------------------
    # ISL topology (static)
    # ------------------------------------------------------------------

    def _compute_isl_topology(self):
        """Pre-compute intra-plane ring and cross-plane ISL pairs.

        Built from the structured (shell, plane, idx) attributes rather than
        ID parsing, so it works for both 2-part and 3-part satellite IDs.
        Cross-plane links join equal indices in adjacent planes of the same
        shell; different shells are not cross-linked.
        """
        links = []
        shells = {}
        for sat in self.satellites:
            shells.setdefault(sat.shell, {}).setdefault(sat.plane, []).append(sat)

        for _sh, planes in shells.items():
            plane_ids = sorted(planes.keys())
            num_planes = len(plane_ids)

            # Intra-plane ring
            for p in plane_ids:
                sats = sorted(planes[p], key=lambda s: s.idx)
                n = len(sats)
                for i in range(n):
                    links.append((sats[i].id, sats[(i + 1) % n].id))

            # Cross-plane (same index in adjacent planes)
            for pi, p in enumerate(plane_ids):
                sats_a = sorted(planes[p], key=lambda s: s.idx)
                p_next = plane_ids[(pi + 1) % num_planes]
                sats_b = sorted(planes[p_next], key=lambda s: s.idx)
                for i in range(min(len(sats_a), len(sats_b))):
                    links.append((sats_a[i].id, sats_b[i].id))

        return links

    def _compute_geometric_isl(self, positions, k=4, max_range_km=6000.0):
        """K-nearest-neighbour ISL for unstructured constellations.

        Real TLE catalogs have no clean (plane, idx) structure, so links
        are formed by spatial proximity instead — the same principle as
        Starlink's dynamic laser crosslinks. Each satellite selects its k
        nearest neighbours within max_range_km (a realistic optical
        crosslink range); the pair set is symmetrised so every selected
        neighbour yields one undirected link.

        Reuses the Phase-7 10-degree spatial grid with a wide margin
        (+/-50 deg of latitude/longitude, comfortably beyond the 6000 km
        range cap at LEO altitude) so the search stays O(N * m) instead of
        O(N^2). Returns the same (a, b) tuple list as the structured
        builder; downstream (propagation cache, engine sync, init frame)
        is topology-source agnostic.
        """
        sat_pos = {sid: p for sid, p in positions.items()
                   if sid.startswith("Sat-")}
        grid = build_sat_grid(sat_pos)

        pairs = set()
        for sid, p in sat_pos.items():
            cands = grid_candidates(grid, p["lat"], p["lon"], margin=5)
            ranked = []
            for cid in cands:
                if cid == sid:
                    continue
                cp = sat_pos[cid]
                d = haversine_km(p["lat"], p["lon"],
                                 cp["lat"], cp["lon"])
                if d <= max_range_km:
                    ranked.append((d, cid))
            ranked.sort()
            for _d, cid in ranked[:k]:
                pairs.add((sid, cid) if sid < cid else (cid, sid))
        return sorted(pairs)

    # ------------------------------------------------------------------
    # Dynamic link computation
    # ------------------------------------------------------------------

    def _topk_uplinks(self, active_set, obs_id, obs_lat, obs_lon,
                      sat_pos, k, min_elev_deg, grid=None):
        """Uplink set for one UAV/ship: its K nearest visible satellites.

        Hysteresis: currently-active links are kept as long as the satellite
        is still above the elevation mask, so links don't flicker when two
        satellites are nearly equidistant. Falls back to the single nearest
        satellite (over the full constellation) to guarantee at least one
        live uplink at all times.

        grid: optional spatial grid (Phase 7) restricting the visibility
        search to nearby satellites.
        """
        candidates = None
        if grid is not None:
            candidates = grid_candidates(grid, obs_lat, obs_lon)
        vis = visible_satellites(obs_lat, obs_lon, sat_pos, min_elev_deg,
                                 candidates=candidates)

        result = set()
        # 1) keep active links that are still visible (hysteresis)
        for sat_id, _d in vis:
            if (sat_id, obs_id) in active_set and len(result) < k:
                result.add((sat_id, obs_id))
        # 2) fill up to K with the nearest visible satellites
        for sat_id, _d in vis:
            if len(result) >= k:
                break
            result.add((sat_id, obs_id))
        # 3) guarantee at least one link even if nothing is above the mask
        if not result and sat_pos:
            nearest = min(
                sat_pos.items(),
                key=lambda kv: haversine_km(obs_lat, obs_lon,
                                            kv[1]["lat"], kv[1]["lon"]))
            result.add((nearest[0], obs_id))
        return result

    def _update_dynamic_links(self, positions):
        """Compute GSL/SUL/SSL with hysteresis (grid-accelerated, Phase 7)."""
        sat_pos = {sid: p for sid, p in positions.items() if sid.startswith("Sat-")}
        gs_pos = {name: (data[0], data[1]) for name, data in self.ground_stations.items()}
        uav_pos = {uid: p for uid, p in positions.items() if uid.startswith("UAV-")}
        ship_pos = {sid: p for sid, p in positions.items()
                    if sid.startswith(("Ship-", "RShip-"))}

        grid = build_sat_grid(sat_pos)

        # GSL: each ground station keeps feeder links to its K nearest
        # visible satellites (hysteresis + nearest-satellite fallback).
        active_gsl_rev = {(sat, gs) for gs, sat in self._active_gsl}
        new_gsl = set()
        for gs_name, (gs_lat, gs_lon) in gs_pos.items():
            for sat_id, _obs in self._topk_uplinks(
                    active_gsl_rev, gs_name, gs_lat, gs_lon,
                    sat_pos, GSL_MAX_LINKS, GSL_MIN_ELEV_DEG, grid=grid):
                new_gsl.add((gs_name, sat_id))
        self._active_gsl = new_gsl

        # SUL: each UAV uplinks to its K nearest visible satellites
        new_sul = set()
        for uav_id, up in uav_pos.items():
            new_sul |= self._topk_uplinks(
                self._active_sul, uav_id, up["lat"], up["lon"],
                sat_pos, SUL_MAX_LINKS, SUL_MIN_ELEV_DEG, grid=grid)
        self._active_sul = new_sul

        # SSL: each ship uplinks to its K nearest visible satellites
        new_ssl = set()
        for ship_id, shp in ship_pos.items():
            new_ssl |= self._topk_uplinks(
                self._active_ssl, ship_id, shp["lat"], shp["lon"],
                sat_pos, SSL_MAX_LINKS, SSL_MIN_ELEV_DEG, grid=grid)
        self._active_ssl = new_ssl

    # ------------------------------------------------------------------
    # Protocol v3 Phase 2: packet-level DES integration
    # ------------------------------------------------------------------

    def _node_position(self, positions, node_id):
        """Resolve any node id (dynamic or ground station) to (lat, lon, alt_m)."""
        if node_id in positions:
            p = positions[node_id]
            return p["lat"], p["lon"], p.get("alt", 0.0)
        if node_id in self.ground_stations:
            lat, lon, _label = self.ground_stations[node_id]
            return lat, lon, 0.0
        return None

    def _edge_prop(self, positions, a, b):
        """Geometric propagation delay (seconds) between two nodes."""
        sp = self._node_position(positions, a)
        tp = self._node_position(positions, b)
        if sp and tp:
            return propagation_delay_ms(sp[0], sp[1], sp[2],
                                        tp[0], tp[1], tp[2]) / 1000.0
        return 0.0

    def _refresh_isl_prop(self, positions):
        """Recompute geometric propagation delays for all static ISLs.

        Called at the 1 Hz link-refresh cadence (Phase 7); between refreshes
        the DES routes with slightly stale ISL weights, which is harmless:
        laser-link delays drift by microseconds per second.
        """
        cache = {}
        for src, tgt in self.isl_links:
            cache[(src, tgt)] = self._edge_prop(positions, src, tgt)
        self._isl_prop = cache

    def _sync_engine_topology(self, positions):
        """Push the current edge set into the DES engine.

        Called at the 1 Hz link-refresh cadence: between refreshes neither
        the active link sets nor the cached ISL propagation delays change,
        so syncing every 5 Hz tick would be wasted work (at 1584 scale the
        edge signature alone hashes ~6500 keys).
        """
        nodes = (list(positions.keys()) + list(self.ground_stations.keys()))

        edges = []
        isl_prop = self._isl_prop
        for src, tgt in self.isl_links:
            edges.append((src, tgt, "isl", isl_prop.get((src, tgt), 0.0)))
        for gs_name, sat_id in self._active_gsl:
            edges.append((gs_name, sat_id, "gsl",
                          self._edge_prop(positions, gs_name, sat_id)))
        for sat_id, uav_id in self._active_sul:
            edges.append((sat_id, uav_id, "sul",
                          self._edge_prop(positions, sat_id, uav_id)))
        for sat_id, ship_id in self._active_ssl:
            edges.append((sat_id, ship_id, "ssl",
                          self._edge_prop(positions, sat_id, ship_id)))

        # Only satellites forward in transit; UAVs / ships / ground stations
        # originate and receive traffic but must not shortcut the mesh.
        transit = (sid for sid in positions if sid.startswith("Sat-"))
        self.engine.sync_topology(nodes, edges, transit=transit)

    def _des_step(self, positions):
        """Drive the packet-level DES and return metrics.

        Declares UAV/ship -> nearest-ground-station Poisson flows, advances
        the engine to sim_time (flushing on a time discontinuity such as
        seek/stop/reset), and returns a fresh snapshot. The engine's edge
        set is maintained separately by _sync_engine_topology (1 Hz).
        """
        # Traffic: each UAV / ship sources packets to its nearest ground station.
        gs_items = list(self.ground_stations.items())
        source_sink = {}
        flow_rate = {}
        flow_prio = {}
        if gs_items:
            for uav in self.uavs:
                up = positions.get(uav.id)
                if not up:
                    continue
                nearest = min(
                    gs_items,
                    key=lambda kv: haversine_km(up["lat"], up["lon"],
                                                kv[1][0], kv[1][1]))
                source_sink[uav.id] = nearest[0]
                flow_rate[uav.id] = UAV_FLOW_RATE_PPS
                flow_prio[uav.id] = PRIO_HIGH        # UAV telemetry/control
            for ship in self.ships:
                shp = positions.get(ship.id)
                if not shp:
                    continue
                nearest = min(
                    gs_items,
                    key=lambda kv: haversine_km(shp["lat"], shp["lon"],
                                                kv[1][0], kv[1][1]))
                source_sink[ship.id] = nearest[0]
                flow_rate[ship.id] = SHIP_FLOW_RATE_PPS
                flow_prio[ship.id] = PRIO_BEST_EFFORT   # ship bulk data
            # 真实船舶（AIS 回放）：图层开启时位置才会出现在 positions 中，
            # 因此用 positions.get 过滤即可；流量参数与合成船舶一致。
            for rship in self.real_ships:
                rsp = positions.get(rship.id)
                if not rsp:
                    continue
                nearest = min(
                    gs_items,
                    key=lambda kv: haversine_km(rsp["lat"], rsp["lon"],
                                                kv[1][0], kv[1][1]))
                source_sink[rship.id] = nearest[0]
                flow_rate[rship.id] = SHIP_FLOW_RATE_PPS
                flow_prio[rship.id] = PRIO_BEST_EFFORT
        self.engine.sync_flows(source_sink, flow_rate, flow_prio)

        # Time discontinuity (seek / stop / reset) => flush transient state.
        dt = self.sim_time - self._des_last_t
        if dt < 0 or dt > MAX_DES_STEP:
            self.engine.flush(self.sim_time)
            dt = 0.0
        self._des_last_t = self.sim_time

        self.engine.advance(self.sim_time)
        return self.engine.snapshot(dt)

    def _link_dict(self, positions, src, tgt, link_type, metrics):
        """Build one slim protocol 3.1 link entry (short keys).

        Keys: t=type, u=bandwidth utilization, l=latency_ms, d=loss rate,
        tx=tx_bps, q=queue depth, p=propagation_ms. Capacity is known per
        type from simulation_init link_types and is not repeated.
        """
        m = metrics["links"].get(frozenset((src, tgt)))
        if m:
            prop_ms = m["propagation_ms"]
            util = m["utilization"]
            latency = m["latency_ms"] if m["latency_ms"] > 0 else prop_ms
            loss = m["loss_rate"]
            tx_bps = m["tx_bps"]
            qd = m["queue_depth"]
        else:
            sp = self._node_position(positions, src)
            tp = self._node_position(positions, tgt)
            prop_ms = (propagation_delay_ms(sp[0], sp[1], sp[2],
                                            tp[0], tp[1], tp[2])
                       if sp and tp else 0.0)
            util = loss = tx_bps = 0.0
            latency = prop_ms
            qd = 0
        return {
            "t": link_type,
            "u": round(util, 4),
            "l": round(latency, 2),
            "d": round(loss, 4),
            "tx": round(tx_bps, 1),
            "q": int(qd),
            "p": round(prop_ms, 2),
        }

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def get_init_message(self):
        """Build v3 simulation_init (v2 fields + packet-level capacities)."""
        nodes = {}

        # Satellites
        multi_shell = self._shells is not None and len(self._shells) > 1
        for sat in self.satellites:
            orbit = {
                "altitude_km": sat.altitude_km,
                "inclination_deg": math.degrees(sat.inclination_rad),
                "plane": sat.plane,
                "index": sat.idx,
            }
            if multi_shell:
                orbit["shell"] = sat.shell
            nodes[sat.id] = {
                "type": "satellite",
                "label": sat.id,
                "orbit": orbit,
            }

        # UAVs
        for uav in self.uavs:
            nodes[uav.id] = {
                "type": "uav",
                "label": f"无人机-{uav.id.split('-')[1]}",
                "group": uav.group,
                "base_alt_m": uav.base_alt_m,
                "speed_kmh": uav.speed_kmh,
            }

        # Ships
        for ship in self.ships:
            nodes[ship.id] = {
                "type": "ship",
                "label": f"货轮-{ship.id.split('-')[1]}",
                "route_name": ship.route_name,
                "speed_knots": round(ship.speed_kmh / 1.852, 1),
            }

        # Real ships (AIS replay layer)
        for rship in self.real_ships:
            nodes[rship.id] = {
                "type": "real_ship",
                "label": rship.name or f"AIS-{rship.mmsi}",
                "mmsi": rship.mmsi,
                "ship_type": rship.ship_type,
                "ais_source": (self.ais_meta or {}).get("source", "ais"),
            }

        # Ground stations
        for name, (lat, lon, label) in self.ground_stations.items():
            nodes[name] = {
                "type": "ground_station",
                "label": label,
                "lat": lat,
                "lon": lon,
            }

        return {
            "message_type": "simulation_init",
            "payload": {
                "version": "3.2",
                "duration": self.duration,
                "update_rate_hz": 5,
                "nodes": nodes,
                # Phase 7: state frames send compact sat_pos arrays aligned
                # to this order, plus delta link sets; the static ISL mesh
                # is announced once here so clients can draw it up front.
                "sat_order": [s.id for s in self.satellites],
                "isl_topology": [list(pair) for pair in self.isl_links],
                # Phase 8 (v3): propagation backend metadata (additive;
                # display altitude stays the constant shell value while the
                # sim internally uses full SGP4 geometry).
                "ephemeris": {
                    "mode": self.ephemeris_mode,
                    "source": self.tle_source,
                    "epoch": self.sim_epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "isl": "geometric" if self._isl_geometric
                           else "structured",
                },
                # 可选星座元数据：前端据此回显星座选择器状态
                "constellation": {
                    "name": self._constellation_name,
                    "label": CONSTELLATION_PRESETS.get(
                        self._constellation_name, {}).get(
                        "label", self._constellation_name),
                    "sat_count": len(self.satellites),
                    "shells": ([dict(s) for s in self._shells]
                               if self._shells else []),
                },
                # 教学实验目录（改进 #2）：前端据此渲染实验卡片
                "experiments": experiment_catalog(),
                # 真实船舶（AIS）图层元数据：未加载轨迹时省略该字段
                **({"ais_layer": {
                    "enabled": self.real_ship_enabled,
                    "source": self.ais_meta.get("source"),
                    "date": self.ais_meta.get("date"),
                    "ship_count": len(self.real_ships),
                }} if self.real_ships else {}),
                "link_types": {
                    "isl": {"label": "星间链路", "color": "#4FC3F7",
                            "capacity_bps": LINK_CAPACITY_BPS["isl"]},
                    "gsl": {"label": "地面-卫星链路", "color": "#FF8A65",
                            "capacity_bps": LINK_CAPACITY_BPS["gsl"]},
                    "sul": {"label": "卫星-无人机链路", "color": "#81C784",
                            "capacity_bps": LINK_CAPACITY_BPS["sul"]},
                    "ssl": {"label": "卫星-船舶链路", "color": "#FFB74D",
                            "capacity_bps": LINK_CAPACITY_BPS["ssl"]},
                },
                "packet_model": {
                    "packet_size_bytes": PACKET_SIZE_BYTES,
                    "queue_capacity_pkts": PACKET_QUEUE_CAPACITY,
                    "traffic": "poisson",
                    "uav_rate_pps": UAV_FLOW_RATE_PPS,
                    "ship_rate_pps": SHIP_FLOW_RATE_PPS,
                    "qos": {"uav": "high(0)", "ship": "best_effort(1)",
                            "scheduling": "strict_priority",
                            "drop_policy": "low_prio_first"},
                    "handover_loss": True,
                    "notes": "阶段3：差异化拓扑更新→真实切换丢包；分域QoS严格优先+低优先先丢",
                },
            },
        }

    def get_state_update(self):
        """Build protocol 3.1 state_update (delta links, compact positions).

        Frame layout (Phase 7, thousand-satellite scale):
          sat_pos       -- [[lat, lon], ...] aligned to init sat_order;
                           satellite altitude is constant (init orbit.altitude_km)
          positions     -- dynamic nodes only (UAV / ship), rounded
          links         -- delta set: non-idle ISLs + all GSL/SUL/SSL,
                           slim short-key entries (see _link_dict)
          links_removed -- link keys dropped since the previous frame
          links_full    -- true on the first frame and every
                           links_full_every ticks (complete active set)
          node_metrics  -- window-active nodes only (filtered by the engine)
        """
        self._tick_count += 1

        # --- Positions (rounded; the same dict feeds internal geometry) ---
        positions = {}
        sat_pos = []
        for sat in self.satellites:
            lat, lon, alt = sat.get_position(self.sim_time)
            rlat, rlon = round(lat, 3), round(lon, 3)
            positions[sat.id] = {"lat": rlat, "lon": rlon, "alt": int(alt)}
            sat_pos.append([rlat, rlon])

        for uav in self.uavs:
            lat, lon, alt, heading = uav.get_position(self.sim_time)
            positions[uav.id] = {
                "lat": round(lat, 3), "lon": round(lon, 3),
                "alt": int(alt), "heading": round(heading, 1),
            }

        for ship in self.ships:
            t_ship = self.sim_time + getattr(ship, '_time_offset', 0.0)
            lat, lon, alt, heading = ship.get_position(t_ship)
            positions[ship.id] = {
                "lat": round(lat, 3), "lon": round(lon, 3),
                "alt": int(alt), "heading": round(heading, 1),
            }

        # 真实船舶（AIS 回放）：图层关闭时不写入位置，SSL/DES 自动剥离
        if self.real_ship_enabled:
            for rship in self.real_ships:
                lat, lon, alt, heading = rship.get_position(self.sim_time)
                positions[rship.id] = {
                    "lat": round(lat, 3), "lon": round(lon, 3),
                    "alt": int(alt), "heading": round(heading, 1),
                }

        # Update dynamic links + ISL propagation cache (Phase 7: at 1 Hz).
        # Always recompute when time runs backwards (seek / stop / reset).
        if (self.sim_time < self._last_link_update or
                self.sim_time - self._last_link_update >=
                self.link_update_interval):
            # Phase 8: geometric ISL (real TLE mode) drifts with the true
            # orbital geometry, so it is recomputed periodically; the diff
            # propagates through packet_sim's existing handover-drop path.
            if (self._isl_geometric and
                    (self.sim_time < self._last_isl_recompute or
                     self.sim_time - self._last_isl_recompute >=
                     self.isl_recompute_interval)):
                self.isl_links = self._compute_geometric_isl(positions)
                self._last_isl_recompute = self.sim_time
            self._update_dynamic_links(positions)
            self._refresh_isl_prop(positions)
            self._sync_engine_topology(positions)
            self._last_link_update = self.sim_time

        # Protocol v3 Phase 2: drive the packet-level DES on the live topology
        metrics = self._des_step(positions)
        link_metrics = metrics["links"]

        # --- Delta link set ---
        links = {}

        # ISL: only links with window activity (present in the snapshot).
        # Idle laser links carry no metrics and are omitted from the frame.
        for src, tgt in self.isl_links:
            if frozenset((src, tgt)) in link_metrics:
                links[f"{src}--{tgt}"] = self._link_dict(
                    positions, src, tgt, "isl", metrics)

        # GSL / SUL / SSL: structural, always announced.
        for gs_name, sat_id in self._active_gsl:
            links[f"{gs_name}--{sat_id}"] = self._link_dict(
                positions, gs_name, sat_id, "gsl", metrics)
        for sat_id, uav_id in self._active_sul:
            links[f"{sat_id}--{uav_id}"] = self._link_dict(
                positions, sat_id, uav_id, "sul", metrics)
        for sat_id, ship_id in self._active_ssl:
            links[f"{sat_id}--{ship_id}"] = self._link_dict(
                positions, sat_id, ship_id, "ssl", metrics)

        cur_keys = set(links.keys())
        links_full = (self._tick_count % self.links_full_every) == 1
        if links_full:
            links_removed = []
        else:
            links_removed = sorted(self._last_link_keys - cur_keys)
        self._last_link_keys = cur_keys

        # Routing highlight (cycle through interesting cross-domain paths)
        routing = {}
        route_phase = int(self.sim_time / 20) % 4
        if route_phase == 0 and self.satellites:
            # Satellite-only path
            routing["highlight_path"] = [s.id for s in self.satellites[:5]]
        elif route_phase == 1 and self._active_gsl and self.satellites:
            # Ground -> Satellite path
            gs_pair = next(iter(self._active_gsl))
            routing["highlight_path"] = [gs_pair[0], gs_pair[1],
                                         self.satellites[1].id, self.satellites[2].id]
        elif route_phase == 2 and self._active_sul:
            # Satellite -> UAV path
            sul_pair = next(iter(self._active_sul))
            routing["highlight_path"] = [sul_pair[0], sul_pair[1]]
        elif route_phase == 3 and self._active_ssl:
            # Satellite -> Ship path
            ssl_pair = next(iter(self._active_ssl))
            routing["highlight_path"] = [ssl_pair[0], ssl_pair[1]]

        # Metrics summary (over the links announced in this frame)
        carrying = [lk for lk in links.values() if lk["tx"] > 0]
        utils = [lk["u"] for lk in carrying]
        latencies = [lk["l"] for lk in links.values()]

        # Per-node packet metrics (window-active only) + DES aggregates
        node_metrics = metrics["nodes"]
        summary = metrics["summary"]

        # Dynamic-node positions only (satellites travel via sat_pos)
        dyn_positions = {nid: p for nid, p in positions.items()
                         if not nid.startswith("Sat-")}

        # Milestone A: live file-transfer tracker (omitted when idle so the
        # steady-state frame stays slim). Reaches the frontend tracker UI and
        # the backend data plane alike.
        file_transfers = (self.engine.file_states()
                          if self.engine.files else None)

        return {
            "message_type": "state_update",
            "payload": {
                "timestamp": round(self.sim_time, 2),
                # 播放状态权威值：前端播放按钮由它驱动，保证多客户端一致
                "is_playing": self.is_playing,
                "sat_pos": sat_pos,
                "positions": dyn_positions,
                "links": links,
                "links_removed": links_removed,
                "links_full": links_full,
                "node_metrics": node_metrics,
                "routing": routing,
                "metrics_summary": {
                    "active_links": len(links),
                    "total_nodes": len(positions) + len(self.ground_stations),
                    "avg_utilization": round(sum(utils) / len(utils), 3) if utils else 0,
                    "max_latency_ms": round(max(latencies), 1) if latencies else 0,
                    # v3 packet-level aggregates (real DES measurements)
                    "pkts_in_flight": summary["pkts_in_flight"],
                    "pkts_delivered": summary["pkts_delivered"],
                    "pkts_dropped": summary["pkts_dropped"],
                    "pkts_handover_dropped": summary["pkts_handover_dropped"],
                    "avg_e2e_latency_ms": round(summary["avg_e2e_latency_ms"], 1),
                    "aggregate_throughput_bps": round(summary["aggregate_throughput_bps"], 1),
                    # v3 Phase 3: per-priority QoS (0=high/UAV, 1=best-effort/ship)
                    "qos": summary["qos"],
                },
                "file_transfers": file_transfers,
            },
        }

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    async def handle_command(self, data):
        payload = data.get("payload", {})
        action = payload.get("action", "")
        params = payload.get("params") or {}

        if action == "play":
            self.is_playing = True
            print(f"  [play] at t={self.sim_time:.1f}s")
        elif action == "pause":
            self.is_playing = False
            print(f"  [pause] at t={self.sim_time:.1f}s")
        elif action == "stop":
            self.is_playing = False
            self.sim_time = 0.0
            print("  [stop] time reset to 0")
        elif action == "reset":
            self.sim_time = 0.0
            print("  [reset] time reset to 0")
        elif action == "speed":
            self.speed = max(0.1, min(10.0, float(params.get("multiplier", 1.0))))
            print(f"  [speed] {self.speed}x")
        elif action == "timeline":
            target = float(params.get("timestamp", 0))
            self.sim_time = max(0, min(self.duration, target))
            print(f"  [timeline] jumped to t={self.sim_time:.1f}s")
        elif action == "metrics":
            self.metrics_mode = params.get("type", "bandwidth")
            print(f"  Metrics mode: {self.metrics_mode}")
        elif action == "filter":
            types = params.get("types", [])
            nodes = params.get("nodes", [])
            print(f"  Filter: types={types}, nodes={len(nodes)}")
        elif action == "focus":
            node_id = params.get("node_id", "")
            print(f"  Focus: {node_id}")
        elif action == "view_preset":
            preset = params.get("preset", "global")
            print(f"  View preset: {preset}")
        elif action == "file_send":
            # Milestone A: the backend enriches params with total_bytes /
            # chunk_size from the stored upload; we model the file as abstract
            # chunks routed src -> dst with timeout-driven ARQ.
            file_id = params.get("file_id")
            if file_id:
                # Keep the engine running long enough for the transfer to
                # finish: a file uploaded after the sim auto-paused at its
                # duration would otherwise never inject a single chunk.
                if self.sim_time >= self.duration - 5.0:
                    self.duration = self.sim_time + 120.0
                if not self.is_playing:
                    self.is_playing = True
                    print("  [file] resumed playback for transfer")
                self.engine.start_file(
                    file_id=file_id,
                    name=params.get("name", file_id),
                    src=params.get("src", ""),
                    dst=params.get("dst", ""),
                    total_bytes=int(params.get("total_bytes", 0)),
                    chunk_size=params.get("chunk_size"),
                    prio=params.get("prio"),
                    rate_cap_bps=params.get("rate_bps"),
                )
                print(f"  [file] send: {file_id} "
                      f"({params.get('src')} -> {params.get('dst')}, "
                      f"{params.get('total_bytes')} B)")
        elif action == "file_cancel":
            file_id = params.get("file_id")
            if file_id:
                self.engine.cancel_file(file_id)
                print(f"  [file] cancelled: {file_id}")
        elif action == "experiment_run":
            # 教学实验（改进 #2）：在独立沙箱引擎中运行，主仿真不受影响。
            exp_id = str(params.get("exp_id", ""))
            run_params = params.get("params") or {}
            # 清理已结束的旧任务记录，再判并发上限。
            self._experiment_runs = {
                rid: t for rid, t in self._experiment_runs.items()
                if not t.done()}
            self._experiment_seq += 1
            run_id = f"{exp_id}-{self._experiment_seq:04d}"
            if len(self._experiment_runs) >= MAX_CONCURRENT_EXPERIMENTS:
                # S5：并发满 → 排队而非拒绝；前面的任务结束后自动开跑。
                self._experiment_queue.append(
                    {"run_id": run_id, "exp_id": exp_id,
                     "params": run_params})
                self._experiment_outbox.append({
                    "message_type": "experiment_update",
                    "payload": {"exp_id": exp_id, "run_id": run_id,
                                "status": "queued",
                                "queue_pos": len(self._experiment_queue)},
                })
                print(f"  [experiment] queued: {run_id} "
                      f"(pos {len(self._experiment_queue)})")
                return
            self._experiment_start(exp_id, run_params, run_id)
            print(f"  [experiment] run: {run_id} params={run_params}")
        elif action == "experiment_quiz":
            # 预习测验判分（改进计划 W2）：答案仅存核心侧，前端只传选项序号。
            exp_id = str(params.get("exp_id", ""))
            try:
                grade = grade_quiz(exp_id, params.get("answers") or {})
                self._experiment_outbox.append({
                    "message_type": "experiment_update",
                    "payload": {"exp_id": exp_id, "status": "quiz",
                                "quiz": grade},
                })
                print(f"  [experiment] quiz graded: {exp_id} "
                      f"{grade['n_correct']}/{grade['n_total']}")
            except ExperimentNotFound:
                self._experiment_outbox.append({
                    "message_type": "experiment_update",
                    "payload": {"exp_id": exp_id, "status": "error",
                                "error": f"unknown experiment: {exp_id}"},
                })
        elif action == "experiment_cancel":
            # 带 run_id 取消指定实验（含仍在排队中的）；不带则取消全部。
            run_id = params.get("run_id")
            if run_id:
                before = len(self._experiment_queue)
                self._experiment_queue = [
                    q for q in self._experiment_queue
                    if q["run_id"] != run_id]
                if len(self._experiment_queue) < before:
                    self._experiment_outbox.append({
                        "message_type": "experiment_update",
                        "payload": {"exp_id": "", "run_id": run_id,
                                    "status": "cancelled",
                                    "note": "已从队列移除"},
                    })
                    print(f"  [experiment] dequeued: {run_id}")
                    return
            targets = ([run_id] if run_id in self._experiment_cancels
                       else list(self._experiment_cancels))
            for rid in targets:
                self._experiment_cancels[rid] = True
            print(f"  [experiment] cancel requested: {targets or 'none'}")
        elif action == "set_ais_layer":
            # 真实船舶图层运行时开关：关闭后下一帧 positions 不再包含
            # RShip-*，SSL 链路与 DES 流量随之移除（links_removed 自动生效）
            if not self.real_ships:
                print("  [ais] ignored: no AIS tracks loaded (--ais-file)")
            else:
                self.real_ship_enabled = bool(params.get("enabled", True))
                if not self.real_ship_enabled:
                    self._active_ssl = {
                        (sat, sh) for sat, sh in self._active_ssl
                        if not sh.startswith("RShip-")}
                print(f"  [ais] layer {'enabled' if self.real_ship_enabled else 'disabled'}"
                      f" ({len(self.real_ships)} real ships)")
        elif action == "set_constellation":
            # 可选星座：运行时热切换 Walker-delta 预设或自定义单壳层，
            # 无需重启核心进程。真实 TLE 编目只能在启动时指定。
            if self._tle_catalog:
                print("  [constellation] ignored: real TLE catalogs are "
                      "fixed at startup (--tle)")
                return
            name = params.get("name")
            custom = params.get("custom")
            try:
                if custom:
                    shells = [validate_custom_shell(custom)]
                    name = "custom"
                else:
                    if name not in CONSTELLATION_PRESETS:
                        print(f"  [constellation] unknown preset: {name}")
                        return
                    shells = [dict(s) for s in
                              CONSTELLATION_PRESETS[name]["shells"]]
            except ValueError as exc:
                print(f"  [constellation] rejected: {exc}")
                return
            self._apply_constellation(shells, name)
            print(f"  [constellation] switched to {name} "
                  f"({self.scale} sats, {len(self.isl_links)} ISLs)")
            # Re-announce the scene so every client rebuilds in place.
            if self.ws is not None:
                await self.ws.send(json.dumps(self.get_init_message()))

    def _experiment_start(self, exp_id, run_params, run_id):
        """启动一个实验任务（并发上限内）。"""
        self._experiment_cancels[run_id] = False
        self._experiment_runs[run_id] = (
            asyncio.get_event_loop().create_task(
                self._experiment_loop(exp_id, run_params, run_id)))

    def _pump_experiment_queue(self):
        """并发空位出现时按 FIFO 启动排队实验（S5）。"""
        self._experiment_runs = {
            rid: t for rid, t in self._experiment_runs.items()
            if not t.done()}
        while (self._experiment_queue
               and len(self._experiment_runs) < MAX_CONCURRENT_EXPERIMENTS):
            item = self._experiment_queue.pop(0)
            self._experiment_start(item["exp_id"], item["params"],
                                   item["run_id"])
            print(f"  [experiment] run (dequeued): {item['run_id']}")

    def _apply_constellation(self, shells, name):
        """Hot-swap the constellation without restarting the process.

        Rebuilds satellites / ISL topology and resets all dynamic-link,
        clock and DES state, mirroring the relevant parts of __init__.
        The caller must re-send simulation_init afterwards so clients
        rebuild the scene.
        """
        self.satellites = create_constellation(shells=shells)
        if self.ephemeris_mode == "sgp4":
            attach_synthetic_sgp4(self.satellites, self.sim_epoch)
        self._shells = shells
        self._constellation_name = name
        self.scale = len(self.satellites)

        # ISL topology (same selection rule as __init__)
        self._isl_geometric = any(s.shell < 0 for s in self.satellites)
        if self._isl_geometric:
            init_pos = {}
            for sat in self.satellites:
                lat, lon, alt = sat.get_position(0.0)
                init_pos[sat.id] = {"lat": lat, "lon": lon, "alt": alt}
            self.isl_links = self._compute_geometric_isl(init_pos)
            self.isl_recompute_interval = 60.0
        else:
            self.isl_links = self._compute_isl_topology()
            self.isl_recompute_interval = None
        self._last_isl_recompute = 0.0

        # Dynamic links, propagation caches and protocol delta state
        self._active_gsl.clear()
        self._active_sul.clear()
        self._active_ssl.clear()
        self._isl_prop = {}
        self._last_link_keys = set()
        self._tick_count = 0
        self._last_link_update = -1e9

        # Simulation clock + DES engine (fresh queues / flows / files)
        self.sim_time = 0.0
        self._des_last_t = 0.0
        self.is_playing = True
        self.engine = PacketEngine(
            seed=42, config={"packet_size_bytes": PACKET_SIZE_BYTES})

    async def _experiment_loop(self, exp_id, run_params=None, run_id=""):
        """运行一个教学实验并把进度/结果推入 outbox（主循环转发）。"""
        def on_progress(update):
            self._experiment_outbox.append({
                "message_type": "experiment_update",
                "payload": {"exp_id": exp_id, "run_id": run_id,
                            "status": "running", **update},
            })

        try:
            result = await run_experiment(
                exp_id, run_params=run_params, on_progress=on_progress,
                cancel_check=lambda: self._experiment_cancels.get(
                    run_id, False))
            self._experiment_outbox.append({
                "message_type": "experiment_update",
                "payload": {"exp_id": exp_id, "run_id": run_id,
                            "status": "done", "result": result},
            })
            print(f"  [experiment] done: {run_id} "
                  f"({'PASS' if result['all_pass'] else 'FAIL'}) "
                  f"score={result.get('score')}")
        except ExperimentCancelled:
            self._experiment_outbox.append({
                "message_type": "experiment_update",
                "payload": {"exp_id": exp_id, "run_id": run_id,
                            "status": "cancelled"},
            })
            print(f"  [experiment] cancelled: {run_id}")
        except ExperimentNotFound as exc:
            self._experiment_outbox.append({
                "message_type": "experiment_update",
                "payload": {"exp_id": exp_id, "run_id": run_id,
                            "status": "error",
                            "error": f"unknown experiment: {exc}"},
            })
            print(f"  [experiment] unknown: {exp_id}")
        except Exception as exc:                    # noqa: BLE001
            self._experiment_outbox.append({
                "message_type": "experiment_update",
                "payload": {"exp_id": exp_id, "run_id": run_id,
                            "status": "error", "error": repr(exc)},
            })
            print(f"  [experiment] error: {exc!r}")
        finally:
            self._experiment_cancels.pop(run_id, None)
            self._pump_experiment_queue()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self):
        num_sats = len(self.satellites)
        num_uavs = len(self.uavs)
        num_ships = len(self.ships)
        num_gs = len(self.ground_stations)

        print(f"\n{'='*55}")
        print("  Demo Simulation Core v2 — Multi-Domain")
        print(f"{'='*55}")
        print(f"  Satellites:      {num_sats}")
        print(f"  UAVs:            {num_uavs}")
        print(f"  Ships:           {num_ships}")
        if self.real_ships:
            src = (self.ais_meta or {}).get("source", "ais")
            print(f"  Real ships(AIS): {len(self.real_ships)} [{src}]")
        print(f"  Ground Stations: {num_gs}")
        print(f"  ISL links:       {len(self.isl_links)}")
        print(f"  Constellation:   {self._constellation_name}")
        print(f"  Ephemeris:       {self.ephemeris_mode}"
              + (f" ({self.tle_source})" if self.tle_source else ""))
        print(f"  Epoch (UTC):     {self.sim_epoch.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        print(f"  Duration:        {self.duration}s")
        print(f"  Protocol:        v3")
        print(f"{'='*55}\n")

        retry_count = 0
        while retry_count < 30:
            try:
                print(f"Connecting to {self.uri}...")
                async with websockets.connect(self.uri, ping_interval=20) as ws:
                    self.ws = ws
                    print("Connected to backend!")

                    # Send init
                    init_msg = self.get_init_message()
                    await ws.send(json.dumps(init_msg))
                    print("Sent simulation_init (v3)")

                    self.running = True
                    retry_count = 0      # healthy session: reset backoff counter
                    dropped = False      # True if the connection died mid-run

                    while self.running:
                        loop_start = time.time()

                        # Receive commands (non-blocking)
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.02)
                            data = json.loads(msg)
                            await self.handle_command(data)
                        except asyncio.TimeoutError:
                            pass
                        except websockets.exceptions.ConnectionClosed:
                            print("\nConnection closed by server - reconnecting...")
                            self.running = False
                            dropped = True
                            break

                        # Advance time
                        if self.is_playing:
                            self.sim_time += self.update_interval * self.speed
                            if self.sim_time >= self.duration:
                                self.sim_time = self.duration
                                self.is_playing = False
                                print("Simulation reached end")

                        # Send state
                        state_msg = self.get_state_update()

                        # Milestone A: forward DES file events (chunk delivered
                        # / complete / cancelled) to the backend data plane,
                        # which reassembles the real bytes from them.
                        file_events = self.engine.drain_file_events()
                        try:
                            if file_events:
                                await ws.send(json.dumps({
                                    "message_type": "file_event",
                                    "events": file_events,
                                }))
                            await ws.send(json.dumps(state_msg))
                            # 教学实验进度/结果帧（如有）
                            while self._experiment_outbox:
                                await ws.send(json.dumps(
                                    self._experiment_outbox.pop(0)))
                        except websockets.exceptions.ConnectionClosed:
                            print("\nSend failed, connection lost - reconnecting...")
                            self.running = False
                            dropped = True
                            break

                        # Maintain rate
                        elapsed = time.time() - loop_start
                        await asyncio.sleep(max(0, self.update_interval - elapsed))

                    if not dropped:
                        break            # clean shutdown: exit the retry loop
                    # else: connection died mid-run -> loop and reconnect.
                    # The sim-time gap (> MAX_DES_STEP) makes _des_step flush
                    # the DES, so counters restart from a consistent state.

            except (ConnectionRefusedError, OSError) as e:
                retry_count += 1
                print(f"Connection failed ({retry_count}/30): {e}")
                print("  Make sure the realtime_backend is running:")
                print("    python -m realtime_backend.run")
                await asyncio.sleep(2)
            except KeyboardInterrupt:
                print("\nStopped by user")
                break
            finally:
                self.running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description="Demo Simulation Core v2 (Multi-Domain)")
    parser.add_argument("--host", default="localhost", help="Backend host")
    parser.add_argument("--port", type=int, default=8000, help="Backend port")
    parser.add_argument("--num-orbits", type=int, default=6,
                        help="Number of orbital planes")
    parser.add_argument("--sats-per-orbit", type=int, default=12,
                        help="Satellites per orbit")
    parser.add_argument("--scale", type=int, default=None,
                        choices=sorted(SCALE_TO_CONSTELLATION.keys()),
                        help="Constellation size preset (overrides "
                             "--num-orbits/--sats-per-orbit; deprecated, "
                             "prefer --constellation)")
    parser.add_argument("--constellation", default=None,
                        choices=sorted(CONSTELLATION_PRESETS.keys()),
                        help="Named constellation preset: demo72 / demo440 "
                             "/ starlink / kuiper / telesat (overrides "
                             "--scale and --num-orbits/--sats-per-orbit)")
    parser.add_argument("--num-uavs", type=int, default=8,
                        help="Number of UAVs")
    parser.add_argument("--num-ships", type=int, default=10,
                        help="Number of ships")
    parser.add_argument("--ephemeris", choices=["circular", "sgp4"],
                        default="circular",
                        help="Propagation model: circular (v3 regression "
                             "baseline) or sgp4 (synthetic TLE generated "
                             "from the Walker elements)")
    parser.add_argument("--tle", default=None, metavar="SRC",
                        help="Real TLE source; implies SGP4 + geometric ISL "
                             "and overrides --scale. Forms: a local file "
                             "path; 'celestrak:<GROUP>' (fetched from the "
                             "Celestrak GP API, cached, offline fallback to "
                             "data/starlink_sample.tle); 'url:<URL>'")
    parser.add_argument("--epoch", default=None, metavar="ISO8601",
                        help="Simulation start UTC, e.g. "
                             "2026-01-01T00:00:00 (default: current time)")
    parser.add_argument("--ais-file", default=None, metavar="PATH",
                        help="真实船舶轨迹 JSON（由 tools/ais_tools.py "
                             "convert 生成），启用 AIS 回放图层")
    parser.add_argument("--ais-max-ships", type=int, default=20,
                        help="AIS 图层最多回放的真实船舶数")
    args = parser.parse_args()

    # celestrak:/url: TLE specs resolve to a local file (cached, with
    # offline fallback). Plain paths pass through unchanged.
    tle_file = args.tle
    if tle_file and (tle_file.startswith("celestrak:")
                     or tle_file.startswith("url:")):
        from tle_source import resolve_tle
        tle_file = resolve_tle(tle_file)

    epoch = None
    if args.epoch:
        try:
            epoch = datetime.fromisoformat(
                args.epoch.replace("Z", "+00:00"))
        except ValueError as exc:
            parser.error(f"invalid --epoch: {exc}")
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=timezone.utc)

    core = DemoSimCore(
        host=args.host,
        port=args.port,
        num_orbits=args.num_orbits,
        sats_per_orbit=args.sats_per_orbit,
        num_uavs=args.num_uavs,
        num_ships=args.num_ships,
        scale=args.scale,
        constellation=args.constellation,
        ephemeris=args.ephemeris,
        tle_file=tle_file,
        epoch=epoch,
        ais_file=args.ais_file,
        ais_max_ships=args.ais_max_ships,
    )
    await core.run()


if __name__ == "__main__":
    asyncio.run(main())
