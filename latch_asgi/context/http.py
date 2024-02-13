from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Self, TypeAlias, TypeVar

from hypercorn.typing import HTTPScope
from latch_o11y.o11y import trace_app_function

from ..asgi_iface import HTTPReceiveCallable, HTTPSendCallable
from ..framework.http import HTTPMethod, receive_class_ext
from . import common

T = TypeVar("T")


@dataclass
class Context(common.Context[HTTPScope, HTTPReceiveCallable, HTTPSendCallable]):
    @trace_app_function
    async def receive_request_payload(self: Self, cls: type[T]) -> T:
        json, res = await receive_class_ext(self.receive, cls)

        self.add_request_span_attrs(json, "http.request.body.data")

        return res


HandlerResult: TypeAlias = Any | None
Handler: TypeAlias = Callable[[Context], Awaitable[HandlerResult]]
Route: TypeAlias = Handler | tuple[list[HTTPMethod], Handler]
