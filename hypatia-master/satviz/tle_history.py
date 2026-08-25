"""Historical TLE archive selection for the prediction layer (N4).

The prediction layer's historical reconciliation previously ran against
the synthetic Walker geometry, which dominated the ~80% median error.
This module selects, for any target instant inside the archive window,
the real TLE record of every satellite whose epoch is closest to the
target (bounded by ``max_age_days`` so SGP4 extrapolation stays in its
valid regime) and builds satellites sharing the scenario start time as
their common time origin.

The parsed archive is cached by (path, mtime); the per-satellite epoch
selection is cheap and runs per prediction.
"""

from __future__ import annotations

import os
import threading
from datetime import timedelta

from ephemeris import load_tle_file

# Repository layout: <repo>/hypatia-master/satviz/tle_history.py
REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", ".."))

# Historical Starlink archive aligned with the Starlink-on-the-Road
# measurement window (2024-01 ~ 2024-03), produced by
# tools/fetch_historical_tle.py (N2).
HIST_TLE_ARCHIVE = os.path.join(REPO_ROOT, "data", "tle_archive",
                                "starlink_2024-01_2024-03.tle")

_DAY_S = 86400.0

# (path, mtime) -> list of (name, satrec, epoch_jd); parse once, reuse.
_RECORD_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _archive_records(tle_path):
    """Parsed (name, satrec, epoch_jd) list, cached per file+mtime."""
    try:
        mtime = os.path.getmtime(tle_path)
    except OSError:
        return None
    key = (tle_path, mtime)
    with _CACHE_LOCK:
        if key in _RECORD_CACHE:
            return _RECORD_CACHE[key]
    records = [(name, rec, rec.jdsatepoch + rec.jdsatepochF)
               for name, rec in load_tle_file(tle_path)]
    with _CACHE_LOCK:
        _RECORD_CACHE[key] = records
    return records


def datetime_to_jd(dt):
    """Julian date of an aware datetime (UTC)."""
    epoch_s = dt.timestamp()
    # JD of the POSIX epoch (1970-01-01 00:00 UTC).
    return 2440587.5 + epoch_s / _DAY_S


def select_records_for_epoch(tle_path, target_dt, max_age_days=7.0):
    """Nearest-epoch TLE record per satellite at ``target_dt``.

    Returns ``(records, meta)`` where records is a list of
    ``(name, satrec)`` pairs and meta carries selection statistics, or
    ``(None, meta)`` when the archive is missing or no record satisfies
    the age bound (the caller degrades to synthetic geometry).
    """
    meta = {"tle_path": tle_path, "target": target_dt.isoformat(),
            "max_age_days": float(max_age_days)}
    records = _archive_records(tle_path)
    if not records:
        meta["error"] = "archive missing or empty"
        return None, meta

    target_jd = datetime_to_jd(target_dt)
    max_age_jd = float(max_age_days)

    # Keep the epoch closest to the target for each satellite name.
    best = {}
    for name, rec, epoch_jd in records:
        age = abs(epoch_jd - target_jd)
        cur = best.get(name)
        if cur is None or age < cur[0]:
            best[name] = (age, rec)

    selected, ages_d = [], []
    for name, (age_jd, rec) in best.items():
        if age_jd > max_age_jd:
            continue
        selected.append((name, rec))
        ages_d.append(age_jd)
    selected.sort(key=lambda pair: pair[0])

    if not selected:
        meta["error"] = "no TLE record within the age bound"
        return None, meta

    ages_d.sort()
    meta.update({
        "n_catalog": len(best),
        "n_selected": len(selected),
        "epoch_age_d_median": round(ages_d[len(ages_d) // 2], 3),
        "epoch_age_d_max": round(ages_d[-1], 3),
    })
    return selected, meta


def build_constellation_for_epoch(tle_path, target_dt, max_age_days=7.0):
    """Satellites for ``target_dt`` sharing it as their time origin.

    Returns ``(satellites, meta)``; satellites is None on degradation.
    ``get_position(t)`` on the returned satellites offsets from
    ``target_dt`` (t = 0 at the scenario start), matching the scenario
    core's sim_epoch.
    """
    # Lazy import: demo_sim_core imports twin_predict, which is imported
    # by demo_sim_core itself; keep resolution at call time.
    import demo_sim_core as dsc

    records, meta = select_records_for_epoch(tle_path, target_dt,
                                             max_age_days=max_age_days)
    if records is None:
        return None, meta
    t0_jd = datetime_to_jd(target_dt)
    return dsc.satellites_from_tle_records(records, t0_jd=t0_jd), meta


def archive_coverage(tle_path=HIST_TLE_ARCHIVE):
    """(first_epoch_dt, last_epoch_dt) of the archive, or (None, None)."""
    from datetime import datetime, timezone
    records = _archive_records(tle_path)
    if not records:
        return None, None
    epochs = [r[2] for r in records]
    jd0 = 2440587.5
    first = datetime.fromtimestamp((min(epochs) - jd0) * _DAY_S,
                                   tz=timezone.utc)
    last = datetime.fromtimestamp((max(epochs) - jd0) * _DAY_S,
                                  tz=timezone.utc)
    return first, last


def covers(tle_path, target_dt, max_age_days=7.0):
    """True when ``target_dt`` falls inside the archive's usable window."""
    first, last = archive_coverage(tle_path)
    if first is None:
        return False
    pad = timedelta(days=float(max_age_days))
    return first - pad <= target_dt <= last + pad


def clear_cache():
    """Drop the parsed-record cache (tests / file rotation)."""
    with _CACHE_LOCK:
        _RECORD_CACHE.clear()
