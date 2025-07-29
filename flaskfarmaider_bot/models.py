import os
import logging
from pathlib import Path
from typing import Any, Literal, Union, Annotated, Sequence

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .helpers import deep_merge

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


def get_default_discord_settings() -> dict:
    return {
        "bots": {
            "flaskfarmaider": FlaskfarmaiderBotConfig(
                bot_type="flaskfarm",
                token="",
                command={"prefix": "!", "checks": {"channels": []}},
                broadcast={
                    "source": {"channels": [], "authors": []},
                    "target": {"channels": []},
                    "encrypt": {"key": ""},
                },
                flaskfarm={"url": "", "apikey": ""},
                api={"keys": [], "host": "0.0.0.0", "port": 8080},
            )
        }
    }


class LoggingConfig(BaseModel):
    level: str
    level_discord: str
    format: str
    date_format: str
    redacted_patterns: Sequence[str]
    redacted_substitute: str


class DiscordBotConfig(BaseModel):
    bot_type: str
    token: str
    command: dict = Field(
        default_factory=lambda: {
            "prefix": "!",
            "checks": {"channels": ()},
        }
    )


class FlaskfarmBotConfig(DiscordBotConfig):
    bot_type: Literal["flaskfarm"]
    flaskfarm: dict = Field(default_factory=lambda: {"url": "", "apikey": ""})


class FlaskfarmaiderBotConfig(FlaskfarmBotConfig):
    bot_type: Literal["flaskfarmaider"]
    broadcast: dict = Field(
        default_factory=lambda: {
            "source": {"channels": (), "authors": ()},
            "target": {"channels": ()},
            "encrypt": {"key": ""},
        }
    )
    api: dict = Field(
        default_factory=lambda: {"keys": (), "host": "0.0.0.0", "port": 8080}
    )


class TestBotConfig(DiscordBotConfig):
    bot_type: Literal["test"]
    test: dict = Field(default_factory=lambda: {"key": ""})


BotUnion = Annotated[
    Union[FlaskfarmBotConfig, FlaskfarmaiderBotConfig, TestBotConfig],
    Field(discriminator="bot_type"),
]


class DiscordConfig(BaseModel):
    bots: dict[str, BotUnion] = Field(
        default_factory=lambda: {
            "flaskfarmaider": FlaskfarmaiderBotConfig(bot_type="flaskfarm", token="")
        }
    )


class MergedYamlSettingsSource(YamlConfigSettingsSource):
    """
    사용자 yaml 설정값을 기본값과 병합하는 클래스
    """

    def __call__(self) -> dict[str, Any]:
        """override"""
        user_config = super().__call__()
        default_config = {}
        for field_name, field in self.settings_cls.model_fields.items():
            if field.default_factory:
                default_config[field_name] = field.default_factory()
        if not user_config:
            return default_config
        return deep_merge(default_config, user_config)

    def _read_files(self, files: str | os.PathLike | None) -> dict[str, Any]:
        """override"""
        if files is None:
            return {}
        if isinstance(files, (str, os.PathLike)):
            files = [files]
        vars: dict[str, Any] = {}
        for file in files:
            file_path = Path(file).expanduser()
            if file_path.is_file():
                vars.update(self._read_file(file_path))
                logger.warning(f"'{file_path.resolve()}' 파일을 불러왔습니다.")
                # 존재하는 첫번째 파일만 로딩
                break
        return vars


class _BaseSettings(BaseSettings):
    """
    사용자의 설정값을 저장하는 클래스
    """

    model_config = None

    def __init__(
        self, *args: Any, user_yaml_file: str | os.PathLike | None = None, **kwds: Any
    ) -> None:
        if user_yaml_file:
            self.model_config["yaml_file"] = user_yaml_file
        super().__init__(*args, **kwds)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """override"""
        merged_yaml_settings = MergedYamlSettingsSource(settings_cls)
        # 설정값 적용 순서
        return (
            init_settings,
            merged_yaml_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


class AppSettings(_BaseSettings):
    """
    앱 실행시 사용하는 설정값 클래스
    """

    discord: DiscordConfig
    logging: LoggingConfig = Field(default_factory=get_default_logging_settings)

    model_config = SettingsConfigDict(
        yaml_file=(
            Path(__file__).with_name("settings.yaml"),
            Path.cwd() / "settings.yaml",
            Path(__file__).with_name("config.yaml"),
            Path.cwd() / "config.yaml",
        ),
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    def model_post_init(self, context: Any, /) -> None:
        """override"""
        super().model_post_init(context)
        # logger.warning(self.model_dump_json(indent=2))
