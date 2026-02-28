import logging
from typing import Any

from pydantic import BaseModel

from .helpers.models import _BaseSettings

logger = logging.getLogger(__name__)


class LoggingConfig(BaseModel):
    level: str = "debug"
    level_discord: str = "info"
    format: str = "%(asctime)s %(levelname)-8s %(message)s ... %(filename)s:%(lineno)d"
    date_format: str = "%Y-%m-%dT%H:%M:%S"
    redacted_patterns: tuple[str, ...] = (
        r"['\"]?(?:apikey|X-Plex-Token|token)['\"]?\s*[:=]\s*['\"]?([^'\"&\s,{}]+)['\"]?",
        r"webhooks/([^/\s]+)/([^/\s]+)",
    )
    redacted_substitute: str = "<REDACTED>"


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


class FlaskfarmServer(BaseModel):
    url: str = "http://localhost:9999"
    apikey: str = ""


class AppSettings(_BaseSettings):
    """
    앱 실행시 사용하는 설정값 클래스
    """

    discord: DiscordConfig
    broadcast: BroadcastConfig
    api: APIConfig
    flaskfarm: FlaskfarmServer
    logging: LoggingConfig = LoggingConfig()

    def model_post_init(self, context: Any, /) -> None:
        """override"""
        super().model_post_init(context)
        # logger.warning(self.model_dump_json(indent=2))
