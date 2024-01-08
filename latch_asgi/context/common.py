from dataclasses import dataclass, field
from typing import Generic, TypeVar

from latch_o11y.o11y import AttributesDict, app_tracer, trace_app_function

from ..asgi_iface import WWWReceiveCallable, WWWScope, WWWSendCallable
from ..auth import Authorization, get_signer_sub

# todo(ayush): this sucks
Scope = TypeVar("Scope", bound=WWWScope)
Send = TypeVar("Send", bound=WWWSendCallable)
Receive = TypeVar("Receive", bound=WWWReceiveCallable)


@dataclass
class Context(Generic[Scope, Receive, Send]):
    scope: Scope
    receive: Receive
    send: Send

    auth: Authorization = field(default_factory=Authorization, init=False)

    _header_cache: dict[bytes, bytes] = field(default_factory=dict, init=False)
    _db_response_idx: int = field(default=0, init=False)

    def __post_init__(self):
        with app_tracer.start_as_current_span("find Authentication header"):
            auth_header = self.header_str("authorization")

        if auth_header is not None:
            self.auth = get_signer_sub(auth_header)

    def header(self, x: str | bytes):
        if isinstance(x, str):
            x = x.encode("utf-8")

        if x in self._header_cache:
            return self._header_cache[x]

        for k, v in self.scope.headers:
            self._header_cache[k] = v
            if k == x:
                return v

        return None

    def header_str(self, x: str | bytes):
        res = self.header(x)
        if res is None:
            return None

        return res.decode("latin-1")

    def add_request_span_attrs(self, data: AttributesDict, prefix: str):
        raise NotImplementedError()

    @trace_app_function
    def add_db_response(self, data: AttributesDict):
        # todo(maximsmol): datadog has shit support for events
        # current_http_request_span().add_event(
        #     f"database response {self._db_response_idx}", dict_to_attrs(data, "data")
        # )
        self.add_request_span_attrs(data, f"db.response.{self._db_response_idx}")
        self._db_response_idx += 1
