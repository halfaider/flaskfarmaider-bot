import os
import re
import sys
import json
import base64
import pathlib
import logging
import asyncio
import functools
from pathlib import Path
from urllib.parse import urljoin, urlencode
from typing import Any, Callable

import discord
import aiohttp
from discord.ext import commands
from Crypto import Random
from Crypto.Cipher import AES

from .servers import FFaiderBotAPI
from .models import AppSettings
from .helpers.parsers import filename_parse

logger = logging.getLogger(__name__)


class FlaskfarmaiderHelpCommand(commands.DefaultHelpCommand):

    def __init__(self, **options: Any) -> None:
        command_attrs: dict = options.get("command_attrs") or {}
        command_attrs.setdefault("name", "help")
        command_attrs.setdefault("aliases", ("helpme", "도움", "도움말", "h"))
        command_attrs.setdefault("help", "도움말 출력")
        command_attrs.setdefault("brief", "이 도움말 출력")
        command_attrs.setdefault(
            "cooldown",
            commands.CooldownMapping.from_cooldown(2.0, 3.0, commands.BucketType.user),
        )
        options["command_attrs"] = command_attrs
        options.setdefault("commands_heading", "명령어:")
        options.setdefault("default_argument_description", "")
        options.setdefault("show_parameter_descriptions", True)
        options.setdefault("arguments_heading", "추가 입력:")
        options.setdefault("no_category", "기타")
        options.setdefault("indent", 4)
        super(FlaskfarmaiderHelpCommand, self).__init__(**options)

    def get_ending_note(self) -> str:
        """override"""
        command_name = self.invoked_with
        return (
            f'"{self.context.clean_prefix}{command_name} (명령어)"를 입력해서 상세 정보를 확인하세요.\n'
            f'카테고리 상세 정보를 확인하려면 "{self.context.clean_prefix}{command_name} (카테고리)"를 입력하세요.\n'
            f"https://github.com/halfaider/flaskfarmaider-bot"
        )

    def get_command_signature(self, command: commands.Command[Any, ..., Any], /) -> str:
        """override"""
        parent = command.full_parent_name
        name = f"{parent} {command.name}" if parent else command.name
        prefix = self.context.clean_prefix
        usage = command.usage if command.usage else command.signature
        return f"{prefix}{name} {usage}"

    def add_command_arguments(
        self, command: commands.Command[Any, ..., Any], /
    ) -> None:
        """override"""
        arguments = command.clean_params.values()
        if not arguments:
            return

        self.paginator.add_line(self.arguments_heading)

        indent = " " * self.indent
        desc_indent = indent * 2

        for argument in arguments:
            name = argument.displayed_name or argument.name
            description = argument.description or self.default_argument_description
            self.paginator.add_line(f"{indent}{name}")
            desc_entry = f"{desc_indent}└ {description}"
            if argument.displayed_default is not None:
                desc_entry += f" (기본값: {argument.displayed_default})"
            self.paginator.add_line(self.shorten_text(desc_entry))


class FlaskfarmaiderBot(commands.Bot):
    """Flaskfarm 도우미 봇"""

    NO_POSTER = "https://dummyimage.com/200x300/000/fff.jpg&text=No+Image"
    OTT_PRIORITY_ROOTS = (
        Path("/ROOT/GDRIVE/VIDEO/방송중/OTT 애니메이션"),
        Path("/ROOT/GDRIVE/VIDEO/방송중/라프텔 애니메이션"),
        Path("/ROOT/GDRIVE/VIDEO/방송중/외국"),
    )
    GENRE_FROM_PATH_ROOTS = (
        Path("/ROOT/GDRIVE/VIDEO/방송중/외국"),
        Path("/ROOT/GDRIVE/VIDEO/방송중"),
        Path("/ROOT/GDRIVE/VIDEO/방송중(기타)"),
    )
    RECENT_MOVIE_ROOT = Path("/ROOT/GDRIVE/VIDEO/영화/최신")
    RECENT_FOREIGN_SERIES_ROOT = Path("/ROOT/GDRIVE/VIDEO/방송중/외국")
    MOVIE_ROOT = Path("/ROOT/GDRIVE/VIDEO/영화")
    PTN_TMDB_IDS = (re.compile(r"{tmdb-(\d+)}", re.IGNORECASE),)

    def __init__(
        self,
        command_prefix: str,
        settings: AppSettings,
        checks: list[Callable],
        **kwds: Any,
    ) -> None:
        super(FlaskfarmaiderBot, self).__init__(command_prefix, **kwds)
        self.settings = settings
        for check in checks:
            self.add_check(check)
        self.help_command = FlaskfarmaiderHelpCommand(command_attrs={"checks": checks})
        self.broadcast_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = dict()
        self.api_server = None

    async def setup_hook(self):
        """override"""
        if (
            "broadcast_worker" not in self.tasks
            or self.tasks["broadcast_worker"].done()
        ):
            task = asyncio.create_task(
                self._broadcast_worker(), name="broadcast_worker"
            )
            self.tasks["broadcast_worker"] = task
            logger.debug("Broadcast worker task created.")
        await super().setup_hook()

    async def on_ready(self) -> None:
        """override"""
        logger.info(f"Logged in as {self.user}")
        await self.add_cog(GDSBroadcastCog(self))
        await self.add_cog(DownloaderBroadcastCog(self))
        if not self.api_server:
            self.api_server = FFaiderBotAPI(self, self.settings.api)
            await self.api_server.start()

    async def on_close(self) -> None:
        """override"""
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        """override"""
        if (
            message.channel.id in self.settings.broadcast.source.channels
            and message.author.id in self.settings.broadcast.source.authors
            and message.content.startswith("```^")
            and message.content.endswith("```")
        ):
            await self._broadcast(message.content)
        else:
            await self.process_commands(message)

    async def on_error(self, event_method: str, *args: Any, **kwds: Any) -> None:
        """override"""
        exc_type, exc_value, exc_tb = sys.exc_info()
        match exc_type:
            case discord.errors.DiscordServerError:
                logger.error(exc_value)
                logger.debug("Retrying in 5 seconds...")
                for _ in range(3):
                    await asyncio.sleep(5)
                    try:
                        await getattr(self, event_method)(*args, **kwds)
                        return
                    except discord.errors.DiscordServerError as e:
                        logger.exception(repr(e))
                logger.error("Maximum retry count exceeded.")
            case _:
                await super().on_error(event_method, *args, **kwds)

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """override"""
        logger.warning(
            f'Error occurred by name="{ctx.author.name}" type="{type(error)}" error="{str(error)}"'
        )
        message = None
        match type(error):
            case (
                commands.errors.CheckFailure
                | commands.errors.CheckAnyFailure
                | commands.errors.CommandNotFound
            ):
                message = f"명령을 실행할 수 없습니다."
            case commands.errors.CommandOnCooldown:
                message = f"잠시 후에 시도해 주세요."
            case commands.errors.MissingRequiredArgument:
                message = f"추가 인자를 입력해 주세요."
            case commands.errors.BadArgument:
                message = f"잘못된 형식의 인자가 입력됐습니다."
            case _:
                await super().on_command_error(ctx, error)
                message = f"오류가 발생했습니다."
        check_channels = self.settings.discord.command.checks.channels
        if not check_channels or ctx.channel.id in check_channels:
            await ctx.send(f"{message}\n> {str(error)}")
            if isinstance(
                error,
                (commands.errors.MissingRequiredArgument, commands.errors.BadArgument),
            ):
                await ctx.send_help(ctx.command)

    async def _broadcast(self, content: str) -> None:
        for channel_id in self.settings.broadcast.target.channels:
            target_ch = self.get_channel(channel_id)
            if not target_ch:
                logger.warning(f"Channel {channel_id} not found.")
                continue
            if not isinstance(target_ch, discord.abc.Messageable):
                logger.warning(f"Channel {channel_id} is not messageable.")
                continue
            logger.debug(f"Broadcast to {channel_id}")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await target_ch.send(content)
                    break
                except discord.errors.DiscordServerError as e:
                    logger.error(
                        f"Failed to send the message ({attempt + 1} / {max_retries}): {e}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"Maximum retry count exceeded for {channel_id}.")
                except Exception:
                    logger.exception(
                        f"An unexpected error occurred while sendig to {channel_id}: {content=}"
                    )
                    break

    async def broadcast_gds(
        self, path: str, mode: str, file_count: int = 0, total_size: int = 0
    ) -> None:
        content = self.get_broadcast_gds_content(path, mode, file_count, total_size)
        logger.debug(f"Broadcast GDS: {mode=} {path=}")
        await self._broadcast(content)

    async def broadcast_downloader(
        self, path: str, item: str, file_count: int = 0, total_size: int = 0
    ) -> None:
        content = await self.get_broadcast_downloader_content(
            path, item, file_count=file_count, total_size=total_size
        )
        logger.debug(
            f"Broadcast Downloader: {item=} {file_count=} {total_size=} {path=}"
        )
        await self._broadcast(content)

    def get_broadcast_gds_content(
        self, path: str, mode: str, file_count: int = 0, total_size: int = 0
    ) -> str:
        data = {
            "t1": "gds_tool",
            "t2": "fp",
            "t3": "user",
            "data": {
                "gds_path": path,
                "scan_mode": mode,
                "count": file_count,
                "size": total_size,
            },
        }
        encrypted_data = self.encrypt(
            json.dumps(data), self.settings.broadcast.encrypt.key
        )
        return f"```^{encrypted_data}```"

    def _get_category_and_module(self, path: Path) -> tuple[str, str]:
        if path.is_relative_to(self.RECENT_FOREIGN_SERIES_ROOT):
            if path.stem.endswith(("-SW", "-ST")):
                return "ktv", "vod"
            return "ftv", "vod"
        if path.is_relative_to(self.MOVIE_ROOT):
            return "movie", "share_movie"
        return "ktv", "vod"

    async def _fetch_metadata(
        self, path: Path, category: str, file_title: str, year: int
    ) -> dict[str, Any]:
        logger.debug(f"{category=} {file_title=}")
        default_query = {
            "apikey": self.settings.flaskfarm.apikey,
            "call": "plex",
            "manual": "True",
        }
        path_str = str(path)
        tmdb_match = next(
            (match for ptn in self.PTN_TMDB_IDS if (match := ptn.search(path_str))),
            None,
        )
        if tmdb_match:
            code_prefix = "MT" if category == "movie" else "FT"
            query = default_query | {"code": f"{code_prefix}{tmdb_match.group(1)}"}
            api_path = f"/metadata/api/{'ftv' if category == 'ktv' else category}/info"
            url = urljoin(self.settings.flaskfarm.url, f"{api_path}?{urlencode(query)}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        return await response.json() or {}
            except Exception:
                logger.exception(f"Metadata fetch failed: {path=}")
                return {}
        else:
            # 검색 후 첫번째 결과의 info를 return
            query = default_query | {"keyword": file_title, "year": year}
            api_path = f"/metadata/api/{category}/search"
            url = urljoin(self.settings.flaskfarm.url, f"{api_path}?{urlencode(query)}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        search_result = await response.json()
                        no_search_result_msg = (
                            f"No search results: {file_title=} {search_result=}"
                        )
                        if not search_result:
                            logger.warning(no_search_result_msg)
                            return {}
                        if isinstance(search_result, list):
                            first_result = search_result[0]
                        # KTV 서치 목록
                        elif isinstance(search_result, dict):
                            has_wavve = bool(search_result.get("wavve"))
                            has_tving = bool(search_result.get("tving"))
                            first_site = None
                            if has_wavve or has_tving:
                                for root in self.OTT_PRIORITY_ROOTS:
                                    if path.is_relative_to(root):
                                        if path.stem.endswith("-SW") and has_wavve:
                                            first_site = "wavve"
                                        elif path.stem.endswith("-ST") and has_tving:
                                            first_site = "tving"
                                        else:
                                            first_site = (
                                                "wavve" if has_wavve else "tving"
                                            )
                                        break
                            if not first_site:
                                first_site = next(iter(search_result), None)
                            site = search_result[first_site] if first_site else {}
                            if not site:
                                logger.warning(no_search_result_msg)
                                return {}
                            # Daum은 dict, 나머지는 list
                            first_result = site[0] if isinstance(site, list) else site
                        else:
                            logger.warning(no_search_result_msg)
                            first_result = {}
                    if code := first_result.get("code"):
                        info_query = default_query | {"code": code}
                        info_api_path = f"/metadata/api/{category}/info"
                        info_url = urljoin(
                            self.settings.flaskfarm.url,
                            f"{info_api_path}?{urlencode(info_query)}",
                        )
                        async with session.get(info_url) as info_response:
                            return await info_response.json()
                    else:
                        logger.warning(f"No code: {file_title=} {first_result=}")
                        return {}
            except Exception:
                logger.exception(f"Metadata fetch failed: {path=}")
                return {}

    def _get_genre_from_path(self, path: Path) -> str | None:
        for root in self.GENRE_FROM_PATH_ROOTS:
            try:
                return path.relative_to(root).parts[0]
            except Exception:
                continue

    def _build_movie_data(
        self,
        metadata: dict,
        path: Path,
        item: str,
        module: str,
        file_title: str,
        file_count: int = 0,
        total_size: int = 0,
        parsed: dict = {},
    ) -> dict:
        metadata = metadata or {}
        countries = metadata.get("country") or []
        ca = "Unknown"
        if path.is_relative_to(self.RECENT_MOVIE_ROOT):
            ca = "최신"
        elif countries:
            for korea in ("한국", "대한민국", "Korea"):
                if korea in countries:
                    ca = "한국"
                    break
            else:
                ca = "외국"
        return {
            "t1": "bot_downloader",
            "t2": module,
            "data": {
                "ca": ca,
                "count": file_count,
                "folderid": item,
                "foldername": path.name,
                "meta": {
                    "code": metadata.get("code") or "Unknown",
                    # 검색 결과는 장르와 국가 정보가 없음
                    "country": countries,
                    "genre": metadata.get("genre") or [],
                    "originaltitle": metadata.get("originaltitle")
                    or metadata.get("title_original")
                    or "",
                    "poster": metadata.get("main_poster")
                    or metadata.get("image_url")
                    or self.NO_POSTER,
                    "title": metadata.get("title")
                    or metadata.get("title_en")
                    or "Unknown",
                    "year": metadata.get("year", 1900),
                },
                "size": total_size,
                "subject": file_title,
            },
        }

    def _build_vod_data(
        self,
        metadata: dict,
        path: Path,
        item: str,
        module: str,
        file_title: str,
        file_count: int = 0,
        total_size: int = 0,
        parsed: dict = {},
    ) -> dict:
        metadata = metadata or {}
        date_match = re.search(r"\d{6}", path.stem)
        genres = metadata.get("genre")
        if folder_genre := self._get_genre_from_path(path):
            genre = folder_genre
        elif isinstance(genres, list) and genres:
            genre = genres[0]
        else:
            genre = "Unknown"
        poster = None
        image_list = metadata.get("thumb") or metadata.get("art")
        if isinstance(image_list, list):
            sorted_image_list = sorted(
                image_list,
                key=lambda x: (x.get("aspect") == "poster", x.get("score") or 0),
                reverse=True,
            )
            if selected := next(iter(sorted_image_list), None):
                poster = selected.get("value") or selected.get("thumb")
        else:
            poster = metadata.get("main_poster") or metadata.get("image_url")
        if not poster:
            poster = self.NO_POSTER
        return {
            "t1": "bot_downloader",
            "t2": module,
            "data": {
                "f": path.name,
                "id": item,
                "meta": {
                    "code": metadata.get("code") or "Unknown",
                    "genre": genre,
                    "poster": poster,
                    "title": metadata.get("title") or "Unknown",
                },
                "s": total_size,
                "c": file_count,
                "vod": {
                    "date": date_match.group() if date_match else "",
                    "name": file_title,
                    "no": parsed.get("episode") or 0,
                    "quality": (parsed.get("resolution") or "").strip("p")
                    or parsed.get("quality")
                    or "",
                    "release": parsed.get("encoder") or "",
                },
            },
        }

    async def get_broadcast_downloader_content(
        self, path: str, item: str, file_count: int = 0, total_size: int = 0
    ) -> str:
        logger.debug(f"{path=} {item=} {file_count=} {total_size=}")
        full_path = Path(path)
        category, module = self._get_category_and_module(full_path)
        parsed_parts = filename_parse(full_path.name)
        logger.debug(f"{parsed_parts=}")
        file_title = parsed_parts.get("title") or full_path.stem
        year = parsed_parts.get("year") or 1900
        metadata = await self._fetch_metadata(full_path, category, file_title, year)
        if category == "movie":
            builder = self._build_movie_data
        else:
            builder = self._build_vod_data
        data = builder(
            metadata=metadata,
            path=full_path,
            item=item,
            module=module,
            file_title=file_title,
            file_count=file_count,
            total_size=total_size,
            parsed=parsed_parts,
        )
        encrypted_data = self.encrypt(
            json.dumps(data), self.settings.broadcast.encrypt.key
        )
        return f"```^{encrypted_data}```"

    async def _broadcast_worker(self) -> None:
        logger.debug("Broadcast worker started.")
        handlers = {"gds": self.broadcast_gds, "downloader": self.broadcast_downloader}
        try:
            while not self.is_closed():
                try:
                    handler, data = await self.broadcast_queue.get()
                    path = data.get("path")
                    extra = data.get("mode") or data.get("item")
                    file_count = data.get("file_count") or 1
                    total_size = data.get("total_size") or 0
                    try:
                        await handlers[handler](path, extra, file_count, total_size)
                    except Exception:
                        logger.exception(
                            f"Failed to broadcast: {handler=} {path=} {extra=}"
                        )
                    finally:
                        self.broadcast_queue.task_done()
                except asyncio.CancelledError:
                    logger.debug("Broadcast worker is being cancelled...")
                    raise
                except Exception as e:
                    logger.exception(e)
                    await asyncio.sleep(1)
        finally:
            logger.debug("Broadcast worker stopped.")

    def _pad(self, text: str) -> bytes:
        text_bytes = text.encode("utf-8")
        pad_len = AES.block_size - (len(text_bytes) % AES.block_size)
        padding = bytes([pad_len] * pad_len)
        return text_bytes + padding

    def _unpad(self, padded_data: bytes) -> bytes:
        pad_len = padded_data[-1]
        return padded_data[:-pad_len]

    def encrypt(self, content: str, key: str) -> str:
        content_bytes = self._pad(content)
        key_bytes = key.encode()
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        encrypted_bytes = cipher.encrypt(content_bytes)
        result = base64.b64encode(iv + encrypted_bytes)
        return result.decode()

    def decrypt(self, encoded: str, key: str) -> str:
        decoded_bytes = base64.b64decode(encoded)
        iv = decoded_bytes[: AES.block_size]
        if len(iv) != AES.block_size:
            iv = os.urandom(AES.block_size)
        encrypted_content = decoded_bytes[AES.block_size :]
        key_bytes = key.encode()
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        decrypted_bytes = cipher.decrypt(encrypted_content)
        unpadded_bytes = self._unpad(decrypted_bytes)
        return unpadded_bytes.decode()


class DownloaderBroadcastCog(commands.Cog, name="다운로더-방송"):
    """봇 다운로더로 방송 명령어"""

    def __init__(self, bot: FlaskfarmaiderBot) -> None:
        self.bot: FlaskfarmaiderBot = bot

    @commands.command(
        name="downloader",
        brief="콘텐츠를 봇 다운로더로 방송합니다.",
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    async def broadcast_downloader(
        self,
        ctx: commands.Context,
        target_str: str = commands.parameter(
            displayed_name="GDS 경로",
            description='"/ROOT/GDRIVE"로 시작, 공백이 있으면 따옴표로 묶으세요.',
        ),
        resource_id: str = commands.parameter(
            displayed_name="리소스 ID",
            description="파일/폴더의 구글 드라이브 ID",
        ),
        total_size: int = commands.parameter(
            default=0,
            displayed_name="총 용량",
            description="전체 파일의 byte 용량",
        ),
        file_count: int = commands.parameter(
            default=0,
            displayed_name="파일 개수",
            description="파일은 1, 폴더는 자식 파일의 총 개수",
        ),
    ) -> None:
        """콘텐츠를 봇 다운로더로 방송합니다."""
        logger.info(f"{target_str=} {resource_id=} {file_count=} {total_size=}")
        target_path = Path(target_str)
        if not target_path.is_relative_to("/ROOT/GDRIVE/"):
            await ctx.send(f"경로가 올바른지 확인해 주세요.```{str(target_path)}```")
            return
        if not re.match(r"^[a-zA-Z0-9-_]{19,50}$", resource_id):
            await ctx.send(
                f"리소스 ID가 올바른지 확인해 주세요.```{str(resource_id)}```"
            )
            return
        await self.bot.broadcast_queue.put(
            (
                "downloader",
                {
                    "path": str(target_path),
                    "item": resource_id,
                    "total_size": total_size,
                    "file_count": file_count,
                },
            )
        )
        await ctx.send(
            f"방송 대기열에 추가했습니다.```GDS 경로: {str(target_path)}\n리소스 ID: {resource_id}\n총 용량: {total_size}\n파일 개수: {file_count}```"
        )


class GDSBroadcastCog(commands.Cog, name="변경사항-방송"):
    """GDS 변경사항 방송 명령어"""

    PARAMETER_BROADCAST = commands.parameter(
        displayed_name="GDS 경로",
        description='"/ROOT/GDRIVE"로 시작. "|"로 구분. /ROOT/GDRIVE/target-01|/ROOT/GDRIVE/target-02|...|/ROOT/GDRIVE/target-N',
    )

    def __init__(self, bot: FlaskfarmaiderBot) -> None:
        self.bot: FlaskfarmaiderBot = bot

    def broadcast(*, mode: str = "ADD") -> Callable:
        def decorator(class_method: Callable) -> Callable:
            @functools.wraps(class_method)
            async def wrapper(
                self: "GDSBroadcastCog", ctx: commands.Context, *, target_str: str
            ) -> None:
                if not target_str:
                    await ctx.send("경로를 입력해 주세요.")
                    return
                targets = [
                    tar
                    for tar in (target.strip() for target in target_str.split("|"))
                    if tar
                ]
                if not targets:
                    await ctx.send("경로를 인식할 수 없습니다.")
                    return
                invalid_paths = list()
                valid_paths = list()
                for target in targets:
                    target_path = pathlib.Path(target)
                    if (
                        target_path.stem
                        and target_path.suffix.lower() in (".yaml", ".yml", ".json")
                        and mode == "ADD"
                    ):
                        invalid_paths.append(target)
                    elif target.startswith("/ROOT/GDRIVE/"):
                        logger.debug(f"author={ctx.author.name} {mode=} {target=}")
                        await self.bot.broadcast_queue.put(
                            ("gds", {"path": target, "mode": mode})
                        )
                        valid_paths.append(target)
                    else:
                        invalid_paths.append(target)
                if invalid_paths:
                    invalid_msg = "\n".join(invalid_paths)
                    await ctx.send(
                        f"경로 및 파일 형식을 확인해 주세요.```{invalid_msg}```"
                    )
                if valid_paths:
                    valid_msg = "\n".join(valid_paths)
                    await ctx.send(f"방송 대기열에 추가했습니다.```{valid_msg}```")

            return wrapper

        return decorator

    @commands.command(name="add", brief='"ADD" 모드로 GDS 변경사항을 방송합니다.')
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="ADD")
    async def broadcast_add(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "ADD" 모드로 GDS 변경사항을 방송합니다."""

    @commands.command(
        name="rm-file", brief='"REMOVE_FILE" 모드로 GDS 변경사항을 방송합니다.'
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="REMOVE_FILE")
    async def broadcast_remove_file(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "REMOVE_FILE" 모드로 GDS 변경사항을 방송합니다."""

    @commands.command(
        name="rm-folder", brief='"REMOVE_FOLDER" 모드로 GDS 변경사항을 방송합니다.'
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="REMOVE_FOLDER")
    async def broadcast_remove_folder(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "REMOVE_FOLDER" 모드로 GDS 변경사항을 방송합니다."""

    @commands.command(
        name="refresh", brief='"REFRESH" 모드로 GDS 변경사항을 방송합니다.'
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="REFRESH")
    async def broadcast_refresh(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "REFRESH" 모드로 GDS 변경사항을 방송합니다."""
