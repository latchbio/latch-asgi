from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeAlias, TypeVar

from latch_o11y.o11y import AttributesDict, dict_to_attrs, trace_app_function

from ..asgi_iface import HTTPReceiveCallable, HTTPScope, HTTPSendCallable
from ..framework.http import HTTPMethod, current_http_request_span, receive_class_ext
from . import common

T = TypeVar("T")


@dataclass
class Context(common.Context[HTTPScope, HTTPReceiveCallable, HTTPSendCallable]):
    def __post_init__(self):
        super().__post_init__()

        if self.auth.oauth_sub is not None:
            current_http_request_span().set_attribute("enduser.id", self.auth.oauth_sub)

    def add_request_span_attrs(self, data: AttributesDict, prefix: str):
        current_http_request_span().set_attributes(dict_to_attrs(data, prefix))

    @trace_app_function
    async def receive_request_payload(self, cls: type[T]) -> T:
        json, res = await receive_class_ext(self.receive, cls)

        # todo(maximsmol): datadog has shit support for events
        # current_http_request_span().add_event(
        #     "request payload", dict_to_attrs(json, "data")
        # )
        self.add_request_span_attrs(json, "http.request_payload")

        return res


HandlerResult: TypeAlias = Any | None
Handler: TypeAlias = Callable[
    [Context],
    Awaitable[HandlerResult],
]
Route: TypeAlias = Handler | tuple[list[HTTPMethod], Handler]
