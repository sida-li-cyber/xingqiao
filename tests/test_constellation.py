"""
Selectable-constellation tests — named presets, hot-swap command and
custom single-shell validation.

  T1: preset table sanity (counts / orbits / labels)
  T2: --scale backward compatibility (geometry identical to presets)
  T3: set_constellation hot-swap (preset + custom + invalid rejection)
  T4: simulation_init carries the constellation metadata
  T5: validate_custom_shell boundary checks

Run:  python -m pytest tests/test_constellation.py
"""

import asyncio
import os
import sys

# Engine modules live in <root>/hypatia-master/satviz; tests in <root>/tests.
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

import pytest

from demo_sim_core import (
    CONSTELLATION_PRESETS,
    DemoSimCore,
    SCALE_TO_CONSTELLATION,
    validate_custom_shell,
)


def run_cmd(core, params):
    """Feed one set_constellation command through the async dispatcher."""
    asyncio.run(core.handle_command(
        {"payload": {"action": "set_constellation", "params": params}}))


# ----------------------------------------------------------------------
# T1: preset table sanity
# ----------------------------------------------------------------------

def test_preset_table_sanity():
    expected = {
        "demo72": 72,
        "demo440": 440,
        "starlink": 1584,
        "kuiper": 34 * 34,
        "telesat": 27 * 13,
    }
    for name, count in expected.items():
        preset = CONSTELLATION_PRESETS[name]
        shells = preset["shells"]
        total = sum(s["planes"] * s["sats_per_plane"] for s in shells)
        assert total == count, f"{name}: expected {count}, got {total}"
        assert preset["label"], f"{name}: missing display label"
        core = DemoSimCore(constellation=name)
        assert len(core.satellites) == count
        assert core.scale == count
        assert core._constellation_name == name
        # Single-shell presets keep the backward-compatible 2-part IDs.
        assert all(len(s.id.split("-")) == 3 for s in core.satellites)


def test_real_constellation_orbital_elements():
    # FCC filing values mirrored from the Hypatia paper scripts.
    kuiper = CONSTELLATION_PRESETS["kuiper"]["shells"][0]
    assert kuiper["altitude_km"] == pytest.approx(630.0)
    assert kuiper["inclination_deg"] == pytest.approx(51.9)

    telesat = CONSTELLATION_PRESETS["telesat"]["shells"][0]
    assert telesat["altitude_km"] == pytest.approx(1015.0)
    assert telesat["inclination_deg"] == pytest.approx(98.98)

    core = DemoSimCore(constellation="kuiper")
    assert all(s.altitude_km == pytest.approx(630.0)
               for s in core.satellites)


# ----------------------------------------------------------------------
# T2: --scale backward compatibility
# ----------------------------------------------------------------------

def test_scale_maps_to_presets():
    for scale, name in SCALE_TO_CONSTELLATION.items():
        via_scale = DemoSimCore(scale=scale)
        via_name = DemoSimCore(constellation=name)
        assert via_scale._constellation_name == name
        assert len(via_scale.satellites) == len(via_name.satellites)
        # Identical geometry: same IDs and positions at arbitrary times.
        for a, b in zip(via_scale.satellites, via_name.satellites):
            assert a.id == b.id
            assert a.get_position(0.0) == b.get_position(0.0)
            assert a.get_position(123.45) == b.get_position(123.45)


def test_unknown_scale_and_preset_rejected():
    with pytest.raises(ValueError):
        DemoSimCore(scale=999)
    with pytest.raises(ValueError):
        DemoSimCore(constellation="nonexistent")


# ----------------------------------------------------------------------
# T3: set_constellation hot-swap
# ----------------------------------------------------------------------

def test_hotswap_preset():
    core = DemoSimCore()
    assert len(core.satellites) == 72
    old_engine = core.engine
    core.sim_time = 42.0

    run_cmd(core, {"name": "kuiper"})

    assert core._constellation_name == "kuiper"
    assert core.scale == 1156
    assert len(core.satellites) == 1156
    # ISL topology rebuilt: structured shells yield exactly 2N links.
    assert len(core.isl_links) == 2 * 1156
    assert all(s.altitude_km == pytest.approx(630.0)
               for s in core.satellites)
    # Clock / DES state reset.
    assert core.sim_time == 0.0
    assert core._des_last_t == 0.0
    assert core.engine is not old_engine
    # Dynamic-link caches cleared.
    assert not core._active_gsl
    assert not core._active_sul
    assert not core._active_ssl
    assert not core._isl_prop
    assert not core._last_link_keys


def test_hotswap_custom():
    core = DemoSimCore()
    run_cmd(core, {"custom": {"planes": 3, "sats_per_plane": 4,
                              "altitude_km": 800, "inclination_deg": 45}})
    assert core._constellation_name == "custom"
    assert core.scale == 12
    assert len(core.isl_links) == 2 * 12
    assert all(s.altitude_km == pytest.approx(800.0)
               for s in core.satellites)


def test_hotswap_rejects_invalid_params():
    core = DemoSimCore()

    # Unknown preset: state untouched.
    run_cmd(core, {"name": "galileo"})
    assert core.scale == 72

    # Out-of-range custom values: state untouched.
    for bad in ({"planes": 3, "sats_per_plane": 4,
                 "altitude_km": 5000, "inclination_deg": 45},
                {"planes": 300, "sats_per_plane": 4,
                 "altitude_km": 550, "inclination_deg": 45},
                {"planes": 80, "sats_per_plane": 40,
                 "altitude_km": 550, "inclination_deg": 45},   # > 1600 sats
                {"planes": 3, "inclination_deg": 45}):          # missing key
        run_cmd(core, {"custom": bad})
        assert core.scale == 72
        assert core._constellation_name == "demo72"


def test_hotswap_ignored_for_tle_catalog():
    core = DemoSimCore()
    core._tle_catalog = True   # simulate a real-TLE session
    run_cmd(core, {"name": "kuiper"})
    assert core.scale == 72
    assert core._constellation_name == "demo72"


# ----------------------------------------------------------------------
# T4: simulation_init metadata
# ----------------------------------------------------------------------

def test_init_message_carries_constellation():
    core = DemoSimCore(constellation="telesat")
    payload = core.get_init_message()["payload"]
    const = payload["constellation"]
    assert const["name"] == "telesat"
    assert const["label"] == CONSTELLATION_PRESETS["telesat"]["label"]
    assert const["sat_count"] == 351
    assert const["shells"] == CONSTELLATION_PRESETS["telesat"]["shells"]


# ----------------------------------------------------------------------
# T5: validate_custom_shell boundaries
# ----------------------------------------------------------------------

def test_validate_custom_shell():
    shell = validate_custom_shell({"planes": "12", "sats_per_plane": "12",
                                   "altitude_km": "550",
                                   "inclination_deg": "53"})
    assert shell == {"planes": 12, "sats_per_plane": 12,
                     "altitude_km": 550.0, "inclination_deg": 53.0}

    for bad in ({},
                {"planes": 0, "sats_per_plane": 12,
                 "altitude_km": 550, "inclination_deg": 53},
                {"planes": 12, "sats_per_plane": 41,
                 "altitude_km": 550, "inclination_deg": 53},
                {"planes": 12, "sats_per_plane": 12,
                 "altitude_km": 299, "inclination_deg": 53},
                {"planes": 12, "sats_per_plane": 12,
                 "altitude_km": 550, "inclination_deg": 130},
                {"planes": "abc", "sats_per_plane": 12,
                 "altitude_km": 550, "inclination_deg": 53}):
        with pytest.raises(ValueError):
            validate_custom_shell(bad)
