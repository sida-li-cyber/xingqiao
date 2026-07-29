"""
file_transfer_client.py — 命令行文件传输客户端（里程碑 A 演示 / 测试工具）

在仿真网络里真实传一个文件：上传 -> 指定源/目的端发送 -> 实时打印进度与路径
-> 传输完成后下载重组字节并做 SHA-256 校验。前端上传界面（C 期）完成前，
用这个工具体验 / 验证整条数据面。

需要先启动后端与仿真核心（两个终端）：

    python -m realtime_backend.run --port 8000
    python hypatia-master/satviz/demo_sim_core.py --port 8000

然后（默认从 UAV-01 传到 Beijing）：

    python tools/file_transfer_client.py 你的文件.pdf

常用参数：
    --src UAV-01        源节点（UAV-01..08 / Ship-01..10 / 地面站名）
    --dst Beijing       目的节点（地面站名，如 Beijing/Shanghai/Tokyo...）
    --rate 5000000      注入速率上限 (bps)，默认 5 Mbps；调大传得更快
    --port 8000         后端端口
    --no-download       只传不下载校验
"""
import argparse
import asyncio
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    import websockets
except ImportError:
    print("需要 websockets 库：pip install websockets")
    sys.exit(1)


def http_json(url, data=None, method=None, headers=None, timeout=15):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def upload(base, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    blob = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    return http_json(
        f"{base}/api/files/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def download(base, file_id: str) -> bytes:
    with urllib.request.urlopen(
            f"{base}/api/files/{file_id}/download", timeout=30) as r:
        return r.read()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


async def run(args):
    base = f"http://{args.host}:{args.port}"
    ws_url = f"ws://{args.host}:{args.port}/ws/client"
    path = Path(args.file)
    if not path.is_file():
        print(f"找不到文件: {path}")
        return 1

    # 后端健康检查
    try:
        h = http_json(f"{base}/health", timeout=3)
        if h.get("cores_connected", 0) < 1:
            print("警告：后端已连上，但还没有仿真核心连接（先启动 demo_sim_core）。")
    except Exception as e:
        print(f"连不上后端 {base}：{e}")
        print("先启动：python -m realtime_backend.run --port %d" % args.port)
        return 1

    blob = path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    print(f"文件   : {path.name}  ({human(len(blob))})")
    print(f"SHA-256: {sha}")

    rec = upload(base, path)
    fid = rec["file_id"]
    print(f"已上传 : file_id={fid}  分片={rec['total_chunks']} x {rec['chunk_size']} B")
    print(f"发送   : {args.src} -> {args.dst}  (速率上限 {human(args.rate)}/s)")

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({
            "message_type": "command",
            "payload": {"action": "file_send",
                        "params": {"file_id": fid, "src": args.src,
                                   "dst": args.dst, "rate_bps": args.rate}},
        }))
        start = time.time()
        last_line = ""
        while True:
            if time.time() - start > args.timeout:
                print(f"\n超时（{args.timeout}s）未完成。")
                return 1
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("message_type") != "state_update":
                continue
            ft = (msg.get("payload") or {}).get("file_transfers") or {}
            info = ft.get(fid)
            if not info:
                continue
            pct = info["progress"] * 100
            path_str = " -> ".join(info.get("path") or [])
            line = (f"\r  {pct:5.1f}%  {human(info['delivered_bytes'])}/"
                    f"{human(info['total_bytes'])}  "
                    f"{human(info['throughput_bps'])}b/s  重传={info['retx']}  "
                    f"{info['state']}  路径: {path_str[:60]}")
            print(line, end="", flush=True)
            last_line = line
            if info["state"] == "COMPLETE":
                print()
                break
            if info["state"] == "CANCELLED":
                print("\n传输被取消。")
                return 1

    if args.no_download:
        print("完成（已跳过下载校验）。")
        return 0

    got = download(base, fid)
    got_sha = hashlib.sha256(got).hexdigest()
    ok = got_sha == sha and len(got) == len(blob)
    print(f"下载   : {human(len(got))}  SHA-256={got_sha}")
    print("校验   : " + ("通过 — 目的端字节与原始文件完全一致 [OK]"
                         if ok else "失败 — 哈希不一致 [FAIL]"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="在仿真网络里传输一个真实文件（上传/发送/追踪/下载校验）")
    ap.add_argument("file", help="要传输的文件路径")
    ap.add_argument("--src", default="UAV-01", help="源节点（默认 UAV-01）")
    ap.add_argument("--dst", default="Beijing", help="目的节点（默认 Beijing）")
    ap.add_argument("--rate", type=float, default=5e6,
                    help="注入速率上限 bps（默认 5000000）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="等待传输完成的超时秒数（默认 180）")
    ap.add_argument("--no-download", action="store_true",
                    help="只传输，不下载校验")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
