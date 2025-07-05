import sys
import json
import pathlib
import logging
import asyncio
import functools
from typing import Awaitable, Any, Callable

import discord
from discord.ext import commands

from servers import FFaiderBotAPI
from helpers import encrypt

logger = logging.getLogger(__name__)


class FlaskfarmaiderHelpCommand(commands.DefaultHelpCommand):

    def __init__(self, **options: dict[str, Any]) -> None:
        command_attrs: dict = options.get('command_attrs') or {}
        command_attrs.setdefault('name', 'help')
        command_attrs.setdefault('aliases', ('helpme', '도움', '도움말', 'h'))
        command_attrs.setdefault('help', '도움말 출력')
        command_attrs.setdefault('brief', '이 도움말 출력')
        command_attrs.setdefault('cooldown', commands.CooldownMapping.from_cooldown(2.0, 3.0, commands.BucketType.user))
        options['command_attrs'] = command_attrs
        options.setdefault('commands_heading', '명령어:')
        options.setdefault('default_argument_description', '')
        options.setdefault('show_parameter_descriptions', True)
        options.setdefault('arguments_heading', '추가 입력:')
        options.setdefault('no_category', '기타')
        options.setdefault('indent', 4)
        super(FlaskfarmaiderHelpCommand, self).__init__(**options)

    def get_ending_note(self) -> str:
        '''override'''
        command_name = self.invoked_with
        return (
            f'"{self.context.clean_prefix}{command_name} (명령어)"를 입력해서 상세 정보를 확인하세요.\n'
            f'카테고리 상세 정보를 확인하려면 "{self.context.clean_prefix}{command_name} (카테고리)"를 입력하세요.'
        )


class GDSBroadcastCog(commands.Cog, name='구드공-방송'):
    '''GDS 변경사항 방송 명령어'''

    PARAMETER_BROADCAST = commands.parameter(
        default=None,
        displayed_name='GDS 경로',
        description='"/ROOT/GDRIVE"로 시작. "|"로 구분. /ROOT/GDRIVE/target-01|/ROOT/GDRIVE/target-02|...|/ROOT/GDRIVE/target-N'
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot: FlaskfarmaiderBot = bot

    def broadcast(*, mode: str = 'ADD') -> Awaitable:
        def decorator(class_method: Awaitable) -> Awaitable:
            @functools.wraps(class_method)
            async def wrapper(self: 'GDSBroadcastCog', ctx: commands.Context, *, target_str: str) -> None:
                if not target_str:
                    await ctx.send('경로를 입력해 주세요.')
                    return
                targets = (target.strip() for target in target_str.split('|') if target)
                invalid_paths = list()
                valid_paths = list()
                for target in targets:
                    target_path = pathlib.Path(target)
                    if target_path.stem and target_path.suffix.lower() in ('.yaml', '.yml', '.json') and mode == 'ADD':
                        invalid_paths.append(target)
                    elif target.startswith('/ROOT/GDRIVE/'):
                        logger.debug(f'author={ctx.author.name} {mode=} {target=}')
                        await self.bot.broadcast_queue.put((target, mode))
                        valid_paths.append(target)
                    else:
                        invalid_paths.append(target)
                if invalid_paths:
                    invalid_msg = '\n'.join(invalid_paths)
                    await ctx.send(f'경로 및 파일 형식을 확인해 주세요.```{invalid_msg}```')
                if valid_paths:
                    valid_msg = '\n'.join(valid_paths)
                    await ctx.send(f'방송 대기열에 추가했습니다.```{valid_msg}```')
            return wrapper
        return decorator

    @commands.command(name='add', brief='"ADD" 모드로 GDS 변경사항을 방송합니다.')
    @commands.cooldown(2.0, 3.0, commands.BucketType.user)
    @broadcast(mode='ADD')
    async def broadcast_add(self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST) -> None:
        '''"ADD" 모드로 GDS 변경사항을 방송합니다.'''
        pass

    @commands.command(name='rm-file', brief='"REMOVE_FILE" 모드로 GDS 변경사항을 방송합니다.')
    @commands.cooldown(2.0, 3.0, commands.BucketType.user)
    @broadcast(mode='REMOVE_FILE')
    async def broadcast_remove_file(self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST) -> None:
        '''"REMOVE_FILE" 모드로 GDS 변경사항을 방송합니다.'''
        pass

    @commands.command(name='rm-folder', brief='"REMOVE_FOLDER" 모드로 GDS 변경사항을 방송합니다.')
    @commands.cooldown(2.0, 3.0, commands.BucketType.user)
    @broadcast(mode='REMOVE_FOLDER')
    async def broadcast_remove_folder(self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST) -> None:
        '''"REMOVE_FOLDER" 모드로 GDS 변경사항을 방송합니다.'''
        pass

    @commands.command(name='refresh', brief='"REFRESH" 모드로 GDS 변경사항을 방송합니다.')
    @commands.cooldown(2.0, 3.0, commands.BucketType.user)
    @broadcast(mode='REFRESH')
    async def broadcast_refresh(self, ctx: commands.Context, *, target_str: str = PARAMETER_BROADCAST) -> None:
        '''"REFRESH" 모드로 GDS 변경사항을 방송합니다.'''
        pass


class FlaskfarmaiderBot(commands.Bot):
    '''Flaskfarm 도우미 봇'''

    def __init__(self, command_prefix: str, config: dict, checks: list[Callable], **kwds: Any) -> None:
        super(FlaskfarmaiderBot, self).__init__(command_prefix, **kwds)
        self.config = config
        for check in checks:
            self.add_check(check)
        self.help_command = FlaskfarmaiderHelpCommand(command_attrs={'checks': checks})
        self.broadcast_queue = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = dict()
        self.api_server = None

    async def setup_hook(self):
        '''override'''
        if 'request_broadcast' not in self.tasks:
            task = asyncio.create_task(self.request_broadcast(), name='request_broadcast')
            self.tasks['request_broadcast'] = task
        await super().setup_hook()

    async def on_ready(self) -> None:
        '''override'''
        logger.info(f'Logged in as {self.user}')
        await self.add_cog(GDSBroadcastCog(self))
        config = self.config['discord']['bots']['flaskfarmaider'].get('api')
        if not self.api_server:
            self.api_server = FFaiderBotAPI(self, config)
            await self.api_server.start()

    async def on_close(self) -> None:
        '''override'''
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        '''override'''
        if (message.channel.id in self.config['discord']['bots']['flaskfarmaider']['broadcast']['source']['channels']
            and message.author.id in self.config['discord']['bots']['flaskfarmaider']['broadcast']['source']['authors']
            and message.content.startswith('```^')
            and message.content.endswith('```')):
            await self._broadcast(message.content)
        else:
            await self.process_commands(message)

    async def on_error(self, event_method: str, *args: Any, **kwds: Any) -> None:
        '''override'''
        exc_type, exc_value, exc_tb = sys.exc_info()
        match exc_type:
            case discord.errors.DiscordServerError:
                logger.error(exc_value)
                logger.debug('Retrying in 5 seconds...')
                for _ in range(3):
                    await asyncio.sleep(5)
                    try:
                        await getattr(self, event_method)(*args, **kwds)
                        return
                    except discord.errors.DiscordServerError as e:
                        logger.exception(repr(e))
                logger.error('Maximum retry count exceeded.')
            case _:
                await super().on_error(event_method, *args, **kwds)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        '''override'''
        logger.warning(f'Error occurred by name="{ctx.author.name}" id={ctx.author.id} error="{str(error)}"')
        check_channel = ctx.channel.id in self.config['discord']['bots']['flaskfarmaider']['command']['checks']['channels']
        match type(error):
            case (commands.errors.CheckFailure
                | commands.errors.CheckAnyFailure
                | commands.errors.CommandNotFound):
                if check_channel:
                    await ctx.send(f'명령을 실행할 수 없습니다. ```{str(error)}```')
            case commands.errors.CommandOnCooldown:
                if check_channel:
                    await ctx.send(f'잠시 후에 시도해 주세요. ```{str(error)}```')
            case commands.errors.MissingRequiredArgument:
                if check_channel:
                    await ctx.send(f'추가 인자를 입력해 주세요. ```{str(error)}```')
            case _:
                await super().on_command_error(ctx, error)

    async def _broadcast(self, content: str) -> None:
        for channel_id in self.config['discord']['bots']['flaskfarmaider']['broadcast']['target']['channels']:
            target_ch = self.get_channel(channel_id)
            if not target_ch:
                logger.warning(f'Channel {channel_id} not found.')
                continue
            logger.debug(f'Broadcast to {channel_id}: "{content}"')
            try:
                await target_ch.send(content)
            except Exception:
                logger.exception(f'Failed to send message to {channel_id}: {content=}')

    async def broadcast(self, path: str, mode: str) -> None:
        content = self.get_broadcast_content(path, mode)
        await self._broadcast(content)

    def get_broadcast_content(self, path: str, mode: str) -> str:
        data = {
            't1': 'gds_tool',
            't2': 'fp',
            't3': 'user',
            'data': {
                'gds_path': path,
                'scan_mode': mode,
            }
        }
        encrypted_data = encrypt(json.dumps(data), self.config['discord']['bots']['flaskfarmaider']['broadcast']['encrypt']['key'])
        return f'```^{encrypted_data}```'

    async def request_broadcast(self) -> None:
        while True:
            try:
                path, mode = await self.broadcast_queue.get()
                try:
                    await self.broadcast(path, mode)
                finally:
                    self.broadcast_queue.task_done()
            except Exception as e:
                logger.exception(repr(e))

