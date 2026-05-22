from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import load_settings
from .core import manager
from .schemas import CommandMessage, StateUpdate

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = load_settings()


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

                await manager.forward_command_to_core(command.dict())
                await manager.send_personal_message(
                    websocket,
                    {
                        "message_type": "ack",
                        "payload": {
                            "action": command.payload.action,
                            "params": command.payload.params,
                            "status": "forwarded",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
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
                message_type = message_data.get("message_type")
                logger.debug(f"Core {core_id} sent message type: {message_type}")

                # Messages to broadcast directly to all clients
                broadcast_types = {settings.state_message_type, "simulation_init"}

                if message_type in broadcast_types:
                    logger.debug(
                        f"Broadcasting {message_type} to {len(manager.clients)} clients"
                    )
                    await manager.broadcast_state(message_data)
                else:
                    logger.debug(f"Core {core_id} sent non-broadcast message: {message_type}")
                    await manager.send_personal_message(
                        websocket,
                        {
                            "message_type": "ack",
                            "payload": {
                                "status": "received",
                                "detail": "Core message accepted.",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        },
                    )
            except ValueError as e:
                logger.error(f"Core {core_id} sent invalid data: {e}")

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
