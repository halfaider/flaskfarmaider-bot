import logging
from typing import Any

from pydantic import BaseModel, Field

from .helpers.models import _BaseSettings

logger = logging.getLogger(__name__)


def get_default_logging_settings() -> dict:
    return {
        "level": "debug",
        "level_discord": "INFO",
        "format": "%(asctime)s,%(msecs)03d|%(levelname)-8s %(message)s ... %(filename)s:%(lineno)d",
        "date_format": "%Y-%m-%dT%H:%M:%S",
        "redacted_patterns": (
            "apikey=(.{10,36})",
            "['\"]apikey['\"]: ['\"](.{10,36})['\"]",
            "['\"]X-Plex-Token['\"]: ['\"](.{20})['\"]",
            "['\"]X-Plex-Token=(.{20})['\"]",
            "webhooks/(.+)/(.+):\\s{",
        ),
        "redacted_substitute": "<REDACTED>",
    }


class LoggingConfig(BaseModel):
    level: str
    level_discord: str
    format: str
    date_format: str
    redacted_patterns: tuple[str, ...]
    redacted_substitute: str


class DiscordChannelsConfig(BaseModel):
    channels: tuple[int, ...] = ()


class DiscordCommandConfig(BaseModel):
    checks: DiscordChannelsConfig
    prefix: str = "/"


class DiscordConfig(BaseModel):
    token: str = ""
    command: DiscordCommandConfig


class BroadcastSourceConfig(DiscordChannelsConfig):
    authors: tuple[int, ...] = ()


class BraodcastEncryptConfig(BaseModel):
    key: str = ""


class BroadcastConfig(BaseModel):
    source: BroadcastSourceConfig
    target: DiscordChannelsConfig
    encrypt: BraodcastEncryptConfig


class APIConfig(BaseModel):
    keys: tuple[str, ...] = ()
    port: int = 8080
    host: str = "0.0.0.0"


class AppSettings(_BaseSettings):
    """
    앱 실행시 사용하는 설정값 클래스
    """

    discord: DiscordConfig
    broadcast: BroadcastConfig
    api: APIConfig
    logging: LoggingConfig = Field(default_factory=get_default_logging_settings)

    def model_post_init(self, context: Any, /) -> None:
        """override"""
        super().model_post_init(context)
        # logger.warning(self.model_dump_json(indent=2))
