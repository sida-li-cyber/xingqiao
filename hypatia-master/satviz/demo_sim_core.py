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

try:
    import websockets
except ImportError:
    print("Error: websockets library not installed.")
    print("Install it with: pip install websockets")
    sys.exit(1)

# Protocol v3 Phase 2: packet-level discrete-event simulation engine
from packet_sim import PacketEngine


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0
EARTH_MU = 398600.4418  # km^3/s^2
EARTH_ROTATION_RATE = 360.0 / 86400.0  # deg/s

# GSL: distance thresholds (km) with hysteresis
GSL_CONNECT_RANGE = 2000.0
GSL_DISCONNECT_RANGE = 2200.0

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
PACKET_SIZE_BYTES = 1500       # single packet size
PACKET_QUEUE_CAPACITY = 200    # per-output-port queue depth (packets)

# --- Protocol v3 Phase 2: DES traffic model (Poisson sources) ---
# UAVs and ships generate packets that are routed (hop-by-hop, store-and-
# forward) through the constellation to their nearest ground station. Rates
# are tuned so uplink utilization is visible (~30% SUL / ~18% SSL) while
# staying well below capacity, i.e. an uncongested, healthy network.
UAV_FLOW_RATE_PPS = 2500.0     # packets/sec generated per UAV (~30% of SUL)
SHIP_FLOW_RATE_PPS = 1500.0    # packets/sec generated per ship (~18% of SSL)
MAX_DES_STEP = 2.0             # larger sim-time jump => flush DES state


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


def visible_satellites(obs_lat, obs_lon, sat_pos, min_elev_deg):
    """Return [(sat_id, ground_dist_km)] for satellites above min_elev_deg,
    sorted nearest-first."""
    out = []
    for sat_id, sp in sat_pos.items():
        sat_alt_km = sp.get("alt", 550000.0) / 1000.0
        el = satellite_elevation_deg(obs_lat, obs_lon,
                                     sp["lat"], sp["lon"], sat_alt_km)
        if el >= min_elev_deg:
            out.append((sat_id,
                        haversine_km(obs_lat, obs_lon, sp["lat"], sp["lon"])))
    out.sort(key=lambda x: x[1])
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
    """Circular orbit propagation (unchanged from v1)."""

    def __init__(self, sat_id, altitude_km, inclination_deg, raan_deg,
                 mean_anomaly_deg):
        self.id = sat_id
        self.altitude_km = altitude_km
        self.inclination_rad = math.radians(inclination_deg)
        self.raan_rad = math.radians(raan_deg)
        self.mean_anomaly_rad = math.radians(mean_anomaly_deg)

        r = EARTH_RADIUS_KM + altitude_km
        self.period = 2.0 * math.pi * math.sqrt(r ** 3 / EARTH_MU)
        self.angular_velocity = 2.0 * math.pi / self.period

    def get_position(self, t):
        """Return (lat_deg, lon_deg, alt_m) at simulation time t."""
        M = self.mean_anomaly_rad + self.angular_velocity * t
        theta = M % (2.0 * math.pi)

        lat_rad = math.asin(math.sin(self.inclination_rad) * math.sin(theta))
        lat = math.degrees(lat_rad)

        lon_offset = math.atan2(
            math.cos(self.inclination_rad) * math.sin(theta),
            math.cos(theta)
        )
        lon = math.degrees(self.raan_rad + lon_offset)
        lon -= EARTH_ROTATION_RATE * t
        lon = normalize_lon(lon)

        return lat, lon, self.altitude_km * 1000.0  # meters


def create_constellation(num_orbits=6, sats_per_orbit=12,
                         altitude_km=550.0, inclination_deg=53.0):
    satellites = []
    for orb in range(num_orbits):
        raan = orb * 180.0 / num_orbits
        for idx in range(sats_per_orbit):
            sat_id = f"Sat-{orb}-{idx}"
            ma = idx * 360.0 / sats_per_orbit
            if orb % 2 == 1:
                ma += 180.0 / sats_per_orbit
            satellites.append(Satellite(
                sat_id, altitude_km, inclination_deg, raan, ma
            ))
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
                 sats_per_orbit=12, num_uavs=8, num_ships=10):
        self.host = host
        self.port = port
        self.uri = f"ws://{host}:{port}/ws/core"

        # Create entities
        self.satellites = create_constellation(num_orbits, sats_per_orbit)
        self.uavs = create_uav_formation(num_uavs)
        self.ships = create_ships(num_ships)
        self.ground_stations = GROUND_STATIONS

        # Pre-compute static ISL topology
        self.isl_links = self._compute_isl_topology()

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
        self.engine = PacketEngine(seed=42)
        self._des_last_t = 0.0
        self.update_interval = 0.2  # 5 Hz state tick (also the DES snapshot dt)

    # ------------------------------------------------------------------
    # ISL topology (static)
    # ------------------------------------------------------------------

    def _compute_isl_topology(self):
        """Pre-compute intra-orbit and cross-orbit ISL pairs."""
        links = []
        orbits = {}
        for sat in self.satellites:
            parts = sat.id.split('-')
            orb_id = int(parts[1])
            orbits.setdefault(orb_id, []).append(sat)

        # Intra-orbit (ring)
        for orb_id, sats in orbits.items():
            sats_sorted = sorted(sats, key=lambda s: int(s.id.split('-')[2]))
            n = len(sats_sorted)
            for i in range(n):
                links.append((sats_sorted[i].id, sats_sorted[(i+1) % n].id))

        # Cross-orbit (same index in adjacent planes)
        num_orb = len(orbits)
        for oi in range(num_orb):
            sats_a = sorted(orbits[oi], key=lambda s: int(s.id.split('-')[2]))
            sats_b = sorted(orbits[(oi+1) % num_orb], key=lambda s: int(s.id.split('-')[2]))
            for i in range(min(len(sats_a), len(sats_b))):
                links.append((sats_a[i].id, sats_b[i].id))

        return links

    # ------------------------------------------------------------------
    # Dynamic link computation
    # ------------------------------------------------------------------

    def _topk_uplinks(self, active_set, obs_id, obs_lat, obs_lon,
                      sat_pos, k, min_elev_deg):
        """Uplink set for one UAV/ship: its K nearest visible satellites.

        Hysteresis: currently-active links are kept as long as the satellite
        is still above the elevation mask, so links don't flicker when two
        satellites are nearly equidistant. Falls back to the single nearest
        satellite to guarantee at least one live uplink at all times.
        """
        vis = visible_satellites(obs_lat, obs_lon, sat_pos, min_elev_deg)

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
        """Compute GSL/SUL/SSL with hysteresis."""
        sat_pos = {sid: p for sid, p in positions.items() if sid.startswith("Sat-")}
        gs_pos = {name: (data[0], data[1]) for name, data in self.ground_stations.items()}
        uav_pos = {uid: p for uid, p in positions.items() if uid.startswith("UAV-")}
        ship_pos = {sid: p for sid, p in positions.items() if sid.startswith("Ship-")}

        # GSL: ground station <-> satellite
        new_gsl = set()
        for gs_name, (gs_lat, gs_lon) in gs_pos.items():
            for sat_id, sp in sat_pos.items():
                d = haversine_km(gs_lat, gs_lon, sp["lat"], sp["lon"])
                pair = (gs_name, sat_id)
                if pair in self._active_gsl:
                    if d < GSL_DISCONNECT_RANGE:
                        new_gsl.add(pair)
                else:
                    if d < GSL_CONNECT_RANGE:
                        new_gsl.add(pair)
        self._active_gsl = new_gsl

        # SUL: each UAV uplinks to its K nearest visible satellites
        new_sul = set()
        for uav_id, up in uav_pos.items():
            new_sul |= self._topk_uplinks(
                self._active_sul, uav_id, up["lat"], up["lon"],
                sat_pos, SUL_MAX_LINKS, SUL_MIN_ELEV_DEG)
        self._active_sul = new_sul

        # SSL: each ship uplinks to its K nearest visible satellites
        new_ssl = set()
        for ship_id, shp in ship_pos.items():
            new_ssl |= self._topk_uplinks(
                self._active_ssl, ship_id, shp["lat"], shp["lon"],
                sat_pos, SSL_MAX_LINKS, SSL_MIN_ELEV_DEG)
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

    def _des_step(self, positions):
        """Drive the packet-level DES from the live topology and return metrics.

        Builds the current undirected edge set (ISL + active GSL/SUL/SSL) with
        geometric propagation delays, declares UAV/ship -> nearest-ground-station
        Poisson flows, advances the engine to sim_time (flushing on a time
        discontinuity such as seek/stop/reset), and returns a fresh snapshot.
        """
        nodes = (list(positions.keys()) + list(self.ground_stations.keys()))

        edges = []
        for src, tgt in self.isl_links:
            edges.append((src, tgt, "isl", self._edge_prop(positions, src, tgt)))
        for gs_name, sat_id in self._active_gsl:
            edges.append((gs_name, sat_id, "gsl",
                          self._edge_prop(positions, gs_name, sat_id)))
        for sat_id, uav_id in self._active_sul:
            edges.append((sat_id, uav_id, "sul",
                          self._edge_prop(positions, sat_id, uav_id)))
        for sat_id, ship_id in self._active_ssl:
            edges.append((sat_id, ship_id, "ssl",
                          self._edge_prop(positions, sat_id, ship_id)))

        self.engine.sync_topology(nodes, edges)

        # Traffic: each UAV / ship sources packets to its nearest ground station.
        gs_items = list(self.ground_stations.items())
        source_sink = {}
        flow_rate = {}
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
        self.engine.sync_flows(source_sink, flow_rate)

        # Time discontinuity (seek / stop / reset) => flush transient state.
        dt = self.sim_time - self._des_last_t
        if dt < 0 or dt > MAX_DES_STEP:
            self.engine.flush(self.sim_time)
            dt = 0.0
        self._des_last_t = self.sim_time

        self.engine.advance(self.sim_time)
        return self.engine.snapshot(dt)

    def _link_dict(self, positions, src, tgt, link_type, metrics):
        """Build one link dict using real DES metrics (geometric prop fallback)."""
        m = metrics["links"].get(frozenset((src, tgt)))
        sp = self._node_position(positions, src)
        tp = self._node_position(positions, tgt)
        prop_ms = (propagation_delay_ms(sp[0], sp[1], sp[2],
                                        tp[0], tp[1], tp[2])
                   if sp and tp else 0.0)
        if m:
            util = m["utilization"]
            latency = m["latency_ms"] if m["latency_ms"] > 0 else prop_ms
            loss = m["loss_rate"]
            tx_bps = m["tx_bps"]
            qd = m["queue_depth"]
            qcap = m["queue_capacity"]
            prop_ms = m["propagation_ms"]
        else:
            util = latency = loss = tx_bps = 0.0
            qd = 0
            qcap = PACKET_QUEUE_CAPACITY * 2
        return {
            "type": link_type,
            "source": src,
            "target": tgt,
            "is_active": True,
            "bandwidth_utilization": round(util, 4),
            "latency_ms": round(latency, 2),
            "loss_rate": round(loss, 4),
            "tx_bps": round(tx_bps, 1),
            "capacity_bps": LINK_CAPACITY_BPS[link_type],
            "queue_depth": int(qd),
            "queue_capacity": int(qcap),
            "propagation_ms": round(prop_ms, 2),
        }

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def get_init_message(self):
        """Build v3 simulation_init (v2 fields + packet-level capacities)."""
        nodes = {}

        # Satellites
        for sat in self.satellites:
            parts = sat.id.split('-')
            nodes[sat.id] = {
                "type": "satellite",
                "label": sat.id,
                "orbit": {
                    "altitude_km": sat.altitude_km,
                    "inclination_deg": math.degrees(sat.inclination_rad),
                    "plane": int(parts[1]),
                    "index": int(parts[2]),
                },
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
                "version": "3.0",
                "duration": self.duration,
                "update_rate_hz": 5,
                "nodes": nodes,
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
                    "notes": "阶段2：自研DES存储转发，UAV/船→最近地面站Poisson流",
                },
            },
        }

    def get_state_update(self):
        """Build v3 state_update (v2 fields + packet-level telemetry)."""
        positions = {}

        # Satellite positions
        for sat in self.satellites:
            lat, lon, alt = sat.get_position(self.sim_time)
            positions[sat.id] = {"lat": lat, "lon": lon, "alt": alt}

        # UAV positions
        for uav in self.uavs:
            lat, lon, alt, heading = uav.get_position(self.sim_time)
            positions[uav.id] = {"lat": lat, "lon": lon, "alt": alt, "heading": heading}

        # Ship positions (with time offset)
        for ship in self.ships:
            t_ship = self.sim_time + getattr(ship, '_time_offset', 0.0)
            lat, lon, alt, heading = ship.get_position(t_ship)
            positions[ship.id] = {"lat": lat, "lon": lon, "alt": alt, "heading": heading}

        # Update dynamic links
        self._update_dynamic_links(positions)

        # Protocol v3 Phase 2: drive the packet-level DES on the live topology
        metrics = self._des_step(positions)

        # Build links dict from real DES measurements
        links = {}

        # ISL (always active)
        for src, tgt in self.isl_links:
            links[f"{src}--{tgt}"] = self._link_dict(
                positions, src, tgt, "isl", metrics)

        # GSL
        for gs_name, sat_id in self._active_gsl:
            links[f"{gs_name}--{sat_id}"] = self._link_dict(
                positions, gs_name, sat_id, "gsl", metrics)

        # SUL
        for sat_id, uav_id in self._active_sul:
            links[f"{sat_id}--{uav_id}"] = self._link_dict(
                positions, sat_id, uav_id, "sul", metrics)

        # SSL
        for sat_id, ship_id in self._active_ssl:
            links[f"{sat_id}--{ship_id}"] = self._link_dict(
                positions, sat_id, ship_id, "ssl", metrics)

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

        # Metrics summary
        active_count = sum(1 for lk in links.values() if lk["is_active"])
        carrying = [lk for lk in links.values() if lk.get("tx_bps", 0) > 0]
        utils = [lk["bandwidth_utilization"] for lk in carrying]
        latencies = [lk["latency_ms"] for lk in links.values() if lk["is_active"]]

        # Protocol v3 Phase 2: per-node packet metrics + aggregates from the DES
        node_metrics = metrics["nodes"]
        summary = metrics["summary"]

        return {
            "message_type": "state_update",
            "payload": {
                "timestamp": round(self.sim_time, 2),
                "positions": positions,
                "links": links,
                "node_metrics": node_metrics,
                "routing": routing,
                "metrics_summary": {
                    "active_links": active_count,
                    "total_nodes": len(positions) + len(self.ground_stations),
                    "avg_utilization": round(sum(utils) / len(utils), 3) if utils else 0,
                    "max_latency_ms": round(max(latencies), 1) if latencies else 0,
                    # v3 packet-level aggregates (real DES measurements)
                    "pkts_in_flight": summary["pkts_in_flight"],
                    "pkts_delivered": summary["pkts_delivered"],
                    "pkts_dropped": summary["pkts_dropped"],
                    "avg_e2e_latency_ms": round(summary["avg_e2e_latency_ms"], 1),
                    "aggregate_throughput_bps": round(summary["aggregate_throughput_bps"], 1),
                },
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
            print(f"  ▶ Play at t={self.sim_time:.1f}s")
        elif action == "pause":
            self.is_playing = False
            print(f"  ⏸ Pause at t={self.sim_time:.1f}s")
        elif action == "stop":
            self.is_playing = False
            self.sim_time = 0.0
            print("  ⏹ Stopped, time reset to 0")
        elif action == "reset":
            self.sim_time = 0.0
            print("  ↻ Reset, time reset to 0")
        elif action == "speed":
            self.speed = max(0.1, min(10.0, float(params.get("multiplier", 1.0))))
            print(f"  Speed set to {self.speed}x")
        elif action == "timeline":
            target = float(params.get("timestamp", 0))
            self.sim_time = max(0, min(self.duration, target))
            print(f"  ⏩ Jumped to t={self.sim_time:.1f}s")
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
        print(f"  Ground Stations: {num_gs}")
        print(f"  ISL links:       {len(self.isl_links)}")
        print(f"  Duration:        {self.duration}s")
        print(f"  Protocol:        v2.0")
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
                    print("Sent simulation_init (v2)")

                    self.running = True

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
                            print("\nConnection closed by server")
                            self.running = False
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
                        await ws.send(json.dumps(state_msg))

                        # Maintain rate
                        elapsed = time.time() - loop_start
                        await asyncio.sleep(max(0, self.update_interval - elapsed))

                    break

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
    parser.add_argument("--num-uavs", type=int, default=8,
                        help="Number of UAVs")
    parser.add_argument("--num-ships", type=int, default=10,
                        help="Number of ships")
    args = parser.parse_args()

    core = DemoSimCore(
        host=args.host,
        port=args.port,
        num_orbits=args.num_orbits,
        sats_per_orbit=args.sats_per_orbit,
        num_uavs=args.num_uavs,
        num_ships=args.num_ships,
    )
    await core.run()


if __name__ == "__main__":
    asyncio.run(main())
