## 설치

### 파이썬 설치 패키지 업그레이드

```bash
python -m pip install --upgrade pip setuptools wheel
```

### 현재 경로에 `flaskfarmaider-bot` 폴더를 만들어서 git clone

소스 업데이트도 아래 명령어로 실행하세요.

```bash
pip install --src . -e "git+https://github.com/halfaider/flaskfarmaider-bot.git#egg=flaskfarmaider_bot"
```

## 실행

### `ffaider-bot` 명령어로 실행

```bash
ffaider-bot
```

설정 파일을 따로 지정하지 않으면 자동으로 설정 파일을 탐색합니다.

```
/data/commands/flaskfarmaider-bot
    /flaskfarmaider_bot
        __init__.py
        bots.py
        cli.py
        ...
        settings.sample.yaml
    .gitignore
    ...
    requirements.txt
```

이런 폴더 구조로 설치되어 있다고 가정할 경우 설정 파일은 아래의 순서대로 탐색됩니다.

```
/data/commands/flaskfarmaider-bot/flaskfarmaider_bot/settings.yaml
${PWD}/settings.yaml
/data/commands/flaskfarmaider-bot/flaskfarmaider_bot/config.yaml
${PWD}/config.yaml
```

### 설정 파일을 지정

설정 파일 경로를 따로 지정할 경우 아래처럼 입력하세요.

```bash
ffaider-bot /data/db/ffaider-bot.yaml
```

### 패키지 모듈을 지정해서 실행

```bash
python3 -m flaskfarmaider_bot.cli /path/to/settings.yaml
```