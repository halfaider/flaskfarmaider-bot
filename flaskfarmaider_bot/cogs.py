from __future__ import annotations

import re
import logging
import functools
from pathlib import Path
from typing import Callable, TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from .bot import FlaskfarmaiderBot

logger = logging.getLogger(__name__)


class DownloaderBroadcastCog(commands.Cog, name="다운로더-방송"):
    """봇 다운로더로 방송 명령어"""

    def __init__(self, bot: "FlaskfarmaiderBot") -> None:
        self.bot: "FlaskfarmaiderBot" = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """다운로더 방송 명령어 실행 채널 검증"""
        allowed = self.bot.settings.discord.command.broadcast.channels
        if not allowed or ctx.channel.id in allowed:
            return True
        return False

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
            await ctx.reply(f"경로가 올바른지 확인해 주세요.```{str(target_path)}```")
            return
        if not re.match(r"^[a-zA-Z0-9-_]{19,50}$", resource_id):
            await ctx.reply(
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
        await ctx.reply(
            f"방송 대기열에 추가했습니다.```GDS 경로: {str(target_path)}\n리소스 ID: {resource_id}\n총 용량: {total_size}\n파일 개수: {file_count}```"
        )


class GDSBroadcastCog(commands.Cog, name="변경사항-방송"):
    """GDS 변경사항 방송 명령어"""

    PARAMETER_BROADCAST = commands.parameter(
        displayed_name="GDS 경로",
        description='"/ROOT/GDRIVE"로 시작. "|"로 구분. /ROOT/GDRIVE/target-01|/ROOT/GDRIVE/target-02|...|/ROOT/GDRIVE/target-N',
    )

    def __init__(self, bot: "FlaskfarmaiderBot") -> None:
        self.bot: "FlaskfarmaiderBot" = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """GDS 방송 명령어 실행 채널 검증"""
        allowed = self.bot.settings.discord.command.broadcast.channels
        if not allowed or ctx.channel.id in allowed:
            return True
        return False

    def broadcast(*, mode: str = "ADD") -> Callable:
        def decorator(class_method: Callable) -> Callable:
            @functools.wraps(class_method)
            async def wrapper(
                self: "GDSBroadcastCog", ctx: commands.Context, *, target_str: str
            ) -> None:
                if not target_str:
                    await ctx.reply("경로를 입력해 주세요.")
                    return
                targets = [
                    tar
                    for tar in (target.strip() for target in target_str.split("|"))
                    if tar
                ]
                if not targets:
                    await ctx.reply("경로를 인식할 수 없습니다.")
                    return
                invalid_paths = list()
                valid_paths = list()
                for target in targets:
                    target_path = Path(target)
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
                    await ctx.reply(
                        f"경로 및 파일 형식을 확인해 주세요.```{invalid_msg}```"
                    )
                if valid_paths:
                    valid_msg = "\n".join(valid_paths)
                    await ctx.reply(f"방송 대기열에 추가했습니다.```{valid_msg}```")

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


class AdminCog(commands.Cog, name="관리"):
    """서버 관리 명령어"""

    def __init__(self, bot: "FlaskfarmaiderBot") -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """AdminCog 명령어 실행 채널 검증"""
        allowed = self.bot.settings.discord.command.admin.channels
        if not allowed or ctx.channel.id in allowed:
            return True
        return False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not member.bot:
            return
        config = self.bot.settings.discord.auto_roles.get(member.guild.id)
        if not config or not config.roles:
            return
        if config.compiled_pattern and not config.compiled_pattern.match(member.name):
            logger.debug(
                f"`{member.name}` does not match auto_roles pattern `{config.pattern}` in {member.guild.name}."
            )
            return
        guild = member.guild
        assigned_roles: list[discord.Role] = []
        for role_id in config.roles:
            role = guild.get_role(role_id)
            if not role:
                logger.warning(f"Role {role_id} not found in {guild.name}.")
                continue
            if role >= guild.me.top_role:
                logger.warning(f"Role `{role.name}` is higher than bot's top role in {guild.name}.")
                continue
            if role in member.roles:
                logger.debug(f"`{member.display_name}` already has `{role.name}`.")
                continue
            try:
                await member.add_roles(role, reason="Auto-promote on join")
                assigned_roles.append(role)
                logger.info(f"Auto-promoted `{member.display_name}` with `{role.name}` in {guild.name}.")
            except Exception:
                logger.exception(f"Failed to auto-promote `{member.display_name}` with role {role_id}.")

        if assigned_roles and config.channel:
            roles_str = ", ".join(f"`{r.name}`" for r in assigned_roles)
            message = f"`{member.display_name}` ({member.id})에게 {roles_str} 역할을 부여했습니다."
            await self.bot._send_to_channel(message, config.channel)

    def _find_bot_member(
        self, guild: discord.Guild, query: str
    ) -> discord.Member | None:
        """ID, 멘션, 봇 이름(username), 별명(nickname)으로 봇(앱) 멤버를 검색합니다."""
        cleaned = query.strip("<@!>")
        if cleaned.isdigit():
            if (member := guild.get_member(int(cleaned))) and member.bot:
                return member

        query_lower = query.lower()
        # 1. 정확한 이름 또는 별명 일치 (봇만 대상, 대소문자 무시)
        for member in guild.members:
            if not member.bot:
                continue
            if (
                member.name.lower() == query_lower
                or member.display_name.lower() == query_lower
            ):
                return member

        # 2. 이름 또는 별명 접두사 일치 (봇만 대상)
        for member in guild.members:
            if not member.bot:
                continue
            if member.name.lower().startswith(
                query_lower
            ) or member.display_name.lower().startswith(query_lower):
                return member

        return None

    @commands.command(name="roles", aliases=["app-roles", "bot-roles"], brief="대상 앱(봇)의 현재 역할 목록을 조회합니다.")
    @commands.cooldown(2, 3.0, commands.BucketType.user)
    async def show_roles(
        self,
        ctx: commands.Context,
        target: str = commands.parameter(
            displayed_name="앱 ID 또는 이름",
            description="역할을 조회할 대상 앱(봇)의 ID, 이름 또는 멘션",
        ),
    ) -> None:
        """대상 앱(봇)의 현재 역할 목록을 조회합니다."""
        guild = ctx.guild
        if not guild:
            await ctx.reply("서버에서만 사용할 수 있습니다.")
            return

        member = self._find_bot_member(guild, target)
        if not member:
            await ctx.reply(f"`{target}`에 해당하는 봇(앱)을 찾을 수 없습니다.")
            return

        # @everyone 역할(guild.default_role)을 제외한 역할 목록 (상위 역할순 정렬)
        roles = [r for r in reversed(member.roles) if r != guild.default_role]

        if not roles:
            await ctx.reply(
                f"`{member.display_name}` ({member.id}) 에게 부여된 추가 역할이 없습니다. (`@everyone`만 보유)"
            )
            return

        role_lines = [f"- {r.name} (ID: {r.id})" for r in roles]
        roles_text = "\n".join(role_lines)

        await ctx.reply(
            f"**`{member.display_name}` ({member.id})** 역할 목록 ({len(roles)}개):\n"
            f"```{roles_text}```"
        )
