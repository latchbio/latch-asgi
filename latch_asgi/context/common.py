from dataclasses import dataclass, field
from typing import Generic, Self, TypeVar

from hypercorn.typing import WWWScope
from latch_o11y.o11y import (
    AttributesDict,
    app_tracer,
    dict_to_attrs,
    trace_app_function,
)
from opentelemetry.util.types import AttributeValue

from ..asgi_iface import WWWReceiveCallable, WWWSendCallable
from ..auth import Authorization, get_signer_sub
from ..framework.common import otel_header_whitelist
from ..framework.http import current_http_request_span

Scope = TypeVar("Scope", bound=WWWScope)
SendCallable = TypeVar("SendCallable", bound=WWWSendCallable)
ReceiveCallable = TypeVar("ReceiveCallable", bound=WWWReceiveCallable)


@dataclass
class Context(Generic[Scope, ReceiveCallable, SendCallable]):
    scope: Scope
    receive: ReceiveCallable
    send: SendCallable

    auth: Authorization = field(default_factory=Authorization, init=False)

    _header_cache: dict[bytes, bytes] = field(default_factory=dict, init=False)
    _db_response_idx: int = field(default=0, init=False)

    def __post_init__(self: Self) -> None:
        with app_tracer.start_as_current_span("find Authentication header"):
            auth_header = self.header_str("authorization")

        if auth_header is not None:
            self.auth = get_signer_sub(auth_header)

        if self.auth.oauth_sub is not None:
            current_http_request_span().set_attribute("enduser.id", self.auth.oauth_sub)

    def header(self: Self, x: str | bytes) -> bytes | None:
        if isinstance(x, str):
            x = x.encode("latin-1")

        if x in self._header_cache:
            return self._header_cache[x]

        for k, v in self.scope["headers"]:
            self._header_cache[k] = v
            if k == x:
                return v

        return None

    def header_str(self: Self, x: str | bytes) -> str | None:
        res = self.header(x)
        if res is None:
            return None

        return res.decode("latin-1")

    def add_request_span_attrs(self: Self, data: AttributesDict, prefix: str) -> None:
        current_http_request_span().set_attributes(dict_to_attrs(data, prefix))

    @trace_app_function
    def add_db_response(self: Self, data: AttributesDict) -> None:
        # todo(maximsmol): datadog has shit support for events
        # current_http_request_span().add_event(
        #     f"database response {self._db_response_idx}", dict_to_attrs(data, "data")
        # )
        self.add_request_span_attrs(data, f"db.response.{self._db_response_idx}")
        self._db_response_idx += 1
