from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Self, TypeAlias, TypeVar

from hypercorn.typing import WebsocketScope
from latch_o11y.o11y import dict_to_attrs, trace_app_function
from opentelemetry.trace import get_current_span

from ..asgi_iface import WebsocketReceiveCallable, WebsocketSendCallable
from ..framework.common import Headers
from ..framework.websocket import (
    accept_connection,
    receive_class_ext,
    send_websocket_auto,
    websocket_session_span_key,
)
from . import common

T = TypeVar("T")


@dataclass
class Context(
    common.Context[WebsocketScope, WebsocketReceiveCallable, WebsocketSendCallable]
):
    _request_span_key = websocket_session_span_key

    @trace_app_function
    async def accept_connection(
        self: Self, *, subprotocol: str | None = None, headers: Headers | None = None
    ) -> None:
        await accept_connection(self.send, subprotocol=subprotocol, headers=headers)

    @trace_app_function
    async def receive_message(self: Self, cls: type[T]) -> T:
        json, res = await receive_class_ext(self.receive, cls)

        get_current_span().set_attributes(dict_to_attrs(json, "payload"))

        return res

    @trace_app_function
    async def send_message(self: Self, data: Any) -> None:
        await send_websocket_auto(self.send, data)


HandlerResult = str
Handler: TypeAlias = Callable[[Context], Awaitable[HandlerResult]]
Route: TypeAlias = Handler
