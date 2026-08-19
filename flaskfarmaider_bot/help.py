from typing import Any

from discord.ext import commands


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

    async def send_pages(self) -> None:
        """override"""
        for page in self.paginator.pages:
            await self.context.reply(page)
