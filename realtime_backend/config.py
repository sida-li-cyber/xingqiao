from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.yaml"


class Settings(BaseSettings):
    """服务器配置设置"""

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: str = "info"

    # CORS配置
    allowed_origins: list[str] = ["*"]

    # WebSocket路径配置
    client_ws_path: str = "/ws/client"
    core_ws_path: str = "/ws/core"

    # 消息类型配置
    state_message_type: str = "state_update"
    command_message_type: str = "command"

    class Config:
        env_prefix = "APP_"
        case_sensitive = False


def load_settings(config_path: Path | None = None) -> Settings:
    """加载配置设置"""
    config_file = config_path or DEFAULT_CONFIG_PATH
    config_data: dict = {}

    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as handle:
                file_data = yaml.safe_load(handle)
                if file_data:
                    config_data.update(file_data)
                    logger.info(f"Configuration loaded from {config_file}")
        except Exception as e:
            logger.error(f"Error loading config file {config_file}: {e}")
    else:
        logger.warning(f"Config file not found at {config_file}, using defaults")

    # 环境变量可以覆盖配置文件设置
    settings = Settings(**config_data)
    logger.info(f"Server will listen on {settings.host}:{settings.port}")

    return settings
