from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import load_settings
from .core import manager
from .edu import EduStore
from .edu_api import init_edu_router, router as edu_router
from .files import CHUNK_SIZE, FileStore
from .files_api import init_files_router, router as files_router
from .schemas import CommandMessage

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = load_settings()

# File-transfer data plane (Milestone A, phase B): uploaded bytes live here and
# are reassembled from the simulation core's file_chunk_delivered events.
FILE_STORE_DIR = Path(__file__).resolve().parent / "data" / "files"
file_store = FileStore(FILE_STORE_DIR)
init_files_router(file_store)

# Education data plane (improvement plan phase 2): lightweight accounts and
# server-side experiment records (JSON file, no external database).
EDU_DB_PATH = Path(os.environ.get(
    "STARBRIDGE_EDU_DB",
    str(Path(__file__).resolve().parent / "data" / "edu" / "db.json")))
edu_store = EduStore(EDU_DB_PATH)
init_edu_router(edu_store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Application starting...")
    logger.info(
        f"Server configuration - Host: {settings.host}, Port: {settings.port}"
    )
    logger.info(f"Client WebSocket path: {settings.client_ws_path}")
    logger.info(f"Core WebSocket path: {settings.core_ws_path}")
    logger.info(f"CORS allowed origins: {settings.allowed_origins}")
    yield
    logger.info("Application shutting down...")


app = FastAPI(
    title="Realtime Simulation Backend",
    description="FastAPI backend for realtime visualization with WebSocket state broadcasting and command forwarding.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# File-transfer data plane endpoints (upload / list / download / delete).
app.include_router(files_router)

# Education data plane endpoints (login / records / gradebook / stats).
app.include_router(edu_router)


@app.get("/health")
async def health_check() -> dict:
    """健康检查端点"""
    logger.debug("Health check requested")
    return {
        "status": "ok",
        "clients_connected": len(manager.clients),
        "cores_connected": len(manager.cores),
    }


@app.get("/status")
async def get_status() -> dict:
    """获取服务器状态"""
    return {
        "status": "running",
        "clients_connected": len(manager.clients),
        "cores_connected": len(manager.cores),
        "config": {
            "host": settings.host,
            "port": settings.port,
            "client_ws_path": settings.client_ws_path,
            "core_ws_path": settings.core_ws_path,
        },
    }


@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    """处理值错误"""
    logger.error(f"Value error: {exc}")
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid input data"},
    )


def handle_file_command(command_dict: dict) -> dict | None:
    """Enrich file_send / file_cancel commands and update the data plane.

    file_send: look up the stored upload, inject total_bytes / chunk_size /
    name into the params (the core models abstract chunks and needs only the
    byte count), mark the record TRANSFERRING, and return the enriched command
    to forward to the core. Returns None for an unknown file_id so the caller
    can reply with an error instead of forwarding.
    """
    payload = command_dict.get("payload", {})
    action = payload.get("action", "")
    params = payload.get("params") or {}

    if action == "file_send":
        file_id = params.get("file_id")
        rec = file_store.get(file_id) if file_id else None
        if rec is None:
            return None
        params = dict(params)
        params["total_bytes"] = rec.total_bytes
        params["chunk_size"] = rec.chunk_size
        params.setdefault("name", rec.name)
        file_store.mark_transferring(
            file_id, params.get("src", ""), params.get("dst", ""))
        payload = dict(payload)
        payload["params"] = params
        out = dict(command_dict)
        out["payload"] = payload
        return out

    if action == "file_cancel":
        file_id = params.get("file_id")
        if file_id:
            file_store.cancel(file_id)
    return command_dict


def handle_file_event(message: dict) -> None:
    """Ingest a core file_event message into the data plane (no broadcast).

    Drives reassembly: file_chunk_delivered marks chunks received (the store
    reassembles + SHA-256-verifies once all arrive); file_complete / file_cancel
    update the record state. Clients learn progress via state_update.file_transfers.
    """
    for ev in message.get("events", []):
        etype = ev.get("type")
        fid = ev.get("file_id")
        if not fid:
            continue
        if etype == "file_chunk_delivered":
            file_store.on_chunk_delivered(fid, ev.get("seq", -1))
        elif etype == "file_complete":
            file_store.on_complete(fid)
        elif etype == "file_cancelled":
            file_store.cancel(fid)


@app.websocket(settings.client_ws_path)
async def client_endpoint(websocket: WebSocket) -> None:
    """前端客户端WebSocket端点"""
    client_id = id(websocket)
    logger.info(f"Client {client_id} attempting to connect")

    try:
        await manager.connect_client(websocket)
        logger.info(
            f"Client {client_id} connected successfully. Total clients: {len(manager.clients)}"
        )

        while True:
            try:
                message_data = await websocket.receive_json()
                logger.debug(
                    f"Client {client_id} sent command: {message_data.get('message_type')}"
                )

                command = CommandMessage(**message_data)
                if not manager.has_cores():
                    await manager.send_personal_message(
                        websocket,
                        {
                            "message_type": "error",
                            "payload": {
                                "status": "no_core_connected",
                                "detail": "No simulation core is connected to receive commands.",
                            },
                        },
                    )
                    continue

                # File-transfer commands are enriched with the stored byte
                # count / chunk size before forwarding to the core.
                action = command.payload.action
                if action in ("file_send", "file_cancel"):
                    enriched = handle_file_command(command.dict())
                    if enriched is None:
                        await manager.send_personal_message(
                            websocket,
                            {
                                "message_type": "error",
                                "payload": {
                                    "status": "unknown_file",
                                    "detail": "file_id not found; upload it first via /api/files/upload.",
                                },
                            },
                        )
                        continue
                    await manager.forward_command_to_core(enriched)
                else:
                    await manager.forward_command_to_core(command.dict())
                await manager.send_personal_message(
                    websocket,
                    {
                        "message_type": "ack",
                        "payload": {
                            "action": command.payload.action,
                            "params": command.payload.params,
                            "status": "forwarded",
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    },
                )
            except ValueError as e:
                logger.error(f"Client {client_id} sent invalid data: {e}")
                await manager.send_personal_message(
                    websocket,
                    {
                        "message_type": "error",
                        "payload": {"error": "Invalid message format"},
                    },
                )

    except WebSocketDisconnect:
        logger.info(
            f"Client {client_id} disconnected. Total clients: {len(manager.clients) - 1}"
        )
        manager.disconnect_client(websocket)
    except Exception as e:
        logger.error(
            f"Unexpected error in client endpoint {client_id}: {e}", exc_info=True
        )
        manager.disconnect_client(websocket)
        try:
            await websocket.close(code=status.WS_1011_SERVER_ERROR)
        except Exception:
            pass


@app.websocket(settings.core_ws_path)
async def core_endpoint(websocket: WebSocket) -> None:
    """仿真核心WebSocket端点"""
    core_id = id(websocket)
    logger.info(f"Core {core_id} attempting to connect")

    try:
        await manager.connect_core(websocket)
        logger.info(
            f"Core {core_id} connected successfully. Total cores: {len(manager.cores)}"
        )
        logger.info(
            f"Will broadcast state updates to {len(manager.clients)} connected clients"
        )

        while True:
            try:
                message_data = await websocket.receive_json()
                message_type = message_data.get("message_type", "unknown")
                logger.debug(f"Core {core_id} sent message type: {message_type}")

                # File delivery events drive backend reassembly only; they are
                # not broadcast (progress reaches clients via file_transfers).
                if message_type == "file_event":
                    handle_file_event(message_data)
                    continue

                # 透明转发：将 core 发来的所有消息广播给全部客户端
                await manager.broadcast_state(message_data)
                logger.debug(
                    f"Relayed '{message_type}' to {len(manager.clients)} clients"
                )
            except ValueError as e:
                logger.error(f"Core {core_id} sent invalid JSON: {e}")

    except WebSocketDisconnect:
        logger.info(
            f"Core {core_id} disconnected. Total cores: {len(manager.cores) - 1}"
        )
        logger.info(f"Remaining clients: {len(manager.clients)}")
        manager.disconnect_core(websocket)
    except Exception as e:
        logger.error(
            f"Unexpected error in core endpoint {core_id}: {e}", exc_info=True
        )
        manager.disconnect_core(websocket)
        try:
            await websocket.close(code=status.WS_1011_SERVER_ERROR)
        except Exception:
            pass
