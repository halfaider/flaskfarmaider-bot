import os
import logging

import discord
from discord.ext import commands
import pydantic

from .bot import FlaskfarmaiderBot
from .models import AppSettings
from .helpers.loggers import set_logger

logger = logging.getLogger(__name__)


def main(settings_file: str | os.PathLike | None = None) -> None:
    try:
        settings = AppSettings(user_yaml_file=settings_file) # type: ignore
    except pydantic.ValidationError as e:
        logger.error(e)
        return
    set_logger(
        level=settings.logging.level,
        format=settings.logging.format,
        datefmt=settings.logging.date_format,
        redacted_patterns=settings.logging.redacted_patterns,
        redacted_substitute=settings.logging.redacted_substitute,
    )

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = FlaskfarmaiderBot(
        command_prefix=settings.discord.command.prefix,
        settings=settings,
        description="flaskfarmaider-bot",
        intents=intents,
    )
    bot.run(
        settings.discord.token,
        log_level=getattr(logging, settings.logging.level_discord.upper(), logging.INFO),
        log_formatter=logging.Formatter(settings.logging.format),
    )
