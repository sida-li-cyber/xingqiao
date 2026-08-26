"""AIS 真实船舶回放模块 — 读取 tools/ais_tools.py 生成的轨迹 JSON，
在仿真时间内按分段线性插值回放真实船舶位置。

节点 ID 统一使用 `RShip-` 前缀，与合成船舶（`Ship-`）区分；
demo_sim_core 用 startswith(("Ship-", "RShip-")) 收集船舶位置，
SSL 链路与 DES 流量因此对真实船舶零改动复用。
"""

import json


def normalize_lon(lon):
    """经度归一化到 [-180, 180)。"""
    while lon >= 180.0:
        lon -= 360.0
    while lon < -180.0:
        lon += 360.0
    return lon


class RealShip:
    """沿真实 AIS 轨迹回放的一艘船。

    轨迹可能存在时间空洞（AIS 报文丢失），空洞超过 max_gap_s 的位置
    被切成多段；回放选取点数最多的一段做循环插值，保证位置连续。
    """

    def __init__(self, ship_id, mmsi, name, ship_type, t, lat, lon, cog,
                 max_gap_s=3600.0):
        self.id = ship_id
        self.mmsi = mmsi
        self.name = name
        self.ship_type = ship_type

        # --- 按时间空洞切段 ---
        seg_bounds = []       # [(start_idx, end_idx)] 闭区间
        start = 0
        for i in range(1, len(t)):
            if t[i] - t[i - 1] > max_gap_s:
                seg_bounds.append((start, i - 1))
                start = i
        seg_bounds.append((start, len(t) - 1))

        # 取点数最多的一段（并列取更早的）
        best = max(seg_bounds, key=lambda b: b[1] - b[0])
        a, b = best
        self._t = [x - t[a] for x in t[a:b + 1]]      # 段内相对秒
        self._lat = lat[a:b + 1]
        self._lon = lon[a:b + 1]
        self._cog = cog[a:b + 1]
        self.duration = self._t[-1] if self._t[-1] > 0 else 1.0
        self.n_segments = len(seg_bounds)

    def get_position(self, t):
        """返回 (lat_deg, lon_deg, alt_m, heading_deg)；按段时长循环。"""
        tt = t % self.duration
        ts, lats, lons, cogs = self._t, self._lat, self._lon, self._cog

        # 定位区间（段内点数有限，线性扫描即可）
        i = 0
        while i < len(ts) - 2 and ts[i + 1] <= tt:
            i += 1

        span = ts[i + 1] - ts[i]
        frac = (tt - ts[i]) / span if span > 0 else 0.0

        lat = lats[i] + frac * (lats[i + 1] - lats[i])

        # 经度插值：跨 ±180° 时沿较短的一侧走
        dlon = lons[i + 1] - lons[i]
        if dlon > 180.0:
            dlon -= 360.0
        elif dlon < -180.0:
            dlon += 360.0
        lon = normalize_lon(lons[i] + frac * dlon)

        # 航向取前向端点值（分段常量），避免圆周均值失真
        heading = cogs[i + 1] % 360.0
        return lat, lon, 0.0, heading


def load_ais_tracks(path, max_ships=None, max_gap_s=None):
    """读取轨迹 JSON，返回 (RealShip 列表, 元数据 dict)。

    元数据含 source / date / ship_count，供 simulation_init 的
    ais_layer 字段回显。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    gap = max_gap_s if max_gap_s is not None else data.get(
        "max_gap_s", 3600.0)
    ships = []
    for entry in data.get("ships", []):
        try:
            t = entry["t"]
            lat = entry["lat"]
            lon = entry["lon"]
            cog = entry.get("cog", [0.0] * len(t))
            assert len(t) == len(lat) == len(lon) == len(cog) >= 2
        except (KeyError, TypeError, AssertionError):
            continue   # 跳过损坏条目，其余继续
        ships.append(RealShip(
            ship_id=entry.get("id", f"RShip-{len(ships) + 1:02d}"),
            mmsi=entry.get("mmsi", 0),
            name=entry.get("name", ""),
            ship_type=entry.get("ship_type", 0),
            t=t, lat=lat, lon=lon, cog=cog, max_gap_s=gap))
        if max_ships is not None and len(ships) >= max_ships:
            break

    meta = {
        "source": data.get("source", "ais"),
        "date": data.get("date", ""),
        "ship_count": len(ships),
    }
    return ships, meta
