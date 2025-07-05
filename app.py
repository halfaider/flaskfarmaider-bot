import sys
import logging
import pathlib
from typing import Any

from helpers import check_packages, set_logger
from config import get_config
from bots import FlaskfarmaiderBot

check_packages((
    ('discord', 'discord.py'),
    ('aiohttp', 'aiohttp'),
    ('yaml', 'pyyaml'),
    ('Crypto', 'pycryptodome')
))

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


def main(*args: Any, **kwds: Any) -> None:
    config = get_config(pathlib.Path(args[1]) if len(args) > 1 else None)

    # logging
    modules = set(file.stem for file in pathlib.Path(__file__).parent.glob('*.py'))
    if '__main__' not in modules:
        modules.add('__main__')
    log_settings = config.pop('logging', {}) or {}
    set_logger(
        level=log_settings.get('level'),
        format=log_settings.get('format'),
        date_format=log_settings.get('date_format'),
        redacted_patterns=log_settings.get('redacted_patterns'),
        redacted_substitute=log_settings.get('redacted_substitute'),
        loggers=modules,
    )

    #Global check function
    def check_channel(ctx: commands.Context) -> bool:
        if valid_channels := config['discord']['bots']['flaskfarmaider']['command']['checks']['channels']:
            if not ctx.channel.id in valid_channels:
                logger.error(f'Invalid channels: {ctx.channel.name} ({ctx.channel.id})')
                return False
        return True

    # with everything enabled except presences, members, and message_content.
    intents = discord.Intents.default()
    intents.message_content = True

    bot = FlaskfarmaiderBot(
        command_prefix=config['discord']['bots']['flaskfarmaider']['command']['prefix'],
        config=config,
        checks=[check_channel],
        description='Flaskfarmaider',
        intents=intents)
    bot.run(
        config['discord']['bots']['flaskfarmaider']['token'],
        log_level=log_settings['level_discord'],
        log_formatter=logging.Formatter(log_settings['format'])
    )


if __name__ == '__main__':
    main(*sys.argv)
