import logging
import inspect
from typing import Any, Callable, Awaitable, Sequence, TypeVar
from functools import wraps

from aiohttp import web

from .models import APIConfig
from .protocols import Broadcastable

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Callable[..., Any])


def route(path: str, method: str = "GET", auth_required: bool = True) -> Callable:
    def decorator(func: T) -> T:
        setattr(func, "route_path", path)
        setattr(func, "route_method", method.upper())
        setattr(func, "route_auth_required", auth_required)
        return func

    return decorator


def validate_post_data(method):
    @wraps(method)
    async def wrapper(self, request: web.Request, *args, **kwds):
        content_type = request.content_type.lower() if request.content_type else ""
        if content_type.startswith("application/json"):
            try:
                data = await request.json()
            except Exception:
                return web.json_response(
                    {"result": "error", "error": "Invalid JSON"}, status=400
                )
        elif content_type.startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")
        ):
            data = await request.post()
        else:
            return web.json_response(
                {"result": "error", "error": "Invalid content type"}, status=400
            )
        return await method(self, request, data, *args, **kwds)

    return wrapper


class Server:

    def __init__(self, settings: APIConfig) -> None:
        self.settings = settings
        aio_logger = logging.getLogger("aiohttp.access")
        aio_logger.setLevel(logging.DEBUG)
        aio_logger.addHandler(logging.StreamHandler())

    @web.middleware
    async def check_api_key_middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        route_info = request.match_info.route
        handler_func = getattr(route_info, "handler", None)
        auth_required = getattr(handler_func, "route_auth_required", True)
        if auth_required and self.settings.keys:
            apikey_in_body = None
            content_type = request.content_type.lower() if request.content_type else ""
            if request.method == "POST":
                request_func = None
                if content_type.lower().startswith("application/json"):
                    request_func = request.json
                elif content_type.lower().startswith(
                    ("application/x-www-form-urlencoded", "multipart/form-data")
                ):
                    request_func = request.post
                try:
                    if request_func is not None:
                        data = await request_func()
                        apikey_in_body = data.get("apikey")
                    else:
                        return web.json_response(
                            {"result": "error", "error": "Invalid content type"},
                            status=400,
                        )
                except Exception as e:
                    logger.warning(e)
            for key in (
                request.headers.get("x-apikey"),
                request.query.get("apikey"),
                apikey_in_body,
            ):
                if key in self.settings.keys:
                    break
            else:
                return web.json_response(
                    {"result": "error", "error": "Unauthorized"}, status=401
                )
        return await handler(request)

    async def start(self) -> None:
        app = web.Application(
            logger=logger, middlewares=[self.check_api_key_middleware]
        )
        for _, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, "route_path") and hasattr(method, "route_method"):
                route_path = getattr(method, "route_path", "")
                route_method_str = getattr(method, "route_method", "GET")
                route_auth_required = getattr(method, "route_auth_required", True)
                route_method = f"add_{route_method_str.lower()}"
                if hasattr(app.router, route_method):
                    route_func = getattr(app.router, route_method, None)
                    if route_func:
                        logger.debug(
                            f'Add route: path="{route_path}" method="{route_method_str}" auth_required={route_auth_required}'
                        )
                        route_func(route_path, method, name=method.__name__)
        runner = web.AppRunner(
            app,
            access_log=logger,
            access_log_format='%a "%r" %s %b "%{Referer}i" "%{User-Agent}i"',
        )
        await runner.setup()
        host = self.settings.host or "0.0.0.0"
        port = self.settings.port or 8080
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        logger.info(f"Listen on http://{host}:{port}")

    @route("/", "GET", False)
    async def index(self, request: web.Request) -> web.Response:
        return web.Response(text=":)")


class APIServer(Server):
    pass


class BotAPIServer(APIServer):

    def __init__(self, bot: Broadcastable, settings: APIConfig, **kwds: Any) -> None:
        super(BotAPIServer, self).__init__(settings=settings, **kwds)
        self.bot = bot


class FFaiderBotAPI(BotAPIServer):

    async def _handle_broadcast(
        self, data: dict, app: str, required_values: Sequence[str]
    ) -> web.Response:
        error_response = {"result": "error", "error": ""}
        if not all(data.get(key) for key in required_values):
            logger.warning(f"Invalid values for {app}: {data}")
            error_response["error"] = "Invalid values"
            return web.json_response(error_response, status=400)
        try:
            await self.bot.broadcast_queue.put((app, data))
        except Exception:
            logger.exception("Broadcast failed")
            error_response["error"] = "Broadcast failed"
            return web.json_response(error_response, status=500)
        return web.Response(status=204)

    @route("/api/broadcasts/gds", method="POST")
    @validate_post_data
    async def api_broadcast_gds(self, request: web.Request, data: dict) -> web.Response:
        return await self._handle_broadcast(data, "gds", ("path", "mode"))

    @route("/api/broadcasts/downloader", method="POST")
    @validate_post_data
    async def api_broadcast_downloader(
        self, request: web.Request, data: dict
    ) -> web.Response:
        return await self._handle_broadcast(data, "downloader", ("path", "item"))
