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
