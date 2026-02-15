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
import PTN

from .servers import FFaiderBotAPI
from .models import AppSettings

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


class FlaskfarmaiderBot(commands.Bot):
    """Flaskfarm 도우미 봇"""

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
        self.byte_size = 16
        self.no_poster = "https://dummyimage.com/200x300/000/fff.jpg&text=No+Image"

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
            f'Error occurred by name="{ctx.author.name}" id={ctx.author.id} error="{str(error)}"'
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
            case _:
                await super().on_command_error(ctx, error)
                return
        check_channels = self.settings.discord.command.checks.channels
        if not check_channels or ctx.channel.id in check_channels:
            await ctx.send(f"{message} ```{str(error)}```")

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
        if path.is_relative_to("/ROOT/GDRIVE/VIDEO/방송중/외국"):
            return "ftv", "vod"
        if path.is_relative_to("/ROOT/GDRIVE/VIDEO/영화"):
            return "movie", "share_movie"
        return "ktv", "vod"

    def _get_file_title(self, path: Path, parsed: dict) -> str:
        match = re.split(r"\.[S\d]*E\d+", path.name)
        if match:
            return " ".join(t for t in re.split(r"[\.\s]+", match[0]) if t)
        return (parsed.get("title") or "").strip(".")

    async def _fetch_metadata(
        self, path: Path, category: str, file_title: str, year: int
    ) -> dict[str, Any]:
        logger.debug(f"{category=} {file_title=}")
        default_query = {
            "apikey": self.settings.flaskfarm.apikey,
            "call": "plex",
            "manual": "True",
        }
        if tmdb_match := re.search(r"{tmdb-(\d+)}", str(path)):
            code_prefix = "MT" if category == "movie" else "FT"
            query = default_query | {"code": f"{code_prefix}{tmdb_match.group(1)}"}
            api_path = f"/metadata/api/{'ftv' if category == 'ktv' else category}/info"
        else:
            query = default_query | {"keyword": file_title, "year": year}
            api_path = f"/metadata/api/{category}/search"
        url = urljoin(self.settings.flaskfarm.url, f"{api_path}?{urlencode(query)}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    # 영화 및 FTV 서치 목록
                    result = await response.json()
                    if isinstance(result, list):
                        return result[0] if result else {}
                    # KTV 서치 목록
                    if (
                        isinstance(result, dict)
                        and category == "ktv"
                        and not tmdb_match
                    ):
                        first_site = next(iter(result), None)
                        site = result[first_site] if first_site else {}
                        if isinstance(site, list):
                            return site[0] if site else {}
                        return site
                    # 영화, FTV Info
                    return result or {}
        except Exception:
            logger.exception(f"Metadata fetch failed: {path=}")
            return {}

    def _get_genre_from_path(self, path: Path) -> str:
        for root in (
            "/ROOT/GDRIVE/VIDEO/방송중/외국",
            "/ROOT/GDRIVE/VIDEO/방송중",
            "/ROOT/GDRIVE/VIDEO/방송중(기타)",
        ):
            try:
                return path.relative_to(root).parts[0]
            except Exception:
                continue
        return "Unknown"

    def _build_movie_data(
        self,
        metadata: dict,
        path: Path,
        item: str,
        module: str,
        file_title: str,
        file_count: int,
        total_size: int,
    ) -> dict:
        metadata = metadata or {}
        countries = metadata.get("country") or []
        ca = "Unknown"
        if path.is_relative_to("/ROOT/GDRIVE/VIDEO/영화/최신"):
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
                    "country": countries,
                    "genre": metadata.get("genre") or [self._get_genre_from_path(path)],
                    "originaltitle": metadata.get("originaltitle")
                    or metadata.get("title_original")
                    or "",
                    "poster": metadata.get("main_poster")
                    or metadata.get("image_url")
                    or self.no_poster,
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
        parsed: dict,
        file_count: int = 0,
        total_size: int = 0,
    ) -> dict:
        metadata = metadata or {}
        date_match = re.search(r"\d{6}", path.stem)
        genres = metadata.get("genre")
        if isinstance(genres, list) and genres:
            genre = genres[0]
        else:
            genre = genres or self._get_genre_from_path(path)
        return {
            "t1": "bot_downloader",
            "t2": module,
            "data": {
                "f": path.name,
                "id": item,
                "meta": {
                    "code": metadata.get("code") or "Unknown",
                    "genre": genre,
                    "poster": metadata.get("main_poster")
                    or metadata.get("image_url")
                    or self.no_poster,
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
        parsed_parts = PTN.parse(full_path.stem)
        logger.debug(f"{parsed_parts=}")
        file_title = self._get_file_title(full_path, parsed_parts)
        year = parsed_parts.get("year") or 1900
        metadata = await self._fetch_metadata(full_path, category, file_title, year)
        if category == "movie":
            data = self._build_movie_data(
                metadata,
                full_path,
                item,
                module,
                file_title,
                file_count,
                total_size,
            )
        else:
            data = self._build_vod_data(
                metadata,
                full_path,
                item,
                module,
                file_title,
                parsed_parts,
                file_count,
                total_size,
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
        pad_len = self.byte_size - (len(text_bytes) % self.byte_size)
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
        iv = decoded_bytes[: self.byte_size]
        if len(iv) != self.byte_size:
            iv = os.urandom(self.byte_size)
        encrypted_content = decoded_bytes[self.byte_size :]
        key_bytes = key.encode()
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        decrypted_bytes = cipher.decrypt(encrypted_content)
        unpadded_bytes = self._unpad(decrypted_bytes)
        return unpadded_bytes.decode()


class GDSBroadcastCog(commands.Cog, name="구드공-방송"):
    """GDS 변경사항 방송 명령어"""

    PARAMETER_BROADCAST = commands.parameter(
        default=None,
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
        pass

    @commands.command(
        name="rm-file", brief='"REMOVE_FILE" 모드로 GDS 변경사항을 방송합니다.'
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="REMOVE_FILE")
    async def broadcast_remove_file(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "REMOVE_FILE" 모드로 GDS 변경사항을 방송합니다."""
        pass

    @commands.command(
        name="rm-folder", brief='"REMOVE_FOLDER" 모드로 GDS 변경사항을 방송합니다.'
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="REMOVE_FOLDER")
    async def broadcast_remove_folder(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "REMOVE_FOLDER" 모드로 GDS 변경사항을 방송합니다."""
        pass

    @commands.command(
        name="refresh", brief='"REFRESH" 모드로 GDS 변경사항을 방송합니다.'
    )
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    @broadcast(mode="REFRESH")
    async def broadcast_refresh(
        self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST
    ) -> None:
        """ "REFRESH" 모드로 GDS 변경사항을 방송합니다."""
        pass
