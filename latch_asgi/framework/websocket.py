from enum import Enum
from typing import Any, Self, TypeVar, cast

import orjson
from latch_data_validation.data_validation import DataValidationError, validate
from latch_o11y.o11y import trace_function, trace_function_with_span
from opentelemetry import context
from opentelemetry.trace.span import Span
from opentelemetry.util.types import AttributeValue

from ..asgi_iface import (
    WebsocketAcceptEvent,
    WebsocketCloseEvent,
    WebsocketReceiveCallable,
    WebsocketSendCallable,
    WebsocketSendEvent,
)
from .common import Headers, tracer

T = TypeVar("T")

websocket_session_span_key = context.create_key("websocket_message_span")


def current_websocket_session_span() -> Span:
    return cast(Span, context.get_value(websocket_session_span_key))


# >>> Error classes


class WebsocketStatus(int, Enum):
    """https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1"""

    normal = 1000
    """
    1000 indicates a normal closure, meaning that the purpose for
    which the connection was established has been fulfilled.
    """

    going_away = 1001
    """
    1001 indicates that an endpoint is "going away", such as a server
    going down or a browser having navigated away from a page.
    """

    protocol_error = 1002
    """
    1002 indicates that an endpoint is terminating the connection due
    to a protocol error.
    """

    unsupported = 1003
    """
    1003 indicates that an endpoint is terminating the connection
    because it has received a type of data it cannot accept (e.g., an
    endpoint that understands only text data MAY send this if it
    receives a binary message).
    """

    reserved = 1004
    """
    Reserved.  The specific meaning might be defined in the future.
    """

    no_status = 1005
    """
    1005 is a reserved value and MUST NOT be set as a status code in a
    Close control frame by an endpoint.  It is designated for use in
    applications expecting a status code to indicate that no status
    code was actually present.
    """

    abnormal = 1006
    """
    1006 is a reserved value and MUST NOT be set as a status code in a
    Close control frame by an endpoint.  It is designated for use in
    applications expecting a status code to indicate that the
    connection was closed abnormally, e.g., without sending or
    receiving a Close control frame.
    """

    unsupported_payload = 1007
    """
    1007 indicates that an endpoint is terminating the connection
    because it has received data within a message that was not
    consistent with the type of the message (e.g., non-UTF-8 [RFC3629]
    data within a text message).
    """

    policy_violation = 1008
    """
    1008 indicates that an endpoint is terminating the connection
    because it has received a message that violates its policy.  This
    is a generic status code that can be returned when there is no
    other more suitable status code (e.g., 1003 or 1009) or if there
    is a need to hide specific details about the policy.
    """

    too_large = 1009
    """
    1009 indicates that an endpoint is terminating the connection
    because it has received a message that is too big for it to
    process.
    """

    mandatory_extension = 1010
    """
    1010 indicates that an endpoint (client) is terminating the
    connection because it has expected the server to negotiate one or
    more extension, but the server didn't return them in the response
    message of the WebSocket handshake.  The list of extensions that
    are needed SHOULD appear in the /reason/ part of the Close frame.
    Note that this status code is not used by the server, because it
    can fail the WebSocket handshake instead.
    """

    server_error = 1011
    """
    1011 indicates that a server is terminating the connection because
    it encountered an unexpected condition that prevented it from
    fulfilling the request.
    """

    tls_handshake_fail = 1015
    """
    1015 is a reserved value and MUST NOT be set as a status code in a
    Close control frame by an endpoint.  It is designated for use in
    applications expecting a status code to indicate that the
    connection was closed due to a failure to perform a TLS handshake
    (e.g., the server certificate can't be verified).
    """


class WebsocketErrorResponse(RuntimeError):
    def __init__(self: Self, status: WebsocketStatus, data: Any) -> None:
        self.status = status
        self.data = data


class WebsocketBadMessage(WebsocketErrorResponse):
    def __init__(self: Self, data: Any) -> None:
        super().__init__(WebsocketStatus.policy_violation, data)


class WebsocketInternalServerError(WebsocketErrorResponse):
    def __init__(self: Self, data: Any) -> None:
        super().__init__(WebsocketStatus.server_error, data)


class WebsocketConnectionClosedError(RuntimeError):
    def __init__(self: Self, code: WebsocketStatus) -> None:
        self.code = code


# >>>
# I/O
# >>>

# >>> Send Lifecycle


@trace_function(tracer)
async def accept_connection(
    send: WebsocketSendCallable,
    /,
    *,
    subprotocol: str | None = None,
    headers: Headers | None = None,
) -> None:
    if headers is None:
        headers = {}

    headers_to_send: list[tuple[bytes, bytes]] = []
    for k, v in headers.items():
        if isinstance(k, str):
            k = k.encode("latin-1")
        if isinstance(v, str):
            v = v.encode("latin-1")
        headers_to_send.append((k, v))

    await send(
        WebsocketAcceptEvent(
            type="websocket.accept", subprotocol=subprotocol, headers=headers_to_send
        )
    )


@trace_function_with_span(tracer)
async def close_connection(
    s: Span, send: WebsocketSendCallable, /, *, status: WebsocketStatus, data: str
) -> None:
    s.set_attributes({"status": status.name, "reason": data})

    await send(
        WebsocketCloseEvent(type="websocket.close", code=status.value, reason=data)
    )

    current_websocket_session_span().set_attribute("websocket.close_reason", data)


# >>> Receive

# todo(maximsmol): add max message length limit by default


@trace_function(tracer)
async def receive_data(receive: WebsocketReceiveCallable) -> bytes | str:
    msg = await receive()

    if msg["type"] == "websocket.connect":
        # todo(ayush): allow upgrades here as well?
        raise ValueError("ASGI protocol violation: duplicate websocket.connect event")

    if msg["type"] == "websocket.disconnect":
        raise WebsocketConnectionClosedError(WebsocketStatus(msg["code"]))

    if msg["bytes"] is not None:
        res = msg["bytes"]
    elif msg["text"] is not None:
        res = msg["text"]
    else:
        raise WebsocketBadMessage("empty message")

    return res


@trace_function(tracer)
async def receive_json(receive: WebsocketReceiveCallable) -> Any:
    return orjson.loads(await receive_data(receive))


@trace_function(tracer)
async def receive_class_ext(
    receive: WebsocketReceiveCallable, cls: type[T]
) -> tuple[Any, T]:
    data = await receive_json(receive)

    try:
        return data, validate(data, cls)
    except DataValidationError as e:
        raise WebsocketBadMessage(e.json()) from None


@trace_function(tracer)
async def receive_websocket_class(receive: WebsocketReceiveCallable, cls: type[T]) -> T:
    return (await receive_class_ext(receive, cls))[1]


# >>> Send


@trace_function(tracer)
async def send_data(send: WebsocketSendCallable, data: str | bytes, /) -> None:
    if isinstance(data, bytes):
        await send(WebsocketSendEvent(type="websocket.send", bytes=data, text=None))
    else:
        await send(WebsocketSendEvent(type="websocket.send", bytes=None, text=data))


@trace_function(tracer)
async def send_json(send: WebsocketSendCallable, data: Any, /) -> None:
    return await send_data(send, orjson.dumps(data))


@trace_function(tracer)
async def send_websocket_auto(send: WebsocketSendCallable, data: Any, /) -> None:
    if isinstance(data, str | bytes):
        return await send_data(send, data)

    return await send_json(send, data)
