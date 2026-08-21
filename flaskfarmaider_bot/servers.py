import json
import logging
import inspect
from typing import Any, Callable, Awaitable, Sequence, TypeVar, TYPE_CHECKING
from functools import wraps

from aiohttp import web

from .models import APIConfig

if TYPE_CHECKING:
    from .bot import FlaskfarmaiderBot

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
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        aio_logger = logging.getLogger("aiohttp.access")
        aio_logger.setLevel(logging.DEBUG)
        if not aio_logger.hasHandlers():
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
                route_method_str = getattr(method, "route_method", "GET").upper()
                route_auth_required = getattr(method, "route_auth_required", True)
                if route_method_str in ("*", "ANY"):
                    logger.debug(
                        f'Add route: path="{route_path}" method="*" auth_required={route_auth_required}'
                    )
                    app.router.add_route("*", route_path, method, name=method.__name__)
                else:
                    route_method = f"add_{route_method_str.lower()}"
                    if hasattr(app.router, route_method):
                        route_func = getattr(app.router, route_method, None)
                        if route_func:
                            logger.debug(
                                f'Add route: path="{route_path}" method="{route_method_str}" auth_required={route_auth_required}'
                            )
                            route_func(route_path, method, name=method.__name__)
        self.runner = web.AppRunner(
            app,
            access_log=logger,
            access_log_format='%a "%r" %s %b "%{Referer}i" "%{User-Agent}i"',
        )
        await self.runner.setup()
        host = self.settings.host or "0.0.0.0"
        port = self.settings.port or 8080
        self.site = web.TCPSite(self.runner, host=host, port=port)
        await self.site.start()
        logger.info(f"Listen on http://{host}:{port}")

    async def stop(self) -> None:
        if self.site:
            logger.info("Stopping the site...")
            await self.site.stop()
            self.site = None
        if self.runner:
            logger.info("Cleaning up AppRunner...")
            await self.runner.cleanup()
            self.runner = None
        logger.info("Server stopped successfully...")

    @route("/", "GET", False)
    async def index(self, request: web.Request) -> web.Response:
        return web.Response(text=":)")

    @route("/api/webhook", "*", True)
    async def dummy_webhook(self, request: web.Request) -> web.Response:
        data = None
        content_type = request.content_type.lower() if request.content_type else ""
        if content_type.startswith("application/json"):
            try:
                data = await request.json()
            except Exception:
                try:
                    raw = await request.text()
                    data = raw if raw else "(empty body)"
                except Exception:
                    data = "<Invalid Body>"
        elif content_type.startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")
        ):
            try:
                post_data = await request.post()
                data = dict(post_data)
                if "payload_json" in data:
                    try:
                        data["payload_json"] = json.loads(data["payload_json"])
                    except Exception:
                        pass
            except Exception:
                data = "<Invalid Form Data>"
        else:
            try:
                raw = await request.text()
                data = raw if raw else None
            except Exception:
                pass

        log_lines = [
            f"[Dummy Webhook] Received {request.method} webhook: path={request.path}",
            f"  - Headers: {dict(request.headers)}",
            f"  - Query: {dict(request.query)}",
        ]

        # Extract and display payload / content specifically if present
        content_val = None
        payload_val = None
        if isinstance(data, dict):
            content_val = data.get("content")
            payload_val = data.get("payload") or data.get("payload_json")
        elif request.query.get("content") or request.query.get("payload"):
            content_val = request.query.get("content")
            payload_val = request.query.get("payload")

        if content_val is not None:
            log_lines.append(f"  - Content: {content_val}")
        if payload_val is not None:
            log_lines.append(f"  - Payload: {payload_val}")
        if data is not None:
            log_lines.append(f"  - Data: {data}")

        logger.info("\n".join(log_lines))
        return web.Response(status=204)


class BotAPIServer(Server):

    def __init__(self, bot: "FlaskfarmaiderBot", settings: APIConfig, **kwds: Any) -> None:
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
