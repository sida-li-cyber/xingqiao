import asyncio
import websockets

async def main():
    uri = "ws://127.0.0.1:8901/ws/echo"
    print("CLIENT: connecting", uri, flush=True)
    try:
        async with websockets.connect(uri, ping_interval=20) as ws:
            await ws.send("hello")
            reply = await asyncio.wait_for(ws.recv(), timeout=5)
            print("CLIENT: OK ->", reply, flush=True)
    except Exception as e:
        print("CLIENT: FAIL ->", type(e).__name__, str(e)[:200], flush=True)

asyncio.run(main())
