import sys
import logging
import asyncio
from typing import Any, Callable

import discord
import aiohttp
from discord.ext import commands

from .servers import FFaiderBotAPI
from .models import AppSettings
from .help import FlaskfarmaiderHelpCommand
from .broadcast import BroadcastService
from .cogs import AdminCog, GDSBroadcastCog, DownloaderBroadcastCog
from .helpers.helpers import get_int

logger = logging.getLogger(__name__)


class FlaskfarmaiderBot(commands.Bot):
    """Flaskfarm 도우미 봇"""

    def __init__(
        self,
        command_prefix: str,
        settings: AppSettings,
        checks: tuple[Callable, ...] | None = None,
        **kwds: Any,
    ) -> None:
        super(FlaskfarmaiderBot, self).__init__(command_prefix, **kwds)
        self.settings = settings
        checks = checks or ()
        for check in checks:
            self.add_check(check)
        self.help_command = FlaskfarmaiderHelpCommand(command_attrs={"checks": checks})
        self.broadcast_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = dict()
        self.api_server = None
        self.session: aiohttp.ClientSession | None = None
        self.broadcast_service: BroadcastService | None = None

    async def setup_hook(self):
        """override"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        self.broadcast_service = BroadcastService(self.session, self.settings)
        if (
            "broadcast_worker" not in self.tasks
            or self.tasks["broadcast_worker"].done()
        ):
            task = asyncio.create_task(
                self._broadcast_worker(), name="broadcast_worker"
            )
            self.tasks["broadcast_worker"] = task
            logger.debug("Broadcast worker task created.")
        await self.add_cog(GDSBroadcastCog(self))
        await self.add_cog(DownloaderBroadcastCog(self))
        await self.add_cog(AdminCog(self))
        if not self.api_server:
            self.api_server = FFaiderBotAPI(self, self.settings.api)
            await self.api_server.start()
        await super().setup_hook()

    async def on_ready(self) -> None:
        """override"""
        logger.info(f"Logged in as {self.user}")

    async def close(self) -> None:
        """override"""
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self.session:
            await self.session.close()
        if self.api_server:
            await self.api_server.stop()
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
        elif message.channel.id in self.settings.broadcast.relay:
            for target in self.settings.broadcast.relay[message.channel.id]:
                if target.compiled_pattern and target.compiled_pattern.match(message.content):
                    await self._relay(message.content, target.to)
        else:
            await self.process_commands(message)

    async def on_error(self, event_method: str, *args: Any, **kwds: Any) -> None:
        """override"""
        exc_type, exc_value, exc_tb = sys.exc_info()
        if isinstance(exc_value, discord.DiscordServerError):
            logger.error(exc_value)
            logger.debug("Retrying in 5 seconds...")
            for _ in range(3):
                await asyncio.sleep(5)
                try:
                    event = getattr(self, event_method, None)
                    if event and callable(event):
                        await event(*args, **kwds)
                    return
                except discord.DiscordServerError as e:
                    logger.exception(repr(e))
                except Exception:
                    logger.exception(f"Unexpected error in {event_method}")
                    break
            logger.error("Maximum retry count exceeded.")
        else:
            await super().on_error(event_method, *args, **kwds)

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """override"""
        logger.warning(
            f'Error occurred by name="{ctx.author.name}" type="{type(error)}" error="{str(error)}"'
        )
        if isinstance(
            error,
            (
                commands.errors.CheckFailure,
                commands.errors.CheckAnyFailure,
                commands.errors.CommandNotFound,
            ),
        ):
            return
        if isinstance(error, commands.CommandOnCooldown):
            message = "잠시 후에 시도해 주세요."
        elif isinstance(error, commands.MissingRequiredArgument):
            message = "추가 인자를 입력해 주세요."
        elif isinstance(error, commands.BadArgument):
            message = "잘못된 형식의 인자가 입력됐습니다."
        else:
            await super().on_command_error(ctx, error)
            message = "오류가 발생했습니다."
        await ctx.reply(f"{message}\n> {str(error)}")
        if isinstance(
            error,
            (commands.errors.MissingRequiredArgument, commands.errors.BadArgument),
        ):
            await ctx.send_help(ctx.command)

    async def _send_to_channel(self, content: str, channel_id: int) -> bool:
        target_ch = self.get_channel(channel_id)
        if not target_ch:
            logger.warning(f"Channel {channel_id} not found.")
            return False
        if not isinstance(target_ch, discord.abc.Messageable):
            logger.warning(f"Channel {channel_id} is not messageable.")
            return False
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await target_ch.send(content)
                return True
            except discord.errors.DiscordServerError as e:
                logger.error(
                    f"Failed to send message to {channel_id} ({attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                else:
                    logger.error(f"Maximum retry count exceeded for {channel_id}.")
            except Exception:
                logger.exception(
                    f"An unexpected error occurred while sending to {channel_id}: {content=}"
                )
                return False
        return False

    async def _relay(self, content: str, channel_id: int) -> None:
        logger.debug(f"Relay to {channel_id}")
        await self._send_to_channel(content, channel_id)

    async def _broadcast(self, content: str) -> None:
        for channel_id in self.settings.broadcast.target.channels:
            logger.debug(f"Broadcast to {channel_id}")
            await self._send_to_channel(content, channel_id)

    async def broadcast_gds(
        self, path: str, mode: str, file_count: int = 0, total_size: int = 0
    ) -> None:
        content = self.broadcast_service.get_gds_content(path, mode, file_count, total_size)
        logger.debug(f"Broadcast GDS: {mode=} {path=}")
        await self._broadcast(content)

    async def broadcast_downloader(
        self, path: str, item: str, file_count: int = 0, total_size: int = 0
    ) -> None:
        content = await self.broadcast_service.get_downloader_content(
            path, item, file_count=file_count, total_size=total_size
        )
        logger.debug(
            f"Broadcast Downloader: {item=} {file_count=} {total_size=} {path=}"
        )
        await self._broadcast(content)

    async def _broadcast_worker(self) -> None:
        logger.debug("Broadcast worker started.")
        handlers = {"gds": self.broadcast_gds, "downloader": self.broadcast_downloader}
        try:
            while not self.is_closed():
                try:
                    handler, data = await self.broadcast_queue.get()
                    path = data.get("path")
                    extra = data.get("mode") or data.get("item")
                    file_count = get_int(data.get("file_count"), default=1)
                    total_size = get_int(data.get("total_size"), default=0)
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
