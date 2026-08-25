import asyncio
import sys

from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/ws/echo")
async def echo(ws: WebSocket):
    await ws.accept()
    print("SERVER: connection open", flush=True)
    while True:
        data = await ws.receive_text()
        await ws.send_text("echo:" + data)

if __name__ == "__main__":
    import uvicorn
    impl = sys.argv[1] if len(sys.argv) > 1 else "auto"
    uvicorn.run(app, host="127.0.0.1", port=8901, ws=impl,
                ws_per_message_deflate=False, log_level="info")
