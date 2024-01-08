from enum import Enum
from typing import Any, TypeVar, cast

import opentelemetry.context as context
import simdjson
from latch_data_validation.data_validation import DataValidationError, validate
from latch_o11y.o11y import trace_function, trace_function_with_span
from opentelemetry.trace.span import Span

from ..asgi_iface import (
    WebsocketAcceptEvent,
    WebsocketCloseEvent,
    WebsocketReceiveCallable,
    WebsocketSendCallable,
    WebsocketSendEvent,
)
from .common import Headers, tracer

T = TypeVar("T")

websocket_request_span_key = context.create_key("websocket_request_span")


def current_websocket_request_span() -> Span:
    return cast(Span, context.get_value(websocket_request_span_key))


# >>> Error classes


class WebsocketStatus(int, Enum):
    normal = 1000
    """
    1000 indicates a normal closure, meaning that the purpose for
    which the connection was established has been fulfilled.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    going_away = 1001
    """
    1001 indicates that an endpoint is "going away", such as a server
    going down or a browser having navigated away from a page.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    protocol_error = 1002
    """
    1002 indicates that an endpoint is terminating the connection due
    to a protocol error.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    unsupported = 1003
    """
    1003 indicates that an endpoint is terminating the connection
    because it has received a type of data it cannot accept (e.g., an
    endpoint that understands only text data MAY send this if it
    receives a binary message).

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    reserved = 1004
    """
    Reserved.  The specific meaning might be defined in the future.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    no_status = 1005
    """
    1005 is a reserved value and MUST NOT be set as a status code in a
    Close control frame by an endpoint.  It is designated for use in
    applications expecting a status code to indicate that no status
    code was actually present.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    abnormal = 1006
    """
    1006 is a reserved value and MUST NOT be set as a status code in a
    Close control frame by an endpoint.  It is designated for use in
    applications expecting a status code to indicate that the
    connection was closed abnormally, e.g., without sending or
    receiving a Close control frame.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    unsupported_payload = 1007
    """
    1007 indicates that an endpoint is terminating the connection
    because it has received data within a message that was not
    consistent with the type of the message (e.g., non-UTF-8 [RFC3629]
    data within a text message).

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    policy_violation = 1008
    """
    1008 indicates that an endpoint is terminating the connection
    because it has received a message that violates its policy.  This
    is a generic status code that can be returned when there is no
    other more suitable status code (e.g., 1003 or 1009) or if there
    is a need to hide specific details about the policy.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    too_large = 1009
    """
    1009 indicates that an endpoint is terminating the connection
    because it has received a message that is too big for it to
    process.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
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

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    server_error = 1011
    """
    1011 indicates that a server is terminating the connection because
    it encountered an unexpected condition that prevented it from
    fulfilling the request.

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """

    tls_handshake_fail = 1015
    """
    1015 is a reserved value and MUST NOT be set as a status code in a
    Close control frame by an endpoint.  It is designated for use in
    applications expecting a status code to indicate that the
    connection was closed due to a failure to perform a TLS handshake
    (e.g., the server certificate can't be verified).

    https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4.1
    """


class WebsocketError(RuntimeError):
    def __init__(self, status: WebsocketStatus, data: Any, *, headers: Headers = {}):
        super().__init__()
        self.status = status
        self.data = data
        self.headers = headers


class WebsocketErrorResponse(WebsocketError): ...


class WebsocketConnectionClosedError(RuntimeError): ...


class WebsocketBadMessage(WebsocketErrorResponse):
    def __init__(self, data: Any, *, headers: Headers = {}):
        super().__init__(WebsocketStatus.policy_violation, data, headers=headers)


class WebsocketInternalServerError(WebsocketErrorResponse):
    def __init__(self, data: Any, *, headers: Headers = {}):
        super().__init__(WebsocketStatus.server_error, data, headers=headers)


# >>> I/O


async def receive_websocket_data(receive: WebsocketReceiveCallable):
    res = b""
    with tracer.start_as_current_span("read websocket message") as s:
        msg = await receive()

        s.set_attribute("type", msg.type)

        if msg.type == "websocket.connect":
            # todo(ayush): allow upgrades here as well?
            raise WebsocketBadMessage("connection has already been established")

        if msg.type == "websocket.disconnect":
            raise WebsocketConnectionClosedError()

        if msg.bytes is not None:
            res = msg.bytes
        elif msg.text is not None:
            res = msg.text.encode("utf-8")
        else:
            raise WebsocketBadMessage("empty message")

        s.set_attributes({"size": len(res)})
        return res


@trace_function(tracer)
async def receive_websocket_json(receive: WebsocketReceiveCallable) -> Any:
    res = await receive_websocket_data(receive)

    p = simdjson.Parser()
    try:
        return p.parse(res, True)
    except ValueError as e:
        raise WebsocketBadMessage("Failed to parse JSON") from e


@trace_function(tracer)
async def receive_websocket_class_ext(
    receive: WebsocketReceiveCallable, cls: type[T]
) -> tuple[Any, T]:
    data = await receive_websocket_json(receive)

    try:
        return data, validate(data, cls)
    except DataValidationError as e:
        raise WebsocketBadMessage(e.json()) from None


@trace_function(tracer)
async def receive_websocket_class(receive: WebsocketReceiveCallable, cls: type[T]) -> T:
    return (await receive_websocket_class_ext(receive, cls))[1]


@trace_function_with_span(tracer)
async def send_websocket_data(
    s: Span,
    send: WebsocketSendCallable,
    data: str | bytes,
):
    if isinstance(data, str):
        data = data.encode("utf-8")

    s.set_attribute("body.size", len(data))

    await send(WebsocketSendEvent(type="websocket.send", bytes=data, text=None))

    current_websocket_request_span().set_attribute(
        "websocket.sent_message_content_length", len(data)
    )


@trace_function_with_span(tracer)
async def accept_websocket_connection(
    s: Span,
    send: WebsocketSendCallable,
    receive: WebsocketReceiveCallable,
    /,
    *,
    subprotocol: str | None = None,
    headers: Headers = {},
):
    msg = await receive()
    if msg.type != "websocket.connect":
        raise WebsocketBadMessage("cannot accept connection without connection request")

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
async def close_websocket_connection(
    s: Span,
    send: WebsocketSendCallable,
    /,
    *,
    status: WebsocketStatus,
    data: str,
):
    s.set_attribute("reason", data)

    await send(WebsocketCloseEvent("websocket.close", status.value, data))

    current_websocket_request_span().set_attribute("websocket.http.close_reason", data)
