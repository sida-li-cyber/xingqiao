"""
Ephemeris providers (Phase 8 / v4 — orbital realism).

Two interchangeable propagation backends behind one interface:

  CircularProvider  -- the v1-v3 Keplerian circular-orbit model, preserved
                       verbatim so `--ephemeris circular` stays byte-identical
                       to the v3 regression baseline.
  SGP4Provider      -- near-Earth SGP4 propagation driven by TLE lines
                       (via the pure-Python `sgp4` package), returning WGS84
                       geodetic coordinates.

Both expose `get_position(t) -> (lat_deg, lon_deg, alt_m)` where *t* is
simulation seconds since the provider epoch (for SGP4: the TLE epoch).

Also included:
  - ECI -> ECEF (GMST rotation) -> WGS84 geodetic conversion;
  - a synthetic TLE writer (Walker-delta elements -> standard two-line
    format with checksums) so the full SGP4 pipeline can run offline and
    deterministically on the structured constellation;
  - a real TLE file loader.

Design note (decision 2026-07-30): the display protocol keeps a constant
shell altitude (sat_pos carries lat/lon only); SGP4's varying altitude is
used internally for all geometry (elevation masks, propagation delays) but
never transmitted, so the frontend and protocol stay untouched.
"""

import math
from datetime import datetime, timezone

from sgp4.api import Satrec, WGS72


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Legacy spherical model (used by CircularProvider and the existing
# haversine / elevation helpers in demo_sim_core — unchanged from v3).
EARTH_RADIUS_KM = 6371.0
EARTH_MU = 398600.4418          # km^3/s^2
EARTH_ROTATION_RATE = 360.0 / 86400.0   # deg/s

# WGS84 ellipsoid (used only by the SGP4 ECI -> geodetic conversion).
WGS84_A = 6378.137              # semi-major axis, km
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)    # first eccentricity squared


# ---------------------------------------------------------------------------
# Time conversion
# ---------------------------------------------------------------------------

def datetime_to_jd(dt):
    """UTC datetime -> Julian date (float)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    y, m = dt.year, dt.month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd = (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) +
          dt.day + b - 1524.5)
    frac = (dt.hour + dt.minute / 60.0 +
            (dt.second + dt.microsecond * 1e-6) / 3600.0) / 24.0
    return jd + frac


def jd_to_datetime(jd):
    """Julian date -> UTC datetime (inverse of datetime_to_jd)."""
    jd = jd + 0.5
    z = math.floor(jd)
    f = jd - z
    if z < 2299161:
        a = z
    else:
        alpha = math.floor((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = math.floor((b - 122.1) / 365.25)
    d = math.floor(365.25 * c)
    e = math.floor((b - d) / 30.6001)
    day = b - d - math.floor(30.6001 * e)
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    hours = f * 24.0
    hh = int(hours)
    mm = int((hours - hh) * 60)
    ss = (hours - hh - mm / 60.0) * 3600.0
    return datetime(year, month, day, hh, mm, 0,
                    int(round(ss * 1e6)) % 1000000, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# ECI -> geodetic
# ---------------------------------------------------------------------------

def gmst_rad(jd):
    """Greenwich Mean Sidereal Time (radians) for a Julian date.

    Vallado (4th ed.) eq. 3-47; accuracy ~0.1 arcsec over a few years,
    far beyond what the visualization / link geometry needs.
    """
    d = jd - 2451545.0
    theta = (280.46061837 + 360.98564736629 * d) % 360.0
    return math.radians(theta)


def eci_to_geodetic(r_eci_km, jd):
    """ECI position (km) at Julian date -> (lat_deg, lon_deg, alt_km).

    Rotates into ECEF with GMST (z-axis only; polar motion / nutation are
    sub-meter effects at LEO and ignored), then iterates the WGS84
    ellipsoid normal to converge geodetic latitude and height.
    """
    th = gmst_rad(jd)
    c, s = math.cos(th), math.sin(th)
    x = c * r_eci_km[0] + s * r_eci_km[1]
    y = -s * r_eci_km[0] + c * r_eci_km[1]
    z = r_eci_km[2]

    lon = math.atan2(y, x)
    p = math.hypot(x, y)

    if p < 1e-9:                       # exactly on the polar axis
        lat = math.copysign(math.pi / 2, z)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2)
        alt = abs(z) - WGS84_A * math.sqrt(1.0 - WGS84_E2)
        return math.degrees(lat), math.degrees(lon), alt

    lat = math.atan2(z, p * (1.0 - WGS84_E2))
    n = WGS84_A
    h = 0.0
    for _ in range(6):
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat_new = math.atan2(z, p * (1.0 - WGS84_E2 * n / (n + h)))
        if abs(lat_new - lat) < 1e-12:
            lat = lat_new
            break
        lat = lat_new

    # Height near the poles is better conditioned via z.
    if abs(lat) > math.radians(80.0):
        h = z / math.sin(lat) - n * (1.0 - WGS84_E2)

    return math.degrees(lat), math.degrees(lon), h


def geodetic_to_ecef(lat_deg, lon_deg, alt_km):
    """WGS84 geodetic (lat, lon, alt) -> ECEF (km). Inverse of
    eci_to_geodetic's ellipsoid math, without the GMST rotation."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * math.sin(lat) ** 2)
    x = (n + alt_km) * math.cos(lat) * math.cos(lon)
    y = (n + alt_km) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + alt_km) * math.sin(lat)
    return x, y, z


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class CircularProvider:
    """Keplerian circular orbit — the v1-v3 propagation model, verbatim."""

    def __init__(self, altitude_km, inclination_rad, raan_rad,
                 mean_anomaly_rad):
        self.altitude_km = altitude_km
        self.inclination_rad = inclination_rad
        self.raan_rad = raan_rad
        self.mean_anomaly_rad = mean_anomaly_rad
        r = EARTH_RADIUS_KM + altitude_km
        self.period = 2.0 * math.pi * math.sqrt(r ** 3 / EARTH_MU)
        self.angular_velocity = 2.0 * math.pi / self.period

    def get_position(self, t):
        """Return (lat_deg, lon_deg, alt_m) at simulation time t."""
        m = self.mean_anomaly_rad + self.angular_velocity * t
        theta = m % (2.0 * math.pi)

        lat_rad = math.asin(math.sin(self.inclination_rad) *
                            math.sin(theta))
        lat = math.degrees(lat_rad)

        lon_offset = math.atan2(
            math.cos(self.inclination_rad) * math.sin(theta),
            math.cos(theta)
        )
        lon = math.degrees(self.raan_rad + lon_offset)
        lon -= EARTH_ROTATION_RATE * t
        lon = ((lon + 180.0) % 360.0) - 180.0

        return lat, lon, self.altitude_km * 1000.0


class SGP4Provider:
    """SGP4 propagation from a TLE (via the `sgp4` package).

    Simulation time t = 0 corresponds to ``t0_jd`` when given, else to
    the TLE epoch (the historical behaviour); positions are returned in
    WGS84 geodetic coordinates. Also exposes the ECI state (position +
    velocity) for downstream link-budget work (Doppler). A custom t0 is
    used by the historical prediction path so every satellite shares the
    scenario start time as its time origin (N4).
    """

    def __init__(self, satrec, t0_jd=None):
        self.satrec = satrec
        self.epoch_jd = satrec.jdsatepoch + satrec.jdsatepochF
        self.t0_jd = self.epoch_jd if t0_jd is None else t0_jd

    def get_state_eci(self, t):
        """(r_eci_km, v_eci_km_s) at simulation time t."""
        jd = self.t0_jd + t / 86400.0
        jd_i = math.floor(jd - 0.5) + 0.5
        fr = jd - jd_i
        e, r, v = self.satrec.sgp4(jd_i, fr)
        if e != 0:
            raise RuntimeError(f"SGP4 propagation error code {e}")
        return r, v

    def get_position(self, t):
        """Return (lat_deg, lon_deg, alt_m) at simulation time t."""
        jd = self.t0_jd + t / 86400.0
        r, _v = self.get_state_eci(t)
        lat, lon, alt_km = eci_to_geodetic(r, jd)
        return lat, lon, alt_km * 1000.0


# ---------------------------------------------------------------------------
# Synthetic TLE generation
# ---------------------------------------------------------------------------

def _tle_checksum(line):
    """Modulo-10 TLE checksum: digits count as their value, '-' as 1."""
    total = 0
    for ch in line:
        if ch.isdigit():
            total += int(ch)
        elif ch == '-':
            total += 1
    return total % 10


def _tle_piece(i):
    """International-designator piece letters: A, B, ... Z, AA, AB, ..."""
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def mean_motion_rev_day(altitude_km, earth_radius_km=WGS84_A):
    """Two-body mean motion (rev/day) for a circular orbit at altitude."""
    a = earth_radius_km + altitude_km
    n_rad_s = math.sqrt(EARTH_MU / (a ** 3))
    return n_rad_s * 86400.0 / (2.0 * math.pi)


def make_tle_lines(sat_num, inc_deg, raan_deg, mean_anom_deg, altitude_km,
                   epoch_dt, intl_year=26, intl_launch=1, piece_idx=0):
    """Build the two TLE lines for one synthetic circular-orbit satellite.

    Eccentricity and B* are zero; argument of perigee is 0 by convention
    for circular orbits. The line format follows the standard two-line
    element set specification (Vallado, table 8-4) including checksums.
    """
    if epoch_dt.tzinfo is None:
        epoch_dt = epoch_dt.replace(tzinfo=timezone.utc)
    yy = epoch_dt.year % 100
    doy = (epoch_dt.timetuple().tm_yday +
           (epoch_dt.hour * 3600 + epoch_dt.minute * 60 +
            epoch_dt.second + epoch_dt.microsecond * 1e-6) / 86400.0)

    n = mean_motion_rev_day(altitude_km)
    piece = _tle_piece(piece_idx)

    l1 = ("1 {num:05d}U {iy:02d}{il:03d}{pc:<3s} "
          "{yy:02d}{doy:012.8f}  .00000000  00000-0  00000-0 0 {elen:04d}")
    l1 = l1.format(num=sat_num, iy=intl_year, il=intl_launch, pc=piece,
                   yy=yy, doy=doy, elen=999)
    l1 += str(_tle_checksum(l1))

    l2 = ("2 {num:05d} {inc:8.4f} {raan:8.4f} {ecc:7s} {argp:8.4f} "
          "{ma:8.4f} {n:11.8f}{rev:05d}")
    l2 = l2.format(num=sat_num, inc=inc_deg, raan=raan_deg % 360.0,
                   ecc="0000000", argp=0.0, ma=mean_anom_deg % 360.0,
                   n=n, rev=0)
    l2 += str(_tle_checksum(l2))

    return l1, l2


def synthetic_satrec(sat_num, inc_deg, raan_deg, mean_anom_deg, altitude_km,
                     epoch_dt, **tle_kw):
    """Create an sgp4 Satrec from Walker-delta elements via synthetic TLE."""
    l1, l2 = make_tle_lines(sat_num, inc_deg, raan_deg, mean_anom_deg,
                            altitude_km, epoch_dt, **tle_kw)
    return Satrec.twoline2rv(l1, l2, WGS72)


def attach_synthetic_sgp4(satellites, epoch_dt, sat_num_start=80001):
    """Replace each Satellite's provider with SGP4 driven by a synthetic
    TLE generated from its own Walker-delta elements.

    The structured (shell, plane, idx) attributes are untouched, so the
    O(N) structured ISL topology remains valid. Deterministic for a fixed
    epoch — the offline regression baseline for Phase 8.
    """
    for i, sat in enumerate(satellites):
        satrec = synthetic_satrec(
            sat_num_start + i,
            math.degrees(sat.inclination_rad),
            math.degrees(sat.raan_rad),
            math.degrees(sat.mean_anomaly_rad),
            sat.altitude_km,
            epoch_dt,
            piece_idx=i,
        )
        sat.provider = SGP4Provider(satrec)


# ---------------------------------------------------------------------------
# Real TLE loading
# ---------------------------------------------------------------------------

def load_tle_file(path):
    """Parse a TLE text file -> list of (name, Satrec).

    Accepts both the three-line form (name line + two element lines) and
    the bare two-line form (names default to the NORAD catalog number).
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh if ln.strip()]

    out = []
    i = 0
    pending_name = None
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("1 ") and i + 1 < len(lines) \
                and lines[i + 1].startswith("2 "):
            satrec = Satrec.twoline2rv(ln, lines[i + 1], WGS72)
            if pending_name is not None:
                name = pending_name
            else:
                name = str(satrec.satnum)
            out.append((name, satrec))
            pending_name = None
            i += 2
        else:
            pending_name = ln.strip()
            i += 1
    return out


def satrec_nominal_altitude_km(satrec):
    """Nominal circular altitude (km) implied by a Satrec's mean motion."""
    # Mean motion (rad/min) -> semi-major axis via two-body inversion.
    n_rad_s = satrec.no_kozai / 60.0
    a = (EARTH_MU / (n_rad_s ** 2)) ** (1.0 / 3.0)
    return a - EARTH_RADIUS_KM
