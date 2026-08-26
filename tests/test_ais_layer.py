"""真实船舶（AIS）图层测试 — 轨迹回放插值 + 仿真核心离线集成。

覆盖：
  - ais_replay.RealShip 的分段线性插值（含跨经度 ±180°、时间空洞切段、循环回放）
  - load_ais_tracks 的字段校验与损坏条目跳过
  - DemoSimCore 集成：RShip-* 位置、SSL 链路、init 元数据、
    set_ais_layer 运行时开关
"""
import asyncio
import json
import os
import sys
import tempfile

# Engine modules live in <root>/hypatia-master/satviz; tests in <root>/tests.
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "hypatia-master", "satviz")))

from ais_replay import RealShip, load_ais_tracks, normalize_lon


# ---------------------------------------------------------------------------
# 轨迹插值单元测试
# ---------------------------------------------------------------------------

def test_interpolation_midpoint():
    ship = RealShip("RShip-01", 1, "TEST", 70,
                    t=[0.0, 60.0, 120.0],
                    lat=[10.0, 10.02, 10.04],
                    lon=[110.0, 110.02, 110.04],
                    cog=[45.0, 45.0, 45.0])
    lat, lon, alt, heading = ship.get_position(30.0)
    assert abs(lat - 10.01) < 1e-9
    assert abs(lon - 110.01) < 1e-9
    assert alt == 0.0
    assert abs(heading - 45.0) < 1e-9


def test_interpolation_antimeridian():
    """跨经度 ±180° 时应沿较短的一侧插值，而不是绕地球一圈。"""
    ship = RealShip("RShip-02", 2, "TEST", 70,
                    t=[0.0, 60.0],
                    lat=[0.0, 0.0],
                    lon=[179.0, -179.0],   # 实际只移动了 2°
                    cog=[90.0, 90.0])
    lat, lon, _alt, _hdg = ship.get_position(30.0)
    assert abs(abs(lon) - 180.0) < 1e-9    # 中点落在日期变更线上


def test_gap_segmentation():
    """时间空洞超过 max_gap_s 时切段，回放取点数最多的一段。"""
    ship = RealShip("RShip-03", 3, "TEST", 70,
                    t=[0.0, 60.0,             # 段 1：2 点
                       5000.0, 5060.0, 5120.0],  # 段 2：3 点（最长）
                    lat=[0.0, 0.01, 10.0, 10.01, 10.02],
                    lon=[0.0, 0.01, 50.0, 50.01, 50.02],
                    cog=[0.0, 0.0, 10.0, 10.0, 10.0],
                    max_gap_s=3600.0)
    assert ship.n_segments == 2
    assert abs(ship.duration - 120.0) < 1e-9     # 取段 2 的时长
    lat, lon, _a, _h = ship.get_position(0.0)
    assert abs(lat - 10.0) < 1e-9 and abs(lon - 50.0) < 1e-9


def test_loop_replay():
    ship = RealShip("RShip-04", 4, "TEST", 70,
                    t=[0.0, 100.0],
                    lat=[0.0, 1.0],
                    lon=[0.0, 1.0],
                    cog=[0.0, 0.0])
    p1 = ship.get_position(25.0)
    p2 = ship.get_position(125.0)      # 一个周期后回到同一位置
    assert abs(p1[0] - p2[0]) < 1e-9 and abs(p1[1] - p2[1]) < 1e-9


def test_normalize_lon():
    assert abs(normalize_lon(181.0) - (-179.0)) < 1e-9
    assert abs(normalize_lon(-181.0) - 179.0) < 1e-9


# ---------------------------------------------------------------------------
# 轨迹 JSON 加载
# ---------------------------------------------------------------------------

def _write_tracks(path, ships):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "marine_cadastre", "date": "2023-06-15",
                   "max_gap_s": 3600.0, "ships": ships}, f)


def test_load_skips_corrupt_entries():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "tracks.json")
        _write_tracks(path, [
            {"id": "RShip-01", "mmsi": 1, "name": "GOOD", "ship_type": 70,
             "t": [0.0, 60.0], "lat": [18.0, 18.05], "lon": [116.0, 116.05],
             "cog": [45.0, 45.0]},
            {"id": "RShip-02", "t": [0.0], "lat": [0.0], "lon": [0.0]},  # 太短
            {"id": "RShip-03", "t": [0.0, 60.0], "lat": [1.0]},           # 不齐
        ])
        ships, meta = load_ais_tracks(path)
        assert len(ships) == 1 and ships[0].id == "RShip-01"
        assert meta["source"] == "marine_cadastre"
        assert meta["ship_count"] == 1


# ---------------------------------------------------------------------------
# 仿真核心离线集成
# ---------------------------------------------------------------------------

def _make_track_file(td):
    """一艘在南海（靠近 UAV 编队）的真实船，600s 轨迹。"""
    path = os.path.join(td, "tracks.json")
    t = [float(i * 60) for i in range(11)]
    _write_tracks(path, [{
        "id": "RShip-01", "mmsi": 367000001, "name": "PACIFIC TESTER",
        "ship_type": 70,
        "t": t,
        "lat": [18.0 + 0.01 * i for i in range(11)],
        "lon": [116.0 + 0.01 * i for i in range(11)],
        "sog": [12.0] * 11,
        "cog": [45.0] * 11,
    }])
    return path


def test_core_integration_ais_layer():
    from demo_sim_core import DemoSimCore

    with tempfile.TemporaryDirectory() as td:
        core = DemoSimCore(ais_file=_make_track_file(td))
        assert len(core.real_ships) == 1
        assert core.real_ship_enabled

        # init 帧包含 real_ship 节点与 ais_layer 元数据
        init = core.get_init_message()["payload"]
        node = init["nodes"].get("RShip-01")
        assert node and node["type"] == "real_ship"
        assert node["mmsi"] == 367000001
        assert init["ais_layer"]["ship_count"] == 1

        # 步进 3 秒（跨过 1 Hz 链路刷新节拍），产生真实包级状态
        core.is_playing = True
        last = None
        for _ in range(15):
            core.sim_time += core.update_interval * core.speed
            last = core.get_state_update()

        assert "RShip-01" in last["payload"]["positions"]
        ssl_ships = {sh for _sat, sh in core._active_ssl}
        assert "RShip-01" in ssl_ships          # SSL 链路已建立

        # 运行时关闭图层
        asyncio.run(core.handle_command({
            "message_type": "command",
            "payload": {"action": "set_ais_layer",
                        "params": {"enabled": False}},
        }))
        assert not core.real_ship_enabled

        core.sim_time += core.update_interval
        last = core.get_state_update()
        assert "RShip-01" not in last["payload"]["positions"]
        assert not any(sh.startswith("RShip-")
                       for _sat, sh in core._active_ssl)

        # 重新开启后位置恢复
        asyncio.run(core.handle_command({
            "message_type": "command",
            "payload": {"action": "set_ais_layer",
                        "params": {"enabled": True}},
        }))
        core.sim_time += core.update_interval
        last = core.get_state_update()
        assert "RShip-01" in last["payload"]["positions"]


def test_core_without_ais_file_unaffected():
    """未提供 --ais-file 时行为不变：无 real_ship 节点、无 ais_layer 字段。"""
    from demo_sim_core import DemoSimCore

    core = DemoSimCore()
    assert not core.real_ships and not core.real_ship_enabled
    payload = core.get_init_message()["payload"]
    assert "ais_layer" not in payload
    assert not any(n["type"] == "real_ship" for n in payload["nodes"].values())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All AIS layer tests passed.")
