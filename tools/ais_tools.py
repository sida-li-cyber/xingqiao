#!/usr/bin/env python3
"""AIS 真实船舶数据工具 — 下载 NOAA Marine Cadastre 每日 AIS 数据包并
转换为仿真核心可回放的紧凑轨迹 JSON。

数据源：NOAA Office for Coastal Management / Marine Cadastre
    https://coast.noaa.gov/htdata/CMSP/AISDataHandler/YYYY/
每日包 AIS_YYYY_MM_DD.zip 内含一个 CSV，列为：
    MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, ShipType
覆盖美国海域全部 AIS 报文，匿名可下载。

用法：
    # 1) 下载某一天的原始数据（约 300 MB）
    python tools/ais_tools.py fetch --date 2023-06-15

    # 2) 转换为紧凑轨迹 JSON（按区域/船数筛选；
    #    默认 bbox 覆盖中国近海：渤海/黄海/东海/台湾海峡/南海）
    python tools/ais_tools.py convert --bbox 105,3,130,41 --max-ships 50

    # 3) 合成中国近海演示轨迹（NOAA 仅覆盖美洲海域，中国海域无原始包时
    #    用该命令沿真实航道合成可回放轨迹，输出 schema 与 convert 一致）
    python tools/ais_tools.py generate --max-ships 50

    # 4) 离线自检（不依赖网络，用内置最小样例验证转换管线）
    python tools/ais_tools.py selftest

输出 JSON 结构（供 hypatia-master/satviz/ais_replay.py 回放）：
    {
      "source": "marine_cadastre", "date": "2023-06-15",
      "bbox": [lon_min, lat_min, lon_max, lat_max],
      "ships": [
        {"id": "RShip-01", "mmsi": 367123450, "name": "EXAMPLE",
         "ship_type": 70,
         "t": [0.0, 61.0, ...], "lat": [...], "lon": [...],
         "sog": [...], "cog": [...]},
        ...
      ]
    }
"""

import argparse
import csv
import io
import json
import math
import os
import random
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

# 项目根目录 = tools/ 的上一级
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AIS_DIR = os.path.join(ROOT_DIR, "realtime_backend", "data", "ais")
RAW_DIR = os.path.join(AIS_DIR, "raw")

NOAA_URL_TEMPLATE = ("https://coast.noaa.gov/htdata/CMSP/AISDataHandler/"
                     "{year}/AIS_{y:04d}_{m:02d}_{d:02d}.zip")

# 默认筛选框：中国近海及主要航运区域
# （渤海、黄海、东海、台湾海峡、南海）。注意 NOAA 数据仅覆盖美洲海域，
# 中国海域请用 generate 合成演示轨迹；convert 的 bbox 不命中时
# 会提示数据实际覆盖范围。
DEFAULT_BBOX = (105.0, 3.0, 130.0, 41.0)   # lon_min, lat_min, lon_max, lat_max
DEFAULT_MAX_SHIPS = 50
DEFAULT_MIN_POINTS = 30
DEFAULT_MAX_GAP_S = 3600.0
SOG_MOVING_MIN = 1.0      # 节：低于该航速视为锚泊/系泊，排序时降权


# ---------------------------------------------------------------------------
# fetch：下载 NOAA 每日 AIS 数据包
# ---------------------------------------------------------------------------

def cmd_fetch(args):
    """流式下载指定日期的每日 AIS zip 包，带进度与大小校验。"""
    try:
        dt = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"错误：--date 格式应为 YYYY-MM-DD，收到 {args.date!r}")

    url = NOAA_URL_TEMPLATE.format(year=dt.year, y=dt.year,
                                   m=dt.month, d=dt.day)
    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, f"AIS_{args.date.replace('-', '_')}.zip")

    if os.path.exists(out_path) and not args.force:
        print(f"已存在：{out_path}（使用 --force 重新下载）")
        return out_path

    print(f"下载 {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            tmp_path = out_path + ".part"
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        pct = 100.0 * got / total
                        print(f"\r  {got / 1e6:8.1f} / {total / 1e6:.1f} MB"
                              f"  ({pct:5.1f}%)", end="", flush=True)
            print()
    except Exception as exc:
        print(f"\n下载失败：{exc}")
        print("提示：可手动下载上述 URL 放入 realtime_backend/data/ais/raw/，"
              "再运行 convert。")
        sys.exit(1)

    if total and os.path.getsize(tmp_path) != total:
        sys.exit("错误：下载不完整，请重试（--force）。")
    os.replace(tmp_path, out_path)
    print(f"完成：{out_path}（{os.path.getsize(out_path) / 1e6:.1f} MB）")
    return out_path


# ---------------------------------------------------------------------------
# convert：原始 CSV -> 紧凑轨迹 JSON
# ---------------------------------------------------------------------------

def _open_csv_rows(input_path):
    """以流式方式逐行产出原始 CSV 的 dict 行；支持 .zip 或直接 .csv。"""
    if input_path.lower().endswith(".zip"):
        with zipfile.ZipFile(input_path) as zf:
            csv_name = next(n for n in zf.namelist()
                            if n.lower().endswith(".csv"))
            with zf.open(csv_name) as f:
                yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8",
                                                           errors="replace"))
    else:
        with open(input_path, newline="", encoding="utf-8",
                  errors="replace") as f:
            yield from csv.DictReader(f)


def _in_bbox(lon, lat, bbox):
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def _pick_input(args):
    """确定 convert 的输入文件：显式 --input 优先，否则取 raw/ 中最新包。"""
    if args.input:
        return args.input
    if os.path.isdir(RAW_DIR):
        zips = sorted(n for n in os.listdir(RAW_DIR)
                      if n.lower().endswith(".zip"))
        if zips:
            return os.path.join(RAW_DIR, zips[-1])
    sys.exit("错误：未找到原始数据；请先运行 fetch 或用 --input 指定 CSV/ZIP。")


def convert_tracks(input_path, out_path, bbox, max_ships, min_points,
                   max_gap_s):
    """两遍扫描：第一遍统计候选船，第二遍仅对候选船收集完整轨迹。

    返回写出的船舶数量。
    """
    # --- 第一遍：统计每艘船的报文总数与 bbox 内报文数 ---
    total_counts = {}
    bbox_counts = {}
    moving_counts = {}      # bbox 内 SOG > SOG_MOVING_MIN 的报文数（过滤锚泊船）
    names = {}
    n_rows = 0
    for row in _open_csv_rows(input_path):
        try:
            mmsi = int(row["MMSI"])
            lat = float(row["LAT"])
            lon = float(row["LON"])
        except (KeyError, TypeError, ValueError):
            continue
        n_rows += 1
        total_counts[mmsi] = total_counts.get(mmsi, 0) + 1
        if _in_bbox(lon, lat, bbox):
            bbox_counts[mmsi] = bbox_counts.get(mmsi, 0) + 1
            try:
                if float(row.get("SOG") or 0) > SOG_MOVING_MIN:
                    moving_counts[mmsi] = moving_counts.get(mmsi, 0) + 1
            except ValueError:
                pass
        name = (row.get("VesselName") or "").strip()
        if name and mmsi not in names:
            names[mmsi] = name

    candidates = sorted(
        (m for m, c in bbox_counts.items() if c >= min_points),
        key=lambda m: (-moving_counts.get(m, 0),
                       -bbox_counts[m], -total_counts.get(m, 0)))[:max_ships]

    if not candidates:
        # bbox 内没有足够的船：报告数据实际覆盖范围，便于放宽 bbox
        lats, lons = _sample_coverage(input_path)
        if lats:
            print(f"警告：bbox={bbox} 内满足条件的船不足。"
                  f"数据实际覆盖约 lon[{lons[0]:.1f},{lons[1]:.1f}] "
                  f"lat[{lats[0]:.1f},{lats[1]:.1f}]，"
                  f"请调整 --bbox 后重试。")
        else:
            print("警告：输入文件没有可解析的 AIS 报文。")
        return 0

    cand_set = set(candidates)

    # --- 第二遍：仅收集候选船的完整轨迹 ---
    tracks = {m: [] for m in cand_set}
    types = {}
    for row in _open_csv_rows(input_path):
        try:
            mmsi = int(row["MMSI"])
        except (KeyError, TypeError, ValueError):
            continue
        if mmsi not in cand_set:
            continue
        try:
            t = datetime.fromisoformat(row["BaseDateTime"]).timestamp()
            lat = float(row["LAT"])
            lon = float(row["LON"])
            sog = float(row.get("SOG") or 0)
            cog = float(row.get("COG") or 0) % 360.0
        except (KeyError, TypeError, ValueError):
            continue
        tracks[mmsi].append((t, lat, lon, sog, cog))
        if mmsi not in types:
            try:
                types[mmsi] = int(row.get("ShipType") or 0)
            except ValueError:
                types[mmsi] = 0

    # --- 组装输出：候选船已按移动报文数排序，时间归一化、坐标取 4 位小数 ---
    ships = []
    for idx, mmsi in enumerate(candidates):
        pts = sorted(tracks[mmsi], key=lambda p: p[0])
        # 去重：同一时刻只保留一条
        dedup = []
        for p in pts:
            if not dedup or p[0] > dedup[-1][0]:
                dedup.append(p)
        if len(dedup) < 2:
            continue
        t0 = dedup[0][0]
        ships.append({
            "id": f"RShip-{len(ships) + 1:02d}",
            "mmsi": mmsi,
            "name": names.get(mmsi, f"MMSI-{mmsi}"),
            "ship_type": types.get(mmsi, 0),
            "t": [round(p[0] - t0, 1) for p in dedup],
            "lat": [round(p[1], 4) for p in dedup],
            "lon": [round(p[2], 4) for p in dedup],
            "sog": [round(p[3], 1) for p in dedup],
            "cog": [round(p[4], 1) for p in dedup],
        })
        if len(ships) >= max_ships:
            break

    # 数据日期取自文件名（AIS_YYYY_MM_DD.zip）或首条报文时间
    base = os.path.basename(input_path)
    date_str = ""
    if base.startswith("AIS_"):
        date_str = base[4:14].replace("_", "-")
    out = {
        "source": "marine_cadastre",
        "date": date_str,
        "bbox": list(bbox),
        "max_gap_s": max_gap_s,
        "ships": ships,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"共扫描 {n_rows} 条报文；输出 {len(ships)} 艘船 -> {out_path}"
          f"（{os.path.getsize(out_path) / 1e6:.2f} MB）")
    return len(ships)


def _sample_coverage(input_path, limit=20000):
    """抽样前 limit 条报文估计数据覆盖的经纬度范围。"""
    lats, lons = [], []
    try:
        for i, row in enumerate(_open_csv_rows(input_path)):
            if i >= limit:
                break
            try:
                lats.append(float(row["LAT"]))
                lons.append(float(row["LON"]))
            except (KeyError, TypeError, ValueError):
                continue
    except Exception:
        pass
    if not lats:
        return None, None
    return (min(lats), max(lats)), (min(lons), max(lons))


def cmd_convert(args):
    input_path = _pick_input(args)
    bbox = DEFAULT_BBOX
    if args.bbox:
        try:
            bbox = tuple(float(x) for x in args.bbox.split(","))
            assert len(bbox) == 4
        except (ValueError, AssertionError):
            sys.exit("错误：--bbox 应为 lon_min,lat_min,lon_max,lat_max")
    out_path = args.output or os.path.join(
        AIS_DIR, "ships_marine_cadastre.json")
    print(f"输入：{input_path}")
    n = convert_tracks(input_path, out_path, bbox, args.max_ships,
                       args.min_points, args.max_gap_s)
    if n == 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# generate：中国近海演示轨迹合成
# ---------------------------------------------------------------------------
# NOAA Marine Cadastre 仅覆盖美洲海域；为点亮中国近海展示，该命令沿
# 中国近海主要航道合成符合 convert 输出 schema 的可回放轨迹。
# 数据仅供演示/教学，source 字段标注为 synthetic。

# 中国近海主要航道（(lat, lon) 航点序列）
CHINA_ROUTES = [
    # 天津 - 青岛（渤海/黄海沿岸南下）
    [(38.9, 118.0), (38.3, 118.7), (37.6, 119.4), (36.9, 120.0), (36.1, 120.4)],
    # 大连 - 烟台（渤海海峡渡轮）
    [(38.9, 121.6), (38.5, 121.2), (38.0, 120.9), (37.6, 121.4)],
    # 黄海南部 - 长江口（沿岸航线）
    [(36.0, 120.6), (34.5, 120.1), (33.0, 120.6), (31.8, 121.6), (31.0, 122.0)],
    # 上海/宁波外海（东海沿岸航线）
    [(31.0, 122.2), (30.0, 122.4), (29.0, 122.2), (28.0, 121.8)],
    # 东海干线（经宫古海峡方向）
    [(28.5, 122.0), (27.5, 123.5), (26.8, 125.0), (26.2, 126.5)],
    # 台湾海峡南北向干线（近岸侧）
    [(24.0, 118.2), (25.0, 119.0), (26.0, 119.8), (27.0, 120.5), (28.0, 121.2)],
    # 台湾海峡南北向干线（外侧）
    [(23.2, 117.2), (24.4, 118.0), (25.6, 118.8), (26.8, 119.6), (27.8, 120.3)],
    # 粤东 - 厦门 - 福州（沿岸航线）
    [(22.3, 115.0), (23.0, 116.2), (23.8, 117.2), (24.4, 118.1), (25.4, 119.2)],
    # 珠江口 - 香港/深圳
    [(21.8, 113.4), (22.1, 113.8), (22.3, 114.1), (22.5, 114.4), (22.8, 114.8)],
    # 香港 - 海南西部（南海北部干线）
    [(22.0, 114.2), (21.5, 112.5), (21.0, 111.0), (20.3, 110.6), (19.8, 110.3),
     (19.0, 109.8), (18.3, 109.5)],
    # 北部湾（琼州海峡 - 钦州/防城港）
    [(20.1, 110.4), (20.3, 109.6), (20.8, 109.0), (21.5, 108.5), (21.7, 108.3)],
    # 南海南下干线（向马六甲海峡方向）
    [(21.0, 111.5), (19.0, 110.8), (17.0, 110.5), (15.0, 110.8), (13.0, 111.5),
     (11.0, 112.5), (9.0, 113.0)],
]

# 演示船名池（船名, AIS ShipType）：货船 70-74 / 油轮 80-84 / 客船 60-69
_DEMO_SHIP_POOL = [
    ("COSCO SHIPPING TAURUS", 70), ("COSCO SHIPPING DENALI", 70),
    ("COSCO SHIPPING PISCES", 71), ("EVER LUCENT", 70), ("EVER LOTUS", 70),
    ("MSC AURORA", 71), ("PACIFIC PIONEER", 70), ("GOLDEN BRIDGE", 72),
    ("ORIENTAL PEARL", 71), ("ZHONG GUANG ZHOU", 70),
    ("XIN HAI TONG 168", 70), ("HAI LI 8", 70), ("MIN DONG HUO 0168", 70),
    ("YUE ZHAN JIANG HUO 9898", 70), ("ZHE NING HAI 66", 70),
    ("SHANDONG XIN HAI", 72), ("CHANG HANG HAI 9", 70), ("JI SU 12", 70),
    ("HAI SHUN 16", 72), ("HAI FENG 666", 70), ("XIN HAI HUI 88", 70),
    ("SHEN HAI 168", 70), ("ZHE DAI ZHOU HUO 0088", 70),
    ("GUANG ZHOU HUO 1234", 70), ("YUAN WANG HUO 68", 72),
    ("WAN HAI 501", 71), ("SITC INCHON", 71), ("NEWNEW SHIPPING", 73),
    ("HAI XIN 66", 73), ("LU SHAN HAI 18", 74), ("DONG FANG HAI 66", 74),
    ("MING HUA 128", 74), ("TONG HAI 8", 73), ("YUE HAI 668", 74),
    ("DA QING 45", 80), ("ZHONG HAI YOU 368", 80), ("HAI HUI 668", 81),
    ("GOLDEN CROWN", 80), ("OCEAN GLORY", 80), ("NEW PIONEER", 81),
    ("ZHONG HUA YOU 9", 80), ("HAI HUA 88", 82), ("SHUN XIN 168", 81),
    ("KUN LUN SHAN", 84),
    ("BO HAI CUI ZHU", 60), ("BO HAI MA ZHU", 60), ("ZHONG YU 8", 60),
    ("YONG XING DAO", 60), ("CHANG DA LONG DAO", 60),
    ("HAI XIA 1", 60), ("HAI XIA 3", 60), ("PU TAO DAO", 60),
]

_M_PER_NM = 1852.0


def _seg_m(p, q):
    """等距近似下两 (lat, lon) 点的距离(米)与东/北分量。"""
    cos_lat = math.cos(math.radians((p[0] + q[0]) / 2))
    dy = (q[0] - p[0]) * 111320.0
    dx = (q[1] - p[1]) * 111320.0 * cos_lat
    return math.hypot(dx, dy), dx, dy


def _build_route_m(route):
    """折线 -> [(cum_a, cum_b, p, q, dx, dy), ...] 与总长(米)。"""
    segs = []
    cum = 0.0
    for p, q in zip(route, route[1:]):
        length, dx, dy = _seg_m(p, q)
        segs.append((cum, cum + length, p, q, dx, dy))
        cum += length
    return segs, cum


def _point_at(segs, total, s):
    """弧长 s 处的 ((lat, lon), 航向 cog)。"""
    s = min(max(s, 0.0), total)
    for a, b, p, q, dx, dy in segs:
        if s <= b:
            f = (s - a) / (b - a) if b > a else 0.0
            return ((p[0] + f * (q[0] - p[0]), p[1] + f * (q[1] - p[1])),
                    math.degrees(math.atan2(dx, dy)) % 360.0)
    _a, _b, _p, q, dx, dy = segs[-1]
    return q, math.degrees(math.atan2(dx, dy)) % 360.0


def generate_tracks(out_path, bbox, max_ships, duration_s=7200.0,
                    dt_s=120.0, seed=20260827):
    """沿 CHINA_ROUTES 合成 max_ships 艘演示船轨迹，写出 convert 同构 JSON。"""
    rng = random.Random(seed)
    pool = list(_DEMO_SHIP_POOL)
    rng.shuffle(pool)

    ships = []
    mmsis = set()
    for i in range(max_ships):
        route = list(CHINA_ROUTES[i % len(CHINA_ROUTES)])
        if rng.random() < 0.5:
            route.reverse()
        segs, total = _build_route_m(route)

        speed_kn = rng.uniform(8.0, 17.0)
        travel = speed_kn * _M_PER_NM / 3600.0 * duration_s
        if travel > 0.9 * total:      # 不超出航线端点
            speed_kn *= 0.9 * total / travel
            travel = 0.9 * total
        s0 = rng.uniform(0.0, total - travel)
        lat_off = rng.uniform(-0.12, 0.12)   # 平行航道横向偏移
        lon_off = rng.uniform(-0.12, 0.12)

        name, ship_type = pool[i % len(pool)]
        mmsi = 0
        while mmsi in mmsis or mmsi == 0:
            mid = rng.choice((412, 413, 414, 477))  # 中国/香港 MID
            mmsi = mid * 1000000 + rng.randint(0, 999999)
        mmsis.add(mmsi)

        t, lats, lons, sogs, cogs = [], [], [], [], []
        n = int(duration_s // dt_s)
        for k in range(n + 1):
            (lat, lon), cog = _point_at(segs, total, s0 + travel * k / n)
            t.append(round(k * dt_s, 1))
            lats.append(round(lat + lat_off, 4))
            lons.append(round(lon + lon_off, 4))
            sogs.append(round(speed_kn + rng.uniform(-0.3, 0.3), 1))
            cogs.append(round(cog, 1))
        ships.append({
            "id": f"RShip-{len(ships) + 1:02d}",
            "mmsi": mmsi, "name": name, "ship_type": ship_type,
            "t": t, "lat": lats, "lon": lons, "sog": sogs, "cog": cogs,
        })

    out = {
        "source": "china_near_seas_synthetic",
        "date": "",
        "bbox": list(bbox),
        "max_gap_s": DEFAULT_MAX_GAP_S,
        "ships": ships,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"合成 {len(ships)} 艘中国近海演示轨迹 -> {out_path}"
          f"（{os.path.getsize(out_path) / 1e6:.2f} MB）")
    return len(ships)


def cmd_generate(args):
    bbox = DEFAULT_BBOX
    if args.bbox:
        try:
            bbox = tuple(float(x) for x in args.bbox.split(","))
            assert len(bbox) == 4
        except (ValueError, AssertionError):
            sys.exit("错误：--bbox 应为 lon_min,lat_min,lon_max,lat_max")
    out_path = args.output or os.path.join(
        AIS_DIR, "ships_marine_cadastre.json")
    generate_tracks(out_path, bbox, args.max_ships, seed=args.seed)


# ---------------------------------------------------------------------------
# selftest：内置最小样例验证转换管线（不依赖网络）
# ---------------------------------------------------------------------------

SELFTEST_CSV = (
    "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,ShipType\n"
    "367000001,2023-06-15T00:00:00,10.0,110.0,12.0,45.0,44,TEST SHIP A,70\n"
    "367000001,2023-06-15T00:01:00,10.02,110.02,12.1,45.5,44,TEST SHIP A,70\n"
    "367000001,2023-06-15T00:02:00,10.04,110.04,12.2,46.0,44,TEST SHIP A,70\n"
    "367000002,2023-06-15T00:00:00,50.0,-70.0,8.0,200.0,199,OUTSIDE B,71\n"
    "367000002,2023-06-15T00:01:00,50.01,-70.01,8.1,200.5,199,OUTSIDE B,71\n"
)


def cmd_selftest(_args):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "sample.csv")
        out_path = os.path.join(td, "ships_test.json")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(SELFTEST_CSV)
        n = convert_tracks(csv_path, out_path, DEFAULT_BBOX,
                           max_ships=5, min_points=2, max_gap_s=3600.0)
        assert n == 1, f"期望筛选出 1 艘 bbox 内的船，实际 {n}"
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        ship = data["ships"][0]
        assert ship["mmsi"] == 367000001
        assert ship["t"][0] == 0.0 and abs(ship["t"][-1] - 120.0) < 1e-6
        assert len(ship["lat"]) == len(ship["lon"]) == len(ship["t"])
        # 回放模块联动校验
        sys.path.insert(0, os.path.join(ROOT_DIR, "hypatia-master", "satviz"))
        from ais_replay import load_ais_tracks
        ships, meta = load_ais_tracks(out_path)
        assert len(ships) == 1 and ships[0].id == "RShip-01"
        lat, lon, alt, cog = ships[0].get_position(30.0)
        assert abs(lat - 10.01) < 1e-3 and abs(lon - 110.01) < 1e-3
    print("selftest 通过：convert 管线与 ais_replay 联动正常。")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AIS 真实船舶数据工具（NOAA Marine Cadastre）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="下载 NOAA 每日 AIS 数据包")
    p_fetch.add_argument("--date", required=True, metavar="YYYY-MM-DD",
                         help="数据日期")
    p_fetch.add_argument("--force", action="store_true",
                         help="已存在时重新下载")
    p_fetch.set_defaults(func=cmd_fetch)

    p_conv = sub.add_parser("convert", help="原始 CSV/ZIP -> 轨迹 JSON")
    p_conv.add_argument("--input", default=None,
                        help="原始 CSV 或 ZIP 路径（默认取 raw/ 中最新包）")
    p_conv.add_argument("--output", default=None, help="输出 JSON 路径")
    p_conv.add_argument("--bbox", default=None,
                        help="筛选框 lon_min,lat_min,lon_max,lat_max "
                             f"（默认 {DEFAULT_BBOX}）")
    p_conv.add_argument("--max-ships", type=int, default=DEFAULT_MAX_SHIPS)
    p_conv.add_argument("--min-points", type=int,
                        default=DEFAULT_MIN_POINTS,
                        help="单船在 bbox 内的最少报文数")
    p_conv.add_argument("--max-gap-s", type=float, default=DEFAULT_MAX_GAP_S,
                        help="轨迹时间空洞上限（秒）")
    p_conv.set_defaults(func=cmd_convert)

    p_gen = sub.add_parser("generate",
                           help="合成中国近海演示轨迹（NOAA 不覆盖中国海域）")
    p_gen.add_argument("--output", default=None, help="输出 JSON 路径")
    p_gen.add_argument("--bbox", default=None,
                       help="筛选框 lon_min,lat_min,lon_max,lat_max "
                            f"（默认 {DEFAULT_BBOX}）")
    p_gen.add_argument("--max-ships", type=int, default=DEFAULT_MAX_SHIPS)
    p_gen.add_argument("--seed", type=int, default=20260827,
                       help="随机种子（默认定值，保证可复现）")
    p_gen.set_defaults(func=cmd_generate)

    p_self = sub.add_parser("selftest", help="内置样例离线自检")
    p_self.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
