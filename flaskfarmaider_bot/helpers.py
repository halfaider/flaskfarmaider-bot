import os
import re
import sys
import copy
import base64
import logging
import logging.config
import subprocess
from typing import Any, Iterable, Sequence

from Crypto import Random
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

BS = 16
pad = lambda s: s + (BS - len(s.encode("utf-8")) % BS) * chr(
    BS - len(s.encode("utf-8")) % BS
)
unpad = lambda s: s[: -ord(s[len(s) - 1 :])]


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
            subprocess.check_call((sys.executable, "-m", "pip", "install", "-U", pi))


class RedactingFilter(logging.Filter):

    def __init__(
        self, patterns: Sequence = (), substitute: str = "<REDACTED>", **kwds: Any
    ) -> None:
        super().__init__(**kwds)
        self.patterns = tuple(re.compile(p, re.IGNORECASE) for p in patterns if p)
        self.substitute = substitute

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        # getMessage() 결과에 이미 args가 반영되어 있음
        record.args = ()
        return True

    def redact(self, text: str) -> str:
        for pattern in self.patterns:
            if pattern.groups == 0:
                text = pattern.sub(self.substitute, text)
            else:
                text = pattern.sub(self.replace_match_groups, text)

        return text

    def replace_match_groups(self, match: re.Match) -> str:
        full_match_text = match.group(0)
        match_start_pos = match.start(0)
        group_spans = []
        for idx in range(1, match.re.groups + 1):
            if match.group(idx):
                group_spans.append(match.span(idx))
        group_spans.sort()
        result_parts = []
        last_end_in_match = 0
        for start, end in group_spans:
            start_in_match = start - match_start_pos
            end_in_match = end - match_start_pos
            result_parts.append(full_match_text[last_end_in_match:start_in_match])
            result_parts.append(self.substitute)
            last_end_in_match = end_in_match
        result_parts.append(full_match_text[last_end_in_match:])
        return "".join(result_parts)


def set_logger(
    level: str = None,
    format: str = None,
    date_format: str = None,
    redacted_patterns: Iterable = None,
    redacted_substitute: str = None,
) -> None:
    default_logging_config = {
        "version": 1,
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "filters": ["redacted"],
            },
        },
        "formatters": {
            "default": {
                "format": "%(asctime)s,%(msecs)03d %(levelname)-8s %(message)s ... %(filename)s:%(lineno)d",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "loggers": {
            __package__: {
                "level": "DEBUG",
                "handlers": ["console"],
                "propagate": False,
            },
        },
        "filters": {
            "redacted": {
                "()": f"{RedactingFilter.__module__}.{RedactingFilter.__name__}",
                "patterns": [
                    r"apikey=(.{10})",
                    r'["]apikey["]: ["](.{10})["]',
                    r'["]X-Plex-Token["]: ["](.{20})["]',
                    r'["]X-Plex-Token=(.{20})["]',
                    r"webhooks/(.+)/(.+):\s{",
                ],
                "substitute": "<REDACTED>",
            },
        },
    }
    try:
        level = getattr(logging, (level or "info").upper(), logging.INFO)
        default_logging_config["loggers"][__package__]["level"] = logging.DEBUG
        if redacted_patterns:
            default_logging_config["filters"]["redacted"][
                "patterns"
            ] = redacted_patterns
        if redacted_substitute:
            default_logging_config["filters"]["redacted"][
                "substitute"
            ] = redacted_substitute
        logging.config.dictConfig(default_logging_config)
    except Exception as e:
        logger.warning(f"로깅 설정 실패: {e}", exc_info=True)
        logging.basicConfig(
            level=level or logging.DEBUG,
            format=format
            or "%(asctime)s,%(msecs)03d|%(levelname)8s| %(message)s <%(filename)s:%(lineno)d#%(funcName)s>",
            datefmt=date_format or "%Y-%m-%dT%H:%M:%S",
        )


def should_merge(base_value: Any, value: Any, merge_type: type) -> bool:
    return isinstance(base_value, merge_type) and isinstance(value, merge_type)


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        base_value = result.get(key)
        if should_merge(base_value, value, dict):
            result[key] = deep_merge(result[key], value)
        # elif should_merge(base_value, value, list):
        #    result[key] = base_value + value
        else:
            result[key] = value
    return result
