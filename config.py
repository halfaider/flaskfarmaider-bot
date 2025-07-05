import os
import pathlib
import logging

import yaml

logger = logging.getLogger(__name__)


def get_default_config() -> dict:
    return {
        'logging': {
            'level': 'DEBUG',
            'level_discord': 'INFO',
            'format': '%(asctime)s|%(levelname).3s| %(message)s <%(filename)s:%(lineno)d#%(funcName)s>',
            'redacted_patterns': [
                "apikey=(.{10,36})",
                "'apikey': '(.{10,36})'",
                "'X-Plex-Token': '(.{20})'",
                "'X-Plex-Token=(.{20})'",
                "webhooks/(.+)/(.+):\\s{",
            ],
            'redacted_substitute': '<REDACTED>',
        },
        'discord': {
            'bots': {
                'flaskfarmaider': {
                    'token': '',
                    'command': {
                        'prefix': '!',
                        'checks': {
                            'channels': []
                        }
                    },
                    'broadcast': {
                        'source': {
                            'channels': [],
                            'authors': []
                        },
                        'target': {
                            'channels': []
                        }
                    },
                    'flaskfarm': {
                        'url': '',
                        'apikey': ''
                    },
                    'api': {
                        'keys': [],
                        'host': '0.0.0.0',
                        'port': 8080,
                    }
                }
            }
        }
    }


def get_config(config_yaml: pathlib.Path = None) -> dict:
    yaml_config = None
    config_files = [pathlib.Path(__file__).with_name('config.yaml'), pathlib.Path(os.getcwd(), 'config.yaml')]
    if config_yaml:
        config_files.insert(0, config_yaml)
    for yaml_file in config_files:
        try:
            with open(yaml_file, 'r', encoding='utf-8') as file_stream:
                yaml_config = yaml.safe_load(file_stream)
                print(f'{yaml_file.resolve()} 파일을 불러왔습니다.')
                break
        except Exception as e:
            print(repr(e))
    else:
        raise Exception('config.yaml 파일을 불러오지 못 했습니다.')

    if not yaml_config:
        raise Exception('설정 값을 가져올 수 없습니다.')

    config = get_default_config()
    config.update(yaml_config)

    return config
