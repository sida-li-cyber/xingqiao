"""
Demo Simulation Core for testing the realtime visualization frontend.

Simulates a simplified Starlink-like LEO constellation and streams
state updates to the realtime_backend via WebSocket.

Usage:
    # Start the realtime backend first:
    python -m realtime_backend.run

    # Then in another terminal:
    python demo_sim_core.py

    # Options:
    python demo_sim_core.py --constellation Starlink --shell 0
    python demo_sim_core.py --constellation Kuiper --shell 1
"""

import asyncio
import argparse
import json
import math
import time
import sys
from datetime import datetime, timezone

try:
    import websockets
except ImportError:
    print("Error: websockets library not installed.")
    print("Install it with: pip install websockets")
    sys.exit(1)


# Earth constants
EARTH_RADIUS_KM = 6371.0
EARTH_MU = 398600.4418  # km^3/s^2
EARTH_ROTATION_RATE = 360.0 / 86400.0  # deg/s


# Simulation scenario profiles: (base_loss_rate, jitter_min, jitter_max, label)
SCENARIOS = {
    "ideal":       (0.001, 0.0005, 0.005,  "Ideal Clear Sky"),
    "commercial":  (0.01,  0.005,  0.04,   "Commercial Service"),
    "weather":     (0.02,  0.01,   0.06,   "Moderate Weather"),
    "handover":    (0.03,  0.005,  0.10,   "Frequent Handover"),
    "extreme":     (0.05,  0.01,   0.15,   "Extreme Conditions"),
}


def deterministic_noise(sim_time: float, link_id: str) -> float:
    """Deterministic pseudo-random in [-1, 1] based on sim_time + link_id.

    Uses multiple sine harmonics so different links produce different
    values, but the result is frozen when sim_time stops advancing.
    """
    h = hash(link_id) % 10007
    # Three harmonics with incommensurate frequencies → no visible
    # repeating pattern within the simulation window (600 s).
    v = (math.sin(sim_time * 0.713 + h * 0.017) * 0.50 +
         math.sin(sim_time * 1.301 + h * 0.023) * 0.30 +
         math.sin(sim_time * 2.117 + h * 0.031) * 0.20)
    return v  # already in [-1, 1]


class Satellite:
    """A simplified satellite with circular orbit propagation."""

    def __init__(self, sat_id, altitude_km, inclination_deg, raan_deg,
                 mean_anomaly_deg, epoch=0.0):
        self.sat_id = sat_id
        self.altitude_km = altitude_km
        self.inclination_rad = math.radians(inclination_deg)
        self.raan_rad = math.radians(raan_deg)
        self.mean_anomaly_rad = math.radians(mean_anomaly_deg)

        # Orbital period (seconds)
        r = EARTH_RADIUS_KM + altitude_km
        self.period = 2.0 * math.pi * math.sqrt(r ** 3 / EARTH_MU)
        self.angular_velocity = 2.0 * math.pi / self.period

    def get_position(self, time_seconds):
        """Return (lat_deg, lon_deg, alt_km) at the given time."""
        # Mean anomaly at time t
        M = self.mean_anomaly_rad + self.angular_velocity * time_seconds

        # Approximate true anomaly = mean anomaly (circular orbit)
        theta = M % (2.0 * math.pi)

        # Latitude from spherical trigonometry
        lat_rad = math.asin(math.sin(self.inclination_rad) * math.sin(theta))
        lat_deg = math.degrees(lat_rad)

        # Longitude
        lon_offset_rad = math.atan2(
            math.cos(self.inclination_rad) * math.sin(theta),
            math.cos(theta)
        )
        lon_deg = math.degrees(self.raan_rad + lon_offset_rad)

        # Account for Earth's rotation
        lon_deg -= EARTH_ROTATION_RATE * time_seconds

        # Normalize to [-180, 180]
        lon_deg = ((lon_deg + 180.0) % 360.0) - 180.0

        return lat_deg, lon_deg, self.altitude_km


# Constellation definitions: (orbits, sats_per_orbit, inclination, altitude_km)
# Full-scale parameters from FCC/ITU filings.
_CONSTELLATION_FULL = {
    "Starlink": [
        {"orbits": 72, "sats_per_orbit": 22, "inclination": 53.0,  "altitude_km": 550.0},
        {"orbits": 32, "sats_per_orbit": 50, "inclination": 53.8,  "altitude_km": 1110.0},
        {"orbits": 8,  "sats_per_orbit": 50, "inclination": 74.0,  "altitude_km": 1130.0},
        {"orbits": 5,  "sats_per_orbit": 75, "inclination": 81.0,  "altitude_km": 1275.0},
        {"orbits": 6,  "sats_per_orbit": 75, "inclination": 70.0,  "altitude_km": 1325.0},
    ],
    "Kuiper": [
        {"orbits": 34, "sats_per_orbit": 34, "inclination": 51.9,  "altitude_km": 630.0},
        {"orbits": 36, "sats_per_orbit": 36, "inclination": 42.0,  "altitude_km": 610.0},
        {"orbits": 28, "sats_per_orbit": 28, "inclination": 33.0,  "altitude_km": 590.0},
    ],
    "Telesat": [
        {"orbits": 27, "sats_per_orbit": 13, "inclination": 98.98, "altitude_km": 1015.0},
        {"orbits": 40, "sats_per_orbit": 33, "inclination": 50.88, "altitude_km": 1325.0},
    ],
}


def scale_shell_params(orbits, sats_per_orbit, target=100.0):
    """Scale down a shell to ~target total satellites."""
    total = orbits * sats_per_orbit
    scale = max(1, int(round(math.sqrt(total / target))))
    return max(1, orbits // scale), max(1, sats_per_orbit // scale)


def create_constellation(name, shell_index, target=100.0):
    """Create a (scaled-down) constellation shell.

    Args:
        name: One of "Starlink", "Kuiper", "Telesat".
        shell_index: 0-based shell index within the constellation.
        target: Target satellite count per shell after scaling.
    """
    shells = _CONSTELLATION_FULL[name]
    if shell_index < 0 or shell_index >= len(shells):
        raise ValueError(f"Shell {shell_index} out of range for {name} (0-{len(shells)-1})")

    cfg = shells[shell_index]
    orig_orbits = cfg["orbits"]
    orig_sats = cfg["sats_per_orbit"]
    scaled_orbits, scaled_sats = scale_shell_params(orig_orbits, orig_sats, target)

    satellites = []
    for orb in range(scaled_orbits):
        raan = orb * 180.0 / scaled_orbits
        for sat in range(scaled_sats):
            sat_id = f"Sat-{orb}-{sat}"
            mean_anomaly = sat * 360.0 / scaled_sats
            if orb % 2 == 1:
                mean_anomaly += 180.0 / scaled_sats
            satellites.append(Satellite(
                sat_id=sat_id,
                altitude_km=cfg["altitude_km"],
                inclination_deg=cfg["inclination"],
                raan_deg=raan,
                mean_anomaly_deg=mean_anomaly,
            ))
    return satellites


def create_ground_stations():
    """Create a set of major cities as ground stations."""
    return {
        "Beijing": (39.9042, 116.4074),
        "Shanghai": (31.2304, 121.4737),
        "Tokyo": (35.6762, 139.6503),
        "Singapore": (1.3521, 103.8198),
        "London": (51.5074, -0.1278),
        "Paris": (48.8566, 2.3522),
        "New York": (40.7128, -74.0060),
        "Los Angeles": (34.0522, -118.2437),
        "Sydney": (-33.8688, 151.2093),
        "Moscow": (55.7558, 37.6173),
        "Delhi": (28.6139, 77.2090),
        "Dubai": (25.2048, 55.2708),
        "Sao Paulo": (-23.5505, -46.6333),
        "Cairo": (30.0444, 31.2357),
        "Cape Town": (-33.9249, 18.4241),
    }


def compute_links(satellites, ground_stations, max_gsl_range_km=2000.0,
                  max_isl_range_km=5000.0):
    """
    Compute ISLs (inter-satellite links) and GSLs (ground-satellite links)
    based on current positions.
    """
    links = {}
    # ISLs: connect satellites within the same orbit (orbit links)
    # Group by orbit
    orbits = {}
    for sat in satellites:
        parts = sat.sat_id.split('-')
        orb_id = parts[1]
        if orb_id not in orbits:
            orbits[orb_id] = []
        orbits[orb_id].append(sat)

    for orb_id, sats in orbits.items():
        sats_sorted = sorted(sats, key=lambda s: int(s.sat_id.split('-')[2]))
        n = len(sats_sorted)
        for i in range(n):
            s1 = sats_sorted[i]
            s2 = sats_sorted[(i + 1) % n]
            link_id = f"{s1.sat_id}-{s2.sat_id}"
            links[link_id] = {"type": "isl", "source": s1.sat_id, "target": s2.sat_id}

    # Cross-orbit links: connect to neighboring orbit's nearest satellite
    # Simplified: connect same-index satellites in adjacent orbits
    for oi in range(len(orbits)):
        orb_a = str(oi)
        orb_b = str((oi + 1) % len(orbits))
        if orb_a in orbits and orb_b in orbits:
            sats_a = sorted(orbits[orb_a], key=lambda s: int(s.sat_id.split('-')[2]))
            sats_b = sorted(orbits[orb_b], key=lambda s: int(s.sat_id.split('-')[2]))
            for i in range(min(len(sats_a), len(sats_b))):
                link_id = f"{sats_a[i].sat_id}-{sats_b[i].sat_id}"
                if link_id not in links:
                    links[link_id] = {"type": "isl", "source": sats_a[i].sat_id,
                                      "target": sats_b[i].sat_id}

    return links


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """Compute distance between two lat/lon points in km."""
    R = EARTH_RADIUS_KM
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class DemoSimCore:
    """Simulation core that streams state updates to the backend."""

    def __init__(self, host="localhost", port=8000,
                 constellation_name="Starlink", shell_index=0):
        self.host = host
        self.port = port
        self.uri = f"ws://{host}:{port}/ws/core"

        self.constellation_name = constellation_name
        self.current_shell = shell_index
        self.satellites = create_constellation(constellation_name, shell_index)
        self.ground_stations = create_ground_stations()
        self.links = compute_links(self.satellites, self.ground_stations)

        self.sim_time = 0.0
        self.duration = 600.0  # 10 minutes of simulation
        self.is_playing = True
        self.speed = 1.0
        self.metrics_mode = "none"
        self.current_scenario = "commercial"

        self.ws = None
        self.running = False

    def get_state_update(self):
        """Build a complete state_update message."""
        # Satellite positions
        sat_positions = {}
        for sat in self.satellites:
            lat, lon, alt = sat.get_position(self.sim_time)
            sat_positions[sat.sat_id] = {
                "lat": lat, "lon": lon, "alt": alt * 1000.0  # km to meters
            }

        # Link status — driven by current scenario + deterministic jitter
        link_status = {}
        base_loss, jitter_min, jitter_max, _label = SCENARIOS[self.current_scenario]
        for link_id, link_info in self.links.items():
            # Deterministic utilization based on sim_time
            utilization = 0.3 + 0.3 * math.sin(self.sim_time * 0.1 + hash(link_id) % 100)
            utilization = max(0.0, min(1.0, utilization))

            # Loss rate = scenario base + deterministic jitter scaled to range
            noise = deterministic_noise(self.sim_time, link_id)
            jitter_span = jitter_max - jitter_min
            jitter = jitter_min + (noise * 0.5 + 0.5) * jitter_span
            loss_rate = round(base_loss + jitter, 4)
            loss_rate = max(0.0, min(1.0, loss_rate))

            link_status[link_id] = {
                "is_active": True,
                "source": link_info["source"],
                "target": link_info["target"],
                "bandwidth_utilization": round(utilization, 3),
                "latency": round(10.0 + 40.0 * utilization, 1),
                "loss_base": round(base_loss, 4),
                "loss_jitter": round(jitter, 4),
                "loss_rate": loss_rate,
            }

        # Ground stations
        gs_data = {}
        for name, (lat, lon) in self.ground_stations.items():
            gs_data[name] = {"lat": lat, "lon": lon, "alt": 0.0, "name": name}

        # Routing (highlight a random path every 30 seconds)
        routing = {}
        route_interval = int(self.sim_time / 30)
        if route_interval % 2 == 0 and len(self.satellites) >= 4:
            sat_ids = [s.sat_id for s in self.satellites[:4]]
            routing["highlight_path"] = sat_ids

        return {
            "message_type": "state_update",
            "payload": {
                "satellite_positions": sat_positions,
                "ground_stations": gs_data,
                "link_status": link_status,
                "routing": routing,
                "bandwidth_utilization": {
                    lid: ls["bandwidth_utilization"]
                    for lid, ls in link_status.items()
                },
                "timestamp": self.sim_time,
            },
        }

    def get_init_message(self):
        """Build the simulation_init message."""
        sat_list = [s.sat_id for s in self.satellites]
        gs_dict = {}
        for name, (lat, lon) in self.ground_stations.items():
            gs_dict[name] = {"lat": lat, "lon": lon, "alt": 0.0, "name": name}

        return {
            "message_type": "simulation_init",
            "payload": {
                "satellites": sat_list,
                "ground_stations": gs_dict,
                "duration": self.duration,
                "scenario": self.current_scenario,
                "constellation": {
                    "name": self.constellation_name,
                    "shell_count": len(_CONSTELLATION_FULL[self.constellation_name]),
                    "current_shell": self.current_shell,
                },
                "total_satellites": len(sat_list),
                "total_links": len(self.links),
            },
        }

    async def handle_command(self, data):
        """Process incoming commands from the backend."""
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
            self.speed = float(params.get("multiplier", 1.0))
            print(f"  Speed set to {self.speed}x")
        elif action == "timeline":
            target = float(params.get("timestamp", 0))
            self.sim_time = max(0, min(self.duration, target))
            print(f"  ⏩ Jumped to t={self.sim_time:.1f}s")
        elif action == "metrics":
            self.metrics_mode = params.get("type", "none")
            print(f"  Metrics mode: {self.metrics_mode}")
        elif action == "filter":
            sats = params.get("satellites", [])
            stas = params.get("stations", [])
            print(f"  Filter: {len(sats)} satellites, {len(stas)} stations")
        elif action == "scenario":
            scenario = params.get("scenario", "commercial")
            if scenario in SCENARIOS:
                self.current_scenario = scenario
                _label = SCENARIOS[scenario][3]
                print(f"  Scenario: {_label}")
            else:
                print(f"  Unknown scenario: {scenario}")
        elif action == "switch_constellation":
            name = params.get("constellation", self.constellation_name)
            shell = int(params.get("shell", 0))
            if name not in _CONSTELLATION_FULL:
                print(f"  Unknown constellation: {name}")
                return
            max_shell = len(_CONSTELLATION_FULL[name]) - 1
            shell = max(0, min(shell, max_shell))
            print(f"  Switching to {name} shell {shell}...")
            self.constellation_name = name
            self.current_shell = shell
            self.satellites = create_constellation(name, shell)
            self.links = compute_links(self.satellites, self.ground_stations)
            self.sim_time = 0.0
            # Send new init immediately so frontend rebuilds the scene
            if self.ws:
                init_msg = self.get_init_message()
                await self.ws.send(json.dumps(init_msg))
            print(f"  Constellation: {len(self.satellites)} satellites, "
                  f"{len(self.links)} links")

    async def run(self):
        """Main simulation loop."""
        print(f"\n{'='*50}")
        print("  Demo Simulation Core")
        print(f"{'='*50}")
        print(f"  Constellation: {self.constellation_name} shell {self.current_shell}")
        print(f"  Satellites: {len(self.satellites)}")
        print(f"  Ground Stations: {len(self.ground_stations)}")
        print(f"  Pre-computed Links: {len(self.links)}")
        print(f"  Duration: {self.duration}s")
        print(f"{'='*50}\n")

        retry_count = 0
        while retry_count < 30:
            try:
                print(f"Connecting to {self.uri}...")
                async with websockets.connect(self.uri, ping_interval=20) as ws:
                    self.ws = ws
                    print("Connected to backend!")

                    # Send initialization message
                    init_msg = self.get_init_message()
                    await ws.send(json.dumps(init_msg))
                    print("Sent simulation_init")

                    self.running = True
                    last_time = time.time()
                    update_interval = 0.1  # 10 Hz updates

                    while self.running:
                        loop_start = time.time()

                        # Receive and process commands (non-blocking)
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

                        # Advance simulation time
                        if self.is_playing:
                            self.sim_time += update_interval * self.speed
                            if self.sim_time >= self.duration:
                                self.sim_time = 0.0
                                print("Simulation looped back to 0")

                        # Send state update
                        state_msg = self.get_state_update()
                        await ws.send(json.dumps(state_msg))

                        # Sleep to maintain update rate
                        elapsed = time.time() - loop_start
                        await asyncio.sleep(max(0, update_interval - elapsed))

                    break  # Exit retry loop on clean exit

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


async def main():
    parser = argparse.ArgumentParser(description="Demo Simulation Core")
    parser.add_argument("--host", default="localhost", help="Backend host")
    parser.add_argument("--port", type=int, default=8000, help="Backend port")
    parser.add_argument("--constellation", default="Starlink",
                        choices=["Starlink", "Kuiper", "Telesat"],
                        help="Constellation name")
    parser.add_argument("--shell", type=int, default=0,
                        help="Shell index (0-based)")
    args = parser.parse_args()

    if args.constellation not in _CONSTELLATION_FULL:
        print(f"Unknown constellation: {args.constellation}")
        print(f"Available: {list(_CONSTELLATION_FULL.keys())}")
        sys.exit(1)

    core = DemoSimCore(
        host=args.host,
        port=args.port,
        constellation_name=args.constellation,
        shell_index=args.shell,
    )
    await core.run()


if __name__ == "__main__":
    asyncio.run(main())
