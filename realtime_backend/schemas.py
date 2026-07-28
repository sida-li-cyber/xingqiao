from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 注意：StatePayload / StateUpdate 模型已移除。
# 后端现在是透明转发层，不再校验 core 发来的状态消息内容。
# 协议格式定义见 docs/protocol-v2-multidomain.md


class CommandPayload(BaseModel):
    action: str = Field(..., description="控制命令，例如 play / pause / speed / metrics / filter / focus")
    params: dict[str, Any] | None = Field(default=None, description="可选参数")


class CommandMessage(BaseModel):
    message_type: Literal["command"] = "command"
    payload: CommandPayload
