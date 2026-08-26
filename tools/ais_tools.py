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

    # 2) 转换为紧凑轨迹 JSON（按区域/船数筛选）
    python tools/ais_tools.py convert --bbox 105,5,125,25 --max-ships 20

    # 3) 离线自检（不依赖网络，用内置最小样例验证转换管线）
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
import os
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

# 演示航线区域（南海）的默认筛选框；NOAA 数据覆盖美国海域，
# 若该框内无船只，convert 会自动放宽并提示实际覆盖范围。
DEFAULT_BBOX = (105.0, 5.0, 125.0, 25.0)   # lon_min, lat_min, lon_max, lat_max
DEFAULT_MAX_SHIPS = 20
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

    p_self = sub.add_parser("selftest", help="内置样例离线自检")
    p_self.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
