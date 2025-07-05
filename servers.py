import logging
import inspect
from typing import Any, Callable

from aiohttp import web
from discord.ext import commands

logger = logging.getLogger(__name__)


def route(path: str, method: str = 'GET', auth_required: bool = True) -> Callable:
    def decorator(func: Callable) -> Callable:
        func.route_path = path
        func.route_method = method.upper()
        func.route_auth_required = auth_required
        return func
    return decorator


class Server:

    def __init__(self, config: dict = None) -> None:
        self.config = {
            'keys': (),
            'port': 8080,
            'host': '0.0.0.0'
        }
        if config:
            self.config.update(config)
        aio_logger = logging.getLogger('aiohttp.access')
        aio_logger.setLevel(logging.DEBUG)
        aio_logger.addHandler(logging.StreamHandler())

    async def check_api_key_middleware(self, app: web.Application, handler: Callable) -> Callable:
        async def middleware_handler(request: web.Request) -> web.StreamResponse:
            route_info = request.match_info.route
            handler_func = getattr(route_info, 'handler', None)
            auth_required = getattr(handler_func, 'route_auth_required', True)
            if auth_required and self.config.get('keys'):
                apikey_in_body = None
                if request.method == 'POST':
                    if request.content_type.lower().startswith('application/json'):
                        try:
                            data = await request.json()
                            apikey_in_body = data.get('apikey')
                        except Exception:
                            pass
                    elif request.content_type.lower().startswith(('application/x-www-form-urlencoded', 'multipart/form-data')):
                        try:
                            data = await request.post()
                            apikey_in_body = data.get('apikey')
                        except Exception:
                            pass
                for key in (request.headers.get('X-apikey'), request.query.get('apikey'), apikey_in_body):
                    if key in self.config.get('keys'):
                        break
                else:
                    return web.json_response({'result': 'error', 'error': 'Unauthorized'}, status=401)
            return await handler(request)
        return middleware_handler

    async def start(self) -> None:
        app = web.Application(logger=logger, middlewares=[self.check_api_key_middleware])
        for _, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, 'route_path') and hasattr(method, 'route_method'):
                route_method = f"add_{method.route_method.lower()}"
                if hasattr(app.router, route_method):
                    route_func = getattr(app.router, route_method, None)
                    if route_func:
                        logger.debug(f'Add route: path="{method.route_path}" method="{method.route_method}" auth_required={method.route_auth_required}')
                        route_func(method.route_path, method, name=method.__name__)
        runner = web.AppRunner(app, access_log=logger, access_log_format='%a "%r" %s %b "%{Referer}i" "%{User-Agent}i"')
        await runner.setup()
        host = self.config.get('host') or '0.0.0.0'
        port = self.config.get('port') or 8080
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        logger.info(f'Listen on http://{host}:{port}')

    @route('/', 'GET', False)
    async def index(self, request: web.Request) -> web.Response:
        return web.Response(text=':)')


class APIServer(Server):
    pass


class BotAPIServer(APIServer):

    def __init__(self, bot: commands.Bot, config: dict = None, **kwds: Any) -> None:
        super(BotAPIServer, self).__init__(config=config, **kwds)
        self.bot = bot


class FFaiderBotAPI(BotAPIServer):

    @route('/api/broadcast', method='POST')
    async def api_broadcast(self, request: web.Request) -> web.Response:
        if request.content_type.lower().startswith('application/json'):
            data = await request.json()
        elif request.content_type.lower().startswith(('application/x-www-form-urlencoded', 'multipart/form-data')):
            data = await request.post()
        else:
            return web.json_response({'result': 'error', 'error': 'Invalid content type'}, status=400)
        path = data.get('path')
        mode = data.get('mode')
        logger.debug(f'{path=} {mode=}')
        if not path or not mode:
            return web.json_response({'result': 'error', 'error': 'Invalid values'}, status=400)
        try:
            await self.bot.broadcast(path, mode)
        except Exception as e:
            logger.exception(repr(e))
            return web.json_response({'result': 'error', 'error': 'Broadcast failed'}, status=500)
        return web.Response(status=204)
