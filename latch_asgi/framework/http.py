from http import HTTPStatus
from typing import Any, Literal, Self, TypeAlias, TypeVar, cast

import orjson
from latch_data_validation.data_validation import DataValidationError, validate
from latch_o11y.o11y import trace_function, trace_function_with_span
from opentelemetry import context
from opentelemetry.trace.span import Span

from ..asgi_iface import (
    HTTPReceiveCallable,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    HTTPSendCallable,
)
from .common import Headers, tracer

T = TypeVar("T")

HTTPMethod: TypeAlias = Literal[
    "GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"
]

# >>> O11y

http_request_span_key = context.create_key("http_request_span")


def current_http_request_span() -> Span:
    return cast(Span, context.get_value(http_request_span_key))


# >>> Error classes


class HTTPErrorResponse(RuntimeError):
    def __init__(
        self: Self, status: HTTPStatus, data: Any, *, headers: Headers | None = None
    ) -> None:
        if headers is None:
            headers = {}

        self.status = status
        self.data = data
        self.headers = headers


class HTTPInternalServerError(HTTPErrorResponse):
    def __init__(self: Self, data: Any, *, headers: Headers | None = None) -> None:
        super().__init__(HTTPStatus.INTERNAL_SERVER_ERROR, data, headers=headers)


class HTTPBadRequest(HTTPErrorResponse):
    def __init__(self: Self, data: Any, *, headers: Headers | None = None) -> None:
        super().__init__(HTTPStatus.BAD_REQUEST, data, headers=headers)


class HTTPForbidden(HTTPErrorResponse):
    def __init__(self: Self, data: Any, *, headers: Headers | None = None) -> None:
        super().__init__(HTTPStatus.FORBIDDEN, data, headers=headers)


class HTTPConnectionClosedError(RuntimeError):
    ...


# >>>
# I/O
# >>>

# todo(maximsmol): add max body length limit by default

# >>> Receive


@trace_function(tracer)
async def receive_data(receive: HTTPReceiveCallable) -> bytes:
    res = b""
    more_body = True
    while more_body:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            raise HTTPConnectionClosedError

        res += msg["body"]
        more_body = msg["more_body"]

    # todo(maximsmol): accumulate instead of overriding
    # todo(maximsmol): probably use the content-length header if present?
    current_http_request_span().set_attribute("http.request.body.size", len(res))

    return res


@trace_function(tracer)
async def receive_json(receive: HTTPReceiveCallable) -> Any:
    return orjson.loads(await receive_data(receive))


async def receive_class_ext(
    receive: HTTPReceiveCallable, cls: type[T]
) -> tuple[Any, T]:
    data = await receive_json(receive)

    try:
        return data, validate(data, cls)
    except DataValidationError as e:
        raise HTTPBadRequest(e.json()) from None


@trace_function(tracer)
async def receive_class(receive: HTTPReceiveCallable, cls: type[T]) -> T:
    return (await receive_class_ext(receive, cls))[1]


# >>> Send


@trace_function(tracer)
async def send_http_data(
    send: HTTPSendCallable,
    status: HTTPStatus,
    data: str | bytes,
    /,
    *,
    content_type: str | bytes | None = b"text/plain",
    headers: Headers | None = None,
) -> None:
    if headers is None:
        headers = {}

    if isinstance(data, str):
        data = data.encode("utf-8")

    headers_to_send: list[tuple[bytes, bytes]] = [
        (b"Content-Length", str(len(data)).encode("latin-1"))
    ]
    for k, v in headers.items():
        if isinstance(k, str):
            k = k.encode("latin-1")
        if isinstance(v, str):
            v = v.encode("latin-1")
        headers_to_send.append((k, v))

    if content_type is not None:
        if isinstance(content_type, str):
            content_type = content_type.encode("latin-1")
        headers_to_send.append((b"Content-Type", content_type))

    await send(
        HTTPResponseStartEvent(
            type="http.response.start", status=status, headers=headers_to_send
        )
    )
    await send(
        HTTPResponseBodyEvent(type="http.response.body", body=data, more_body=False)
    )


@trace_function(tracer)
async def send_json(
    send: HTTPSendCallable,
    status: HTTPStatus,
    data: Any,
    /,
    *,
    content_type: str = "application/json",
    headers: Headers | None = None,
) -> None:
    return await send_http_data(
        send, status, orjson.dumps(data), content_type=content_type, headers=headers
    )


@trace_function(tracer)
async def send_auto(
    send: HTTPSendCallable,
    status: HTTPStatus,
    data: str | bytes | Any,
    /,
    *,
    headers: Headers | None = None,
) -> None:
    if isinstance(data, str | bytes):
        return await send_http_data(send, status, data, headers=headers)

    return await send_json(send, status, data, headers=headers)
