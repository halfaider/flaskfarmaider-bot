import os
import re
import sys
import base64
import logging
import datetime
import subprocess
from typing import Any, Iterable, Sequence

from Crypto import Random
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

BS = 16
pad = lambda s: s + (BS - len(s.encode('utf-8')) % BS) * chr(BS - len(s.encode('utf-8')) % BS)
unpad = lambda s : s[:-ord(s[len(s)-1:])]


def encrypt(content: str, key: str):
    content_: bytes = pad(content).encode()
    key_: bytes = key.encode()
    iv: bytes = Random.new().read(AES.block_size)
    cipher = AES.new(key_, AES.MODE_CBC, iv)
    tmp: bytes = cipher.encrypt(content_)
    result = base64.b64encode(iv + tmp)
    result = result.decode()
    return result


def decrypt(encoded: str, key: str):
    encoded = base64.b64decode(encoded)
    iv = encoded[:16]
    if len(iv) != 16:
        iv = os.urandom(16)
    key_: bytes = key.encode()
    print(type(key_))
    cipher = AES.new(key_, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(encoded[16:])).decode()


def check_packages(packages: Iterable[Sequence[str]]) -> None:
    for pkg, pi in packages:
        try:
            __import__(pkg)
        except Exception as e:
            print(repr(e))
            subprocess.check_call((sys.executable, '-m', 'pip', 'install', '-U', pi))


class RedactedFormatter(logging.Formatter):

    def __init__(self, *args: Any, patterns: Iterable = (), substitute: str = '<REDACTED>', **kwds: Any):
        super(RedactedFormatter, self).__init__(*args, **kwds)
        self.patterns = tuple(re.compile(pattern, re.I) for pattern in patterns)
        self.substitute = substitute

    def format(self, record):
        msg = super().format(record)
        for pattern in self.patterns:
            match = pattern.search(msg)
            if match:
                groups = groups if len(groups := match.groups()) > 0 else (match.group(0),)
                for found in groups:
                    msg = self.redact(re.compile(found, re.I), msg)
        return msg

    def formatTime(self, record: logging.LogRecord, datefmt: str = None):
        dt = datetime.datetime.fromtimestamp(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
            return s[:-3]
        else:
            return super().formatTime(record, datefmt)

    def redact(self, pattern: re.Pattern, text: str) -> str:
        return pattern.sub(self.substitute, text)


def set_logger(level: str = None,
               format: str = None,
               date_format: str = None,
               redacted_patterns: Iterable = None,
               redacted_substitute: str = None,
               handlers: Iterable = None,
               loggers: Iterable = None) -> None:
    try:
        level = getattr(logging, (level or 'info').upper(), logging.INFO)
        fomatter = RedactedFormatter(
            patterns=redacted_patterns or (r'apikey=(.{10})',),
            substitute=redacted_substitute or '<REDACTED>',
            fmt=format or '%(asctime)s|%(levelname)8s| %(message)s <%(filename)s:%(lineno)d#%(funcName)s>',
            datefmt=date_format or '%Y-%m-%dT%H:%M:%S'
        )
        if not handlers:
            handlers = (logging.StreamHandler(),)
        for mod in loggers or ():
            module_logger = logging.getLogger(mod)
            module_logger.setLevel(level)
            for handler in handlers:
                if not any(isinstance(h, type(handler)) for h in module_logger.handlers):
                    handler.setFormatter(fomatter)
                    module_logger.addHandler(handler)
    except Exception as e:
        logger.warning(f'로깅 설정 실패: {e}', exc_info=True)
        logging.basicConfig(
            level=level or logging.DEBUG,
            format=format or '%(asctime)s|%(levelname)8s| %(message)s <%(filename)s:%(lineno)d#%(funcName)s>',
            datefmt=date_format or '%Y-%m-%dT%H:%M:%S'
        )
