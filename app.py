import sys
from flaskfarmaider_bot import cli


def main(*args):
    cli.main(*args)


if __name__ == "__main__":
    print(f"설치:")
    print(f"  python -m pip install --upgrade pip setuptools wheel")
    print(f"  pip install --src . -e 'git+https://github.com/halfaider/flaskfarmaider-bot.git#egg=flaskfarmaider_bot'")
    print(f"실행:")
    print(f"  ffaider-bot -h")
    print(f"  ffaider-bot")
    print(f"  ffaider-bot /path/to/settings.yaml")
    print(f"  {sys.executable} -m flaskfarmaider_bot.cli /path/to/config.yaml")
    main(*sys.argv)
