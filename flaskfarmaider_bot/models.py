import re
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

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


class DiscordAutoRolesConfig(BaseModel):
    roles: tuple[int, ...] = ()
    pattern: str = ""
    channel: int = 0

    _compiled_pattern: re.Pattern | None = PrivateAttr(default=None)

    @property
    def compiled_pattern(self) -> re.Pattern | None:
        return self._compiled_pattern

    def model_post_init(self, context: Any, /) -> None:
        if self.pattern:
            self._compiled_pattern = re.compile(self.pattern, re.IGNORECASE)


class DiscordCommandConfig(BaseModel):
    broadcast: DiscordChannelsConfig = Field(default_factory=DiscordChannelsConfig)
    admin: DiscordChannelsConfig = Field(default_factory=DiscordChannelsConfig)
    prefix: str = "!"


class DiscordConfig(BaseModel):
    token: str = ""
    command: DiscordCommandConfig = Field(default_factory=DiscordCommandConfig)
    auto_roles: dict[int, DiscordAutoRolesConfig] = Field(default_factory=dict)


class BroadcastSourceConfig(DiscordChannelsConfig):
    authors: tuple[int, ...] = ()


class BroadcastEncryptConfig(BaseModel):
    key: str = ""


class BroadcastRelayTargetConfig(BaseModel):
    to: int
    pattern: str = ""

    _compiled_pattern: re.Pattern | None = PrivateAttr(default=None)

    @property
    def compiled_pattern(self) -> re.Pattern | None:
        return self._compiled_pattern

    def model_post_init(self, context: Any, /) -> None:
        if self.pattern:
            self._compiled_pattern = re.compile(self.pattern, re.IGNORECASE)


class ModuleRuleConfig(BaseModel):
    metadata: str
    bot_downloader: str
    patterns: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()

    _compiled_patterns: tuple[re.Pattern, ...] = PrivateAttr(default_factory=tuple)
    _path_roots: tuple[Path, ...] = PrivateAttr(default_factory=tuple)

    @property
    def compiled_patterns(self) -> tuple[re.Pattern, ...]:
        return self._compiled_patterns

    @property
    def path_roots(self) -> tuple[Path, ...]:
        return self._path_roots

    def model_post_init(self, context: Any, /) -> None:
        self._compiled_patterns = tuple(
            re.compile(p, re.IGNORECASE) for p in self.patterns
        )
        self._path_roots = tuple(Path(r) for r in self.roots)

    def is_match(self, full_path: str) -> bool:
        return any(p.search(full_path) for p in self._compiled_patterns)

    def is_relative(self, full_path: Path) -> bool:
        return any(full_path.is_relative_to(root) for root in self._path_roots)


class ImageConfig(BaseModel):
    no_poster: str = ""


class TmdbConfig(BaseModel):
    id_patterns: tuple[str, ...] = ()

    _compiled_id_patterns: tuple[re.Pattern, ...] = PrivateAttr(default_factory=tuple)

    @property
    def compiled_id_patterns(self) -> tuple[re.Pattern, ...]:
        return self._compiled_id_patterns

    def model_post_init(self, context: Any, /) -> None:
        self._compiled_id_patterns = tuple(
            re.compile(p, re.IGNORECASE) for p in self.id_patterns
        )

    def get_tmdb_id(self, full_path: str) -> str | None:
        for ptn in self._compiled_id_patterns:
            if match := ptn.search(full_path):
                try:
                    return match.group("id")
                except IndexError:
                    return match.group(1) if match.groups() else None


class BroadcastConfig(BaseModel):
    source: BroadcastSourceConfig
    target: DiscordChannelsConfig
    encrypt: BroadcastEncryptConfig
    relay: dict[int, tuple[BroadcastRelayTargetConfig, ...]] = Field(default_factory=dict)

    module_rules: tuple[ModuleRuleConfig, ...] = ()
    genre_by_subfolders: tuple[str, ...] = ()
    ott_metadata_roots: tuple[str, ...] = ()
    title_patterns: tuple[str, ...] = ()

    _path_genre_by_subfolders: tuple[Path, ...] = PrivateAttr(default_factory=tuple)
    _path_ott_metadata_roots: tuple[Path, ...] = PrivateAttr(default_factory=tuple)
    _compiled_title_patterns: tuple[re.Pattern, ...] = PrivateAttr(
        default_factory=tuple
    )

    @property
    def path_genre_by_subfolders(self) -> tuple[Path, ...]:
        return self._path_genre_by_subfolders

    @property
    def path_ott_metadata_roots(self) -> tuple[Path, ...]:
        return self._path_ott_metadata_roots

    @property
    def compiled_title_patterns(self) -> tuple[re.Pattern, ...]:
        return self._compiled_title_patterns

    def model_post_init(self, context: Any, /) -> None:
        self._path_ott_metadata_roots = tuple(Path(r) for r in self.ott_metadata_roots)
        self._path_genre_by_subfolders = tuple(
            Path(r) for r in self.genre_by_subfolders
        )
        self._compiled_title_patterns = tuple(
            re.compile(p, re.IGNORECASE) for p in self.title_patterns
        )

    def is_relative_ott(self, full_path: Path) -> bool:
        return any(
            full_path.is_relative_to(root) for root in self._path_ott_metadata_roots
        )

    def is_relative_genre(self, full_path: Path) -> bool:
        return any(
            full_path.is_relative_to(root) for root in self._path_genre_by_subfolders
        )

    def get_genre_from_subfolder(self, full_path: Path) -> str | None:
        for root in self._path_genre_by_subfolders:
            try:
                return full_path.relative_to(root).parts[0]
            except Exception:
                pass

    def get_search_keywords(self, filename: str) -> list[str]:
        keywords = [filename]
        for ptn in self._compiled_title_patterns:
            if match := ptn.search(filename):
                try:
                    val = match.group("title")
                except IndexError:
                    val = match.group(1) if match.groups() else None
                if val and (val := val.strip()):
                    if val not in keywords:
                        keywords.append(val)
        return list(dict.fromkeys(keywords))


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
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    images: ImageConfig = Field(default_factory=ImageConfig)
    tmdb: TmdbConfig = Field(default_factory=TmdbConfig)

    def model_post_init(self, context: Any, /) -> None:
        """override"""
        super().model_post_init(context)
        # logger.warning(self.model_dump_json(indent=2))
