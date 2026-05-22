from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class StatePayload(BaseModel):
    satellite_positions: dict[str, Any] = Field(..., description="Satellite position data")
    link_status: dict[str, Any] = Field(..., description="Link status data")
    routing: dict[str, Any] = Field(..., description="Routing information")
    bandwidth_utilization: dict[str, Any] = Field(..., description="Bandwidth utilization metrics")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StateUpdate(BaseModel):
    message_type: Literal["state_update"] = "state_update"
    payload: StatePayload


class CommandPayload(BaseModel):
    action: str = Field(..., description="控制命令，例如 pause / resume / speed / metrics")
    params: dict[str, Any] | None = Field(default=None, description="可选参数")


class CommandMessage(BaseModel):
    message_type: Literal["command"] = "command"
    payload: CommandPayload
