from http import HTTPStatus
from typing import Any, Literal, TypeAlias, TypeVar, cast

import opentelemetry.context as context
import simdjson
from latch_data_validation.data_validation import DataValidationError, validate
from latch_o11y.o11y import trace_function, trace_function_with_span
from opentelemetry.trace.span import Span
from orjson import dumps

from ..asgi_iface import (
    HTTPReceiveCallable,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    HTTPSendCallable,
)
from .common import Headers, tracer

T = TypeVar("T")

HTTPMethod: TypeAlias = (
    Literal["GET"]
    | Literal["HEAD"]
    | Literal["POST"]
    | Literal["PUT"]
    | Literal["DELETE"]
    | Literal["CONNECT"]
    | Literal["OPTIONS"]
    | Literal["TRACE"]
    | Literal["PATCH"]
)

# >>> O11y

http_request_span_key = context.create_key("http_request_span")


def current_http_request_span() -> Span:
    return cast(Span, context.get_value(http_request_span_key))


# >>> Error classes


class HTTPErrorResponse(RuntimeError):
    def __init__(self, status: HTTPStatus, data: Any, *, headers: Headers = {}):
        super().__init__()
        self.status = status
        self.data = data
        self.headers = headers


class HTTPInternalServerError(HTTPErrorResponse):
    def __init__(self, data: Any, *, headers: Headers = {}):
        super().__init__(HTTPStatus.INTERNAL_SERVER_ERROR, data, headers=headers)


class HTTPBadRequest(HTTPErrorResponse):
    def __init__(self, data: Any, *, headers: Headers = {}):
        super().__init__(HTTPStatus.BAD_REQUEST, data, headers=headers)


class HTTPForbidden(HTTPErrorResponse):
    def __init__(self, data: Any, *, headers: Headers = {}):
        super().__init__(HTTPStatus.FORBIDDEN, data, headers=headers)


class HTTPConnectionClosedError(RuntimeError): ...


# >>> I/O

# todo(maximsmol): add max body length limit by default


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


@trace_function(tracer)
async def receive_json(receive: HTTPReceiveCallable) -> Any:
    res = await receive_data(receive)

    p = simdjson.Parser()
    try:
        return p.parse(res, True)
    except ValueError as e:
        raise HTTPBadRequest("Failed to parse JSON") from e


async def receive_data(receive: HTTPReceiveCallable):
    res = b""
    more_body = True
    while more_body:
        with tracer.start_as_current_span("read chunk") as s:
            msg = await receive()
            if msg.type == "http.disconnect":
                raise HTTPConnectionClosedError()

            res += msg.body
            more_body = msg.more_body

            s.set_attributes({"size": len(msg.body), "more_body": more_body})

    # todo(maximsmol): accumulate instead of overriding
    # todo(maximsmol): probably use the content-length header if present?
    current_http_request_span().set_attribute("http.request_content_length", len(res))

    return res


@trace_function_with_span(tracer)
async def send_http_data(
    s: Span,
    send: HTTPSendCallable,
    status: HTTPStatus,
    data: str | bytes,
    /,
    *,
    content_type: str | bytes | None = "text/plain",
    headers: Headers = {},
):
    if isinstance(data, str):
        data = data.encode("utf-8")

    s.set_attribute("size", len(data))
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

    current_http_request_span().set_attribute("http.response_content_length", len(data))


@trace_function(tracer)
async def send_json(
    send: HTTPSendCallable,
    status: HTTPStatus,
    data: Any,
    /,
    *,
    content_type: str = "application/json",
    headers: Headers = {},
):
    return await send_http_data(
        send, status, dumps(data), content_type=content_type, headers=headers
    )


@trace_function(tracer)
async def send_auto(
    send: HTTPSendCallable,
    status: HTTPStatus,
    data: str | bytes | Any,
    /,
    *,
    headers: Headers = {},
):
    if isinstance(data, str) or isinstance(data, bytes):
        return await send_http_data(send, status, data, headers=headers)

    return await send_json(send, status, data, headers=headers)
