import asyncio
from collections.abc import Awaitable
from http import HTTPStatus
from typing import Self

import orjson
from hypercorn.typing import (
    ASGIReceiveCallable,
    ASGISendCallable,
    HTTPScope,
    LifespanScope,
    LifespanShutdownCompleteEvent,
    LifespanShutdownFailedEvent,
    LifespanStartupCompleteEvent,
    LifespanStartupFailedEvent,
    Scope,
    WebsocketScope,
)
from latch_o11y.o11y import log, trace_function_with_span
from opentelemetry import context
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace import Span, SpanKind, get_tracer
from opentelemetry.util.types import AttributeValue

from .asgi_iface import (
    HTTPReceiveCallable,
    HTTPReceiveEvent,
    HTTPSendCallable,
    HTTPSendEvent,
    LifespanReceiveCallable,
    LifespanReceiveEvent,
    LifespanSendCallable,
    LifespanSendEvent,
    WebsocketReceiveCallable,
    WebsocketReceiveEventT,
    WebsocketSendCallable,
    WebsocketSendEventT,
)
from .context import http, websocket
from .datadog_propagator import DDTraceContextTextMapPropagator
from .framework.common import otel_header_whitelist
from .framework.http import (
    HTTPErrorResponse,
    HTTPInternalServerError,
    current_http_request_span,
    http_request_span_key,
    send_auto,
    send_http_data,
)
from .framework.websocket import (
    WebsocketErrorResponse,
    WebsocketInternalServerError,
    WebsocketStatus,
    close_connection,
    current_websocket_session_span,
    websocket_session_span_key,
)

tracer = get_tracer(__name__)


# todo(maximsmol): ASGI instrumentation should trace lifespan


def get_common_attrs(scope: HTTPScope | WebsocketScope) -> dict[str, AttributeValue]:
    client_addr = scope.get("client")
    if client_addr is None:
        client_addr = (None, None)

    server_addr = scope.get("server")
    if server_addr is None:
        server_addr = (None, None)

    attrs: dict[str, AttributeValue] = {
        "client.address": str(client_addr[0]),
        "client.port": str(client_addr[1]),
        "server.address": str(server_addr[0]),
        "server.port": str(server_addr[1]),
        "network.transport": "tcp",
        "network.peer.address": str(client_addr[0]),
        "network.peer.port": str(client_addr[1]),
        "network.local.address": str(server_addr[0]),
        "network.local.port": str(server_addr[1]),
        "network.protocol.name": "http",
        "network.protocol.version": scope["http_version"],
        "url.scheme": scope["scheme"],
        "url.path": scope["path"],
        "url.path_original": scope["raw_path"],
        "url.query": scope["query_string"],
    }

    for k, v in scope["headers"]:
        k = k.decode("latin-1")
        k = k.lower()

        if k == "user-agent":
            attrs["user_agent.original"] = v.decode("latin-1")

        if k not in otel_header_whitelist:
            v = b"REDACTED"

        attrs[f"http.request.header.{k}"] = v.decode("latin-1")

    return attrs


class LatchASGIServer:
    def __init__(
        self: Self,
        *,
        http_routes: dict[str, http.Route] | None = None,
        websocket_routes: dict[str, websocket.Route] | None = None,
        startup_tasks: list[Awaitable] | None = None,
        shutdown_tasks: list[Awaitable] | None = None,
    ) -> None:
        if http_routes is None:
            http_routes = {}
        if websocket_routes is None:
            websocket_routes = {}

        if startup_tasks is None:
            startup_tasks = []
        if shutdown_tasks is None:
            shutdown_tasks = []

        self.http_routes: dict[str, http.Route] = http_routes
        self.websocket_routes: dict[str, websocket.Route] = websocket_routes
        self.startup_tasks: list[Awaitable] = startup_tasks
        self.shutdown_tasks: list[Awaitable] = shutdown_tasks

    async def scope_lifespan(
        self: Self,
        scope: LifespanScope,
        receive: LifespanReceiveCallable,
        send: LifespanSendCallable,
    ) -> None:
        asgi_v = scope["asgi"].get("version", "2.0")
        asgi_spec_v = scope["asgi"].get("spec_version", "1.0")

        await log.info(
            f"Waiting for lifespan events (ASGI v{asgi_v} @ spec v{asgi_spec_v})"
        )

        while True:
            message = await receive()

            if message["type"] == "lifespan.startup":
                with tracer.start_as_current_span(
                    "ASGI Startup",
                    attributes={
                        "asgi.event_type": message["type"],
                        "asgi.version": asgi_v,
                        "asgi.spec_version": asgi_spec_v,
                    },
                ):
                    try:
                        await log.info("Executing startup tasks")

                        set_global_textmap(DDTraceContextTextMapPropagator())

                        # todo(maximsmol): debug clock skew on connection reset
                        await asyncio.gather(*self.startup_tasks)

                        await send(
                            LifespanStartupCompleteEvent(
                                type="lifespan.startup.complete"
                            )
                        )
                    except Exception as e:
                        await send(
                            LifespanStartupFailedEvent(
                                type="lifespan.startup.failed", message=str(e)
                            )
                        )

                        raise
            elif message["type"] == "lifespan.shutdown":
                with tracer.start_as_current_span(
                    "ASGI Shutdown", attributes={"asgi.event_type": message["type"]}
                ):
                    try:
                        await asyncio.gather(*self.shutdown_tasks)

                        await send(
                            LifespanShutdownCompleteEvent(
                                type="lifespan.shutdown.complete"
                            )
                        )
                    except Exception as e:
                        await send(
                            LifespanShutdownFailedEvent(
                                type="lifespan.shutdown.failed", message=str(e)
                            )
                        )

                        raise

                    break
            else:
                raise RuntimeError(f"unknown lifespan event: {message['type']!r}")

    async def scope_websocket(
        self: Self,
        scope: WebsocketScope,
        receive: WebsocketReceiveCallable,
        send: WebsocketSendCallable,
    ) -> None:
        s = current_websocket_session_span()

        with tracer.start_as_current_span("find route handler"):
            handler = self.websocket_routes.get(scope["path"])

        if handler is None:
            # todo(maximsmol): better error message
            await log.info("Not found")
            await close_connection(
                send, status=WebsocketStatus.policy_violation, data="Not found"
            )
            return

        s.set_attribute("http.route", scope["path"])

        try:
            try:
                msg = await receive()
                if msg["type"] != "websocket.connect":
                    raise ValueError(
                        "ASGI protocol violation: missing websocket.connect event"
                    )

                ctx = websocket.Context(scope, receive, send)
                data = await handler(ctx)
            except WebsocketErrorResponse:
                raise
            except Exception as e:
                # todo(maximsmol): better error message
                raise WebsocketInternalServerError("Internal Error") from e
        except WebsocketErrorResponse as e:
            await close_connection(
                send, status=e.status, data=orjson.dumps({"error": e.data}).decode()
            )

            if e.status != WebsocketStatus.server_error:
                return

            # await log.exception() # fixme(maximsmol)
            raise
        else:
            await close_connection(send, status=WebsocketStatus.normal, data=data)

    async def scope_http(
        self: Self,
        scope: HTTPScope,
        receive: HTTPReceiveCallable,
        send: HTTPSendCallable,
    ) -> None:
        s = current_http_request_span()

        with tracer.start_as_current_span("find route handler"):
            route = self.http_routes.get(scope["path"])

        if route is None:
            await send_http_data(send, HTTPStatus.NOT_FOUND, "Not found")
            return

        s.set_attribute("http.route", scope["path"])

        if not isinstance(route, tuple):
            methods = ["POST"]
            handler = route
        else:
            methods, handler = route

        if scope["method"] not in methods:
            if len(methods) == 1:
                methods_str = methods[0]
            elif len(methods) == 2:
                methods_str = f"{methods[0]} and {methods[1]}"
            else:
                methods_str = ", and ".join([", ".join(methods[:-1]), methods[-1]])

            await send_http_data(
                send,
                HTTPStatus.METHOD_NOT_ALLOWED,
                f"Only {methods_str} requests are supported",
            )
            return

        if handler is None:
            # todo(maximsmol): better error message
            await log.info("Not found")
            await send_http_data(send, HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            try:
                ctx = http.Context(scope, receive, send)
                res = await handler(ctx)

                if res is not None:
                    with tracer.start_as_current_span("send response"):
                        await send_auto(send, HTTPStatus.OK, res)
            except HTTPErrorResponse:
                raise
            except Exception as e:
                # todo(maximsmol): better error message
                raise HTTPInternalServerError("Internal error") from e
        except HTTPErrorResponse as e:
            await send_auto(send, e.status, {"error": e.data}, headers=e.headers)

            if e.status != HTTPStatus.INTERNAL_SERVER_ERROR:
                return

            # await log.exception() # fixme(maximsmol)
            raise

    async def raw_app(
        self: Self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        try:
            if scope["type"] == "lifespan":
                # lifespan is not wrapped in a span because it has a `while(true)`

                @trace_function_with_span(tracer)
                async def ls_receive(s: Span) -> LifespanReceiveEvent:
                    x = await receive()
                    s.set_attribute("event_type", x["type"])

                    if x["type"] == "lifespan.startup":
                        return x

                    if x["type"] == "lifespan.shutdown":
                        return x

                    raise RuntimeError(f"unknown lifespan event type: {x['type']!r}")

                @trace_function_with_span(tracer)
                async def ls_send(s: Span, e: LifespanSendEvent) -> None:
                    s.set_attribute("type", e["type"])

                    if e["type"] == "lifespan.shutdown.failed":
                        s.set_attribute("data.message", e["message"])

                    if e["type"] == "lifespan.startup.failed":
                        s.set_attribute("data.message", e["message"])

                    await send(e)

                return await self.scope_lifespan(scope, ls_receive, ls_send)

            if scope["type"] == "websocket":
                span_name = f"WS {scope['path']}"
                with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER) as s:
                    new_ctx = context.set_value(websocket_session_span_key, s)
                    ctx_reset_token = context.attach(new_ctx)

                    try:
                        await log.info(span_name)

                        attrs = get_common_attrs(scope)
                        for i, x in enumerate(scope["subprotocols"]):
                            attrs[f"websocket.subprotocol.{i}"] = x
                        s.set_attributes(attrs)

                        @trace_function_with_span(tracer)
                        async def ws_receive(s: Span) -> WebsocketReceiveEventT:
                            x = await receive()
                            s.set_attribute("type", x["type"])

                            if x["type"] == "websocket.connect":
                                return x

                            if x["type"] == "websocket.disconnect":
                                s.set_attribute("data.disconnect_code", x["code"])
                                return x

                            if x["type"] == "websocket.receive":
                                attrs: dict[str, AttributeValue] = {
                                    "data.binary": x["bytes"] is None
                                }

                                if x["bytes"] is not None:
                                    attrs["data.size"] = len(x["bytes"])
                                elif x["text"] is not None:
                                    attrs["data.size"] = len(x["text"])

                                s.set_attributes(attrs)
                                return x

                            raise RuntimeError(
                                f"unknown websocket event type: {x['type']!r}"
                            )

                        @trace_function_with_span(tracer)
                        async def ws_send(s: Span, e: WebsocketSendEventT) -> None:
                            s.set_attribute("type", e["type"])

                            if e["type"] == "websocket.accept":
                                attrs: dict[str, AttributeValue] = {
                                    "data.subprotocol": str(e["subprotocol"])
                                }
                                for i, (k, v) in enumerate(e["headers"]):
                                    if v not in otel_header_whitelist:
                                        v = b""

                                    attrs[
                                        f"data.header.{i}.{k.decode('latin-1')}"
                                    ] = v.decode("latin-1")

                            if e["type"] == "websocket.close":
                                s.set_attributes(
                                    {
                                        "data.code": WebsocketStatus(e["code"]).name,
                                        "data.reason": str(e["reason"]),
                                    }
                                )

                            if e["type"] == "websocket.send":
                                attrs: dict[str, AttributeValue] = {
                                    "data.binary": e["bytes"] is None
                                }

                                if e["bytes"] is not None:
                                    attrs["data.size"] = len(e["bytes"])
                                elif e["text"] is not None:
                                    attrs["data.size"] = len(e["text"])

                                s.set_attributes(attrs)

                            await send(e)

                        return await self.scope_websocket(scope, ws_receive, ws_send)
                    finally:
                        context.detach(ctx_reset_token)

            if scope["type"] == "http":
                span_name = f"{scope['method']} {scope['path']}"
                with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER) as s:
                    new_ctx = context.set_value(http_request_span_key, s)
                    ctx_reset_token = context.attach(new_ctx)

                    try:
                        await log.info(span_name)

                        attrs = get_common_attrs(scope)
                        attrs["http.request.method"] = scope["method"]
                        s.set_attributes(attrs)

                        @trace_function_with_span(tracer)
                        async def http_receive(s: Span) -> HTTPReceiveEvent:
                            x = await receive()
                            s.set_attribute("event_type", x["type"])

                            if x["type"] == "http.request":
                                s.set_attributes(
                                    {
                                        "data.size": len(x["body"]),
                                        "data.more_body": x["more_body"],
                                    }
                                )
                                return x

                            if x["type"] == "http.disconnect":
                                return x

                            raise RuntimeError(
                                f"unknown http event type: {x['type']!r}"
                            )

                        @trace_function_with_span(tracer)
                        async def http_send(s: Span, e: HTTPSendEvent) -> None:
                            s.set_attribute("event_type", e["type"])

                            if e["type"] == "http.response.start":
                                s.set_attribute("data.status", e["status"])
                                current_http_request_span().set_attribute(
                                    "http.response.status_code", e["status"]
                                )

                            if e["type"] == "http.response.body":
                                s.set_attribute("data.size", len(e["body"]))
                                current_http_request_span().set_attribute(
                                    "http.response.body.size", len(e["body"])
                                )

                            await send(e)

                        return await self.scope_http(scope, http_receive, http_send)
                    finally:
                        context.detach(ctx_reset_token)

            raise RuntimeError(f"unsupported protocol: {scope['type']!r}")
        except Exception:
            await log.exception("Fallback exception handler:")
            raise
