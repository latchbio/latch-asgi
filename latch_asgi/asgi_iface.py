import dataclasses
from dataclasses import dataclass
from typing import (
    Awaitable,
    Callable,
    Iterable,
    Literal,
    TypeAlias,
    get_args,
    get_origin,
)

import hypercorn.typing as htyping
from latch_data_validation.data_validation import validate


def type_str(x: type) -> str:
    for f in dataclasses.fields(x):
        if f.name != "type":
            continue

        o = get_origin(f.type)
        if o is not Literal:
            raise ValueError("'type' field type is not a Literal")

        res = get_args(f.type)[0]
        if not isinstance(res, str):
            raise ValueError("'type' field Literal is not a string")

        return res

    raise ValueError("'type' field not found")


@dataclass(frozen=True)
class ASGIVersions:
    spec_version: str
    version: Literal["2.0"] | Literal["3.0"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.ASGIVersions)


# >>> Lifespan
@dataclass(frozen=True)
class LifespanScope:
    type: Literal["lifespan"]
    asgi: ASGIVersions

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanScope)


@dataclass(frozen=True)
class LifespanStartupEvent:
    type: Literal["lifespan.startup"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanStartupEvent)


@dataclass(frozen=True)
class LifespanShutdownEvent:
    type: Literal["lifespan.shutdown"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanShutdownEvent)


LifespanReceiveEvent: TypeAlias = LifespanStartupEvent | LifespanShutdownEvent
LifespanReceiveCallable: TypeAlias = Callable[[], Awaitable[LifespanReceiveEvent]]


@dataclass(frozen=True)
class LifespanStartupCompleteEvent:
    type: Literal["lifespan.startup.complete"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanStartupCompleteEvent)


@dataclass(frozen=True)
class LifespanStartupFailedEvent:
    type: Literal["lifespan.startup.failed"]
    message: str

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanStartupFailedEvent)


LifespanStartupSendEvent: TypeAlias = (
    LifespanStartupCompleteEvent | LifespanStartupFailedEvent
)


@dataclass(frozen=True)
class LifespanShutdownCompleteEvent:
    type: Literal["lifespan.shutdown.complete"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanShutdownCompleteEvent)


@dataclass(frozen=True)
class LifespanShutdownFailedEvent:
    type: Literal["lifespan.shutdown.failed"]
    message: str

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.LifespanShutdownFailedEvent)


LifespanShutdownSendEvent: TypeAlias = (
    LifespanShutdownCompleteEvent | LifespanShutdownFailedEvent
)

LifespanSendEvent: TypeAlias = LifespanStartupSendEvent | LifespanShutdownSendEvent
LifespanSendCallable: TypeAlias = Callable[[LifespanSendEvent], Awaitable[None]]


# >>> HTTP
@dataclass(frozen=True)
class HTTPScope:
    type: Literal["http"]
    asgi: ASGIVersions
    http_version: str
    method: str
    scheme: str
    path: str
    raw_path: bytes
    query_string: bytes
    root_path: str
    headers: Iterable[tuple[bytes, bytes]]
    client: tuple[str, int] | None
    server: tuple[str, int | None] | None
    extensions: dict[str, dict]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.HTTPScope)


@dataclass(frozen=True)
class HTTPRequestEvent:
    type: Literal["http.request"]
    body: bytes
    more_body: bool

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.HTTPRequestEvent)


@dataclass(frozen=True)
class HTTPDisconnectEvent:
    type: Literal["http.disconnect"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.HTTPDisconnectEvent)


HTTPReceiveEvent: TypeAlias = HTTPRequestEvent | HTTPDisconnectEvent
HTTPReceiveCallable: TypeAlias = Callable[[], Awaitable[HTTPReceiveEvent]]


@dataclass(frozen=True)
class HTTPResponseStartEvent:
    type: Literal["http.response.start"]
    status: int
    headers: Iterable[tuple[bytes, bytes]]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.HTTPResponseStartEvent)


@dataclass(frozen=True)
class HTTPResponseBodyEvent:
    type: Literal["http.response.body"]
    body: bytes
    more_body: bool

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.HTTPResponseBodyEvent)


@dataclass(frozen=True)
class HTTPServerPushEvent:
    type: Literal["http.response.push"]
    path: str
    headers: Iterable[tuple[bytes, bytes]]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.HTTPServerPushEvent)


HTTPSendEvent: TypeAlias = (
    HTTPResponseStartEvent
    | HTTPResponseBodyEvent
    | HTTPServerPushEvent
    | HTTPDisconnectEvent
)
HTTPSendCallable: TypeAlias = Callable[[HTTPSendEvent], Awaitable[None]]


# >>> Websocket
@dataclass(frozen=True)
class WebsocketScope:
    type: Literal["websocket"]
    asgi: ASGIVersions
    http_version: str
    scheme: str
    path: str
    raw_path: bytes
    query_string: bytes
    root_path: str
    headers: Iterable[tuple[bytes, bytes]]
    client: tuple[str, int] | None
    server: tuple[str, int | None] | None
    subprotocols: Iterable[str]
    extensions: dict[str, object]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketScope)


@dataclass(frozen=True)
class WebsocketConnectEvent:
    type: Literal["websocket.connect"]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketConnectEvent)


@dataclass(frozen=True)
class WebsocketAcceptEvent:
    type: Literal["websocket.accept"]
    subprotocol: str | None
    headers: Iterable[tuple[bytes, bytes]]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketAcceptEvent)


@dataclass(frozen=True)
class WebsocketReceiveEvent:
    type: Literal["websocket.receive"]
    bytes: bytes | None
    text: str | None

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketReceiveEvent)


@dataclass(frozen=True)
class WebsocketSendEvent:
    type: Literal["websocket.send"]
    bytes: bytes | None
    text: str | None

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketSendEvent)


@dataclass(frozen=True)
class WebsocketResponseStartEvent:
    type: Literal["websocket.http.response.start"]
    status: int
    headers: Iterable[tuple[bytes, bytes]]

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketResponseStartEvent)


@dataclass(frozen=True)
class WebsocketResponseBodyEvent:
    type: Literal["websocket.http.response.body"]
    body: bytes
    more_body: bool

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketResponseBodyEvent)


@dataclass(frozen=True)
class WebsocketDisconnectEvent:
    type: Literal["websocket.disconnect"]
    code: int

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketDisconnectEvent)


@dataclass(frozen=True)
class WebsocketCloseEvent:
    type: Literal["websocket.close"]
    code: int
    reason: str | None

    def as_dict(self):
        return validate(dataclasses.asdict(self), htyping.WebsocketCloseEvent)


WebsocketSendEventT: TypeAlias = (
    WebsocketAcceptEvent
    | WebsocketSendEvent
    | WebsocketResponseBodyEvent
    | WebsocketResponseStartEvent
    | WebsocketCloseEvent
)
WebsocketSendCallable: TypeAlias = Callable[[WebsocketSendEventT], Awaitable[None]]


WebsocketReceiveEventT: TypeAlias = (
    WebsocketConnectEvent | WebsocketReceiveEvent | WebsocketDisconnectEvent
)
WebsocketReceiveCallable: TypeAlias = Callable[[], Awaitable[WebsocketReceiveEventT]]


WWWScope: TypeAlias = HTTPScope | WebsocketScope
Scope: TypeAlias = HTTPScope | WebsocketScope | LifespanScope

WWWSendCallable: TypeAlias = HTTPSendCallable | WebsocketSendCallable
SendCallable: TypeAlias = (
    HTTPSendCallable | WebsocketSendCallable | LifespanSendCallable
)

WWWReceiveCallable: TypeAlias = HTTPReceiveCallable | WebsocketReceiveCallable
ReceiveCallable: TypeAlias = (
    HTTPReceiveCallable | WebsocketReceiveCallable | LifespanReceiveCallable
)
