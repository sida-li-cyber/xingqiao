from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket连接管理器，用于管理客户端和核心的连接"""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.cores: set[WebSocket] = set()
        logger.info("ConnectionManager initialized")

    async def connect_client(self, websocket: WebSocket) -> None:
        """建立客户端连接"""
        await websocket.accept()
        self.clients.add(websocket)
        logger.info(f"Client connected. Total clients: {len(self.clients)}")

    async def connect_core(self, websocket: WebSocket) -> None:
        """建立核心连接"""
        await websocket.accept()
        self.cores.add(websocket)
        logger.info(f"Core connected. Total cores: {len(self.cores)}")

    def disconnect_client(self, websocket: WebSocket) -> None:
        """断开客户端连接"""
        self.clients.discard(websocket)
        logger.info(f"Client disconnected. Total clients: {len(self.clients)}")

    def disconnect_core(self, websocket: WebSocket) -> None:
        """断开核心连接"""
        self.cores.discard(websocket)
        logger.info(f"Core disconnected. Total cores: {len(self.cores)}")

    async def broadcast_state(self, message: dict[str, Any]) -> None:
        """向所有客户端广播状态更新"""
        if not self.clients:
            logger.debug("No clients connected, skipping broadcast")
            return

        disconnected_clients = []
        for connection in set(self.clients):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to client: {e}")
                disconnected_clients.append(connection)

        # 清除断开的连接
        for connection in disconnected_clients:
            self.disconnect_client(connection)

    async def forward_command_to_core(self, message: dict[str, Any]) -> None:
        """转发命令给所有核心实例"""
        if not self.cores:
            logger.warning("No cores connected, command will not be forwarded")
            return

        disconnected_cores = []
        for connection in set(self.cores):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending command to core: {e}")
                disconnected_cores.append(connection)

        # 清除断开的连接
        for connection in disconnected_cores:
            self.disconnect_core(connection)

    async def send_personal_message(
        self, websocket: WebSocket, message: dict[str, Any]
    ) -> None:
        """发送个人消息给指定客户端"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")

    def has_clients(self) -> bool:
        """检查是否有客户端连接"""
        return len(self.clients) > 0

    def has_cores(self) -> bool:
        """检查是否有核心连接"""
        return len(self.cores) > 0

    def get_status(self) -> dict[str, Any]:
        """获取连接管理器的状态"""
        return {
            "clients_count": len(self.clients),
            "cores_count": len(self.cores),
            "has_clients": self.has_clients(),
            "has_cores": self.has_cores(),
        }


manager = ConnectionManager()
