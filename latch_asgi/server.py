import asyncio
import dataclasses
import traceback
from http import HTTPStatus

import opentelemetry.context as context
from hypercorn.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from typing import Awaitable
from latch_data_validation.data_validation import untraced_validate, validate
from latch_o11y.o11y import log
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace import get_current_span, get_tracer

from .datadog_propagator import DDTraceContextTextMapPropagator

from .asgi_iface import (
    HTTPDisconnectEvent,
    HTTPReceiveCallable,
    HTTPRequestEvent,
    HTTPScope,
    HTTPSendCallable,
    HTTPSendEvent,
    LifespanReceiveCallable,
    LifespanScope,
    LifespanSendCallable,
    LifespanSendEvent,
    LifespanShutdownCompleteEvent,
    LifespanShutdownEvent,
    LifespanShutdownFailedEvent,
    LifespanStartupCompleteEvent,
    LifespanStartupEvent,
    LifespanStartupFailedEvent,
    type_str,
)
from .context import Context, Route
from .framework import (
    HTTPErrorResponse,
    HTTPInternalServerError,
    current_http_request_span,
    http_request_span_key,
    send_auto,
    send_data,
)

tracer = get_tracer(__name__)


# todo(maximsmol): ASGI instrumentation should trace lifespan

class LatchASGIServer:
    routes: dict[str, Route]
    startup_tasks: list[Awaitable] = []
    shutdown_tasks: list[Awaitable] = []

    def __init__(self, routes: dict[str, Route], startup_tasks: list[Awaitable] = [], shutdown_tasks: list[Awaitable] = []):
        self.routes = routes
        self.startup_tasks = startup_tasks
        self.shutdown_tasks = shutdown_tasks

    async def scope_lifespan(
        self, scope: LifespanScope, receive: LifespanReceiveCallable, send: LifespanSendCallable
    ):
        await log.info(
            f"Waiting for lifespan events (ASGI v{scope.asgi.version} @ spec"
            f" v{scope.asgi.spec_version})"
        )
        while True:
            message = await receive()
            await log.info(repr(message.type))

            if isinstance(message, LifespanStartupEvent):
                with tracer.start_as_current_span("startup"):
                    try:
                        await log.info("Connecting to Vacuole")
                        # todo(maximsmol): debug clock skew on connection reset
                        await asyncio.gather(*self.startup_tasks)

                        with tracer.start_as_current_span("send completion event"):
                            await send(
                                LifespanStartupCompleteEvent("lifespan.startup.complete")
                            )
                    except Exception as e:
                        with tracer.start_as_current_span("send failure event"):
                            await send(
                                LifespanStartupFailedEvent(
                                    "lifespan.startup.failed", str(e)
                                )
                            )

                        raise e
            elif isinstance(message, LifespanShutdownEvent):
                with tracer.start_as_current_span("shutdown"):
                    try:
                        await asyncio.gather(*self.shutdown_tasks)

                        with tracer.start_as_current_span("send completion event"):
                            await send(
                                LifespanShutdownCompleteEvent("lifespan.shutdown.complete")
                            )
                    except Exception as e:
                        with tracer.start_as_current_span("send failure event"):
                            await send(
                                LifespanShutdownFailedEvent(
                                    "lifespan.shutdown.failed", str(e)
                                )
                            )

                        raise e

                    break


    async def scope_http(
        self, scope: HTTPScope, receive: HTTPReceiveCallable, send: HTTPSendCallable
    ):
        ctx_reset_token: object | None = None
        try:
            new_ctx = context.set_value(http_request_span_key, get_current_span())
            ctx_reset_token = context.attach(new_ctx)

            current_http_request_span().set_attribute("resource.name", scope.path)
            await log.info(f"{scope.method} {scope.path}")

            with tracer.start_as_current_span("find route handler"):
                route = self.routes.get(scope.path)

            if not isinstance(route, tuple):
                methods = ["POST"]
                handler = route
            else:
                methods, handler = route

            if scope.method not in methods:
                if len(methods) == 1:
                    methods_str = methods[0]
                elif len(methods) == 2:
                    methods_str = f"{methods[0]} and {methods[1]}"
                else:
                    methods_str = ", and ".join([", ".join(methods[:-1]), methods[-1]])

                await send_data(
                    send,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    f"Only {methods_str} requests are supported",
                )
                return

            if handler is None:
                # todo(maximsmol): better error message
                await log.info("Not found")
                await send_data(send, HTTPStatus.NOT_FOUND, "Not found")
                return

            try:
                try:
                    ctx = Context(scope, receive, send)
                    res = await handler(ctx)

                    if res is not None:
                        with tracer.start_as_current_span("send response"):
                            await send_auto(send, HTTPStatus.OK, res)
                    return
                except HTTPErrorResponse as e:
                    raise e
                except Exception as e:
                    # todo(maximsmol): better error message
                    raise HTTPInternalServerError("Internal error") from e
            except HTTPErrorResponse as e:
                await send_auto(send, e.status, {"error": e.data}, headers=e.headers)
                if e.status == HTTPStatus.INTERNAL_SERVER_ERROR:
                    # await log.exception() # fixme(maximsmol)
                    traceback.print_exc()
                return
        finally:
            if ctx_reset_token is not None:
                context.detach(ctx_reset_token)


    async def raw_app(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable):
        try:
            if scope["type"] == "lifespan":

                async def ls_receive():
                    x = await receive()

                    if x["type"] == type_str(LifespanStartupEvent):
                        return untraced_validate(x, LifespanStartupEvent)

                    if x["type"] == type_str(LifespanShutdownEvent):
                        return untraced_validate(x, LifespanShutdownEvent)

                    raise RuntimeError(f"unknown lifespan event type: {repr(x['type'])}")

                async def ls_send(e: LifespanSendEvent):
                    data = dataclasses.asdict(e)
                    await send(data)

                return await self.scope_lifespan(
                    untraced_validate(scope, LifespanScope), ls_receive, ls_send
                )

            if scope["type"] == "http":

                async def http_receive():
                    x = await receive()

                    if x["type"] == type_str(HTTPRequestEvent):
                        return validate(x, HTTPRequestEvent)

                    if x["type"] == type_str(HTTPDisconnectEvent):
                        return validate(x, HTTPDisconnectEvent)

                    raise RuntimeError(f"unknown http event type: {repr(x['type'])}")

                async def http_send(e: HTTPSendEvent):
                    data = dataclasses.asdict(e)
                    await send(data)

                return await self.scope_http(validate(scope, HTTPScope), http_receive, http_send)

            raise RuntimeError(f"unsupported protocol: {repr(scope['type'])}")
        except Exception as e:
            await log.exception("Fallback exception handler:")
            raise e


set_global_textmap(DDTraceContextTextMapPropagator())
