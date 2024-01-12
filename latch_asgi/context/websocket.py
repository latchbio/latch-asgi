from dataclasses import dataclass
from typing import Awaitable, Callable, TypeAlias, TypeVar

from latch_o11y.o11y import AttributesDict, dict_to_attrs

from ..asgi_iface import WebsocketReceiveCallable, WebsocketScope, WebsocketSendCallable
from ..framework.websocket import WebsocketStatus, current_websocket_request_span
from . import common

T = TypeVar("T")


@dataclass
class Context(
    common.Context[WebsocketScope, WebsocketReceiveCallable, WebsocketSendCallable]
):
    def __post_init__(self):
        super().__post_init__()

        if self.auth.oauth_sub is not None:
            current_websocket_request_span().set_attribute(
                "enduser.id", self.auth.oauth_sub
            )

    def add_request_span_attrs(self, data: AttributesDict, prefix: str):
        current_websocket_request_span().set_attributes(dict_to_attrs(data, prefix))


HandlerResult = str | tuple[WebsocketStatus, str]
Handler: TypeAlias = Callable[
    [Context],
    Awaitable[HandlerResult],
]
Route: TypeAlias = Handler
