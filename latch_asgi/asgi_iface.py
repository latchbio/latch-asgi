from collections.abc import Awaitable, Callable
from typing import TypeAlias

from hypercorn.typing import (
    HTTPDisconnectEvent,
    HTTPRequestEvent,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    HTTPServerPushEvent,
    LifespanShutdownCompleteEvent,
    LifespanShutdownEvent,
    LifespanShutdownFailedEvent,
    LifespanStartupCompleteEvent,
    LifespanStartupEvent,
    LifespanStartupFailedEvent,
    WebsocketAcceptEvent,
    WebsocketCloseEvent,
    WebsocketConnectEvent,
    WebsocketDisconnectEvent,
    WebsocketReceiveEvent,
    WebsocketResponseBodyEvent,
    WebsocketResponseStartEvent,
    WebsocketSendEvent,
)

# >>> Lifespan
LifespanReceiveEvent: TypeAlias = LifespanStartupEvent | LifespanShutdownEvent
LifespanReceiveCallable: TypeAlias = Callable[[], Awaitable[LifespanReceiveEvent]]

LifespanShutdownSendEvent: TypeAlias = (
    LifespanShutdownCompleteEvent | LifespanShutdownFailedEvent
)
LifespanStartupSendEvent: TypeAlias = (
    LifespanStartupCompleteEvent | LifespanStartupFailedEvent
)

LifespanSendEvent: TypeAlias = LifespanStartupSendEvent | LifespanShutdownSendEvent
LifespanSendCallable: TypeAlias = Callable[[LifespanSendEvent], Awaitable[None]]

# >>> HTTP

HTTPReceiveEvent: TypeAlias = HTTPRequestEvent | HTTPDisconnectEvent
HTTPReceiveCallable: TypeAlias = Callable[[], Awaitable[HTTPReceiveEvent]]

HTTPSendEvent: TypeAlias = (
    HTTPResponseStartEvent
    | HTTPResponseBodyEvent
    | HTTPServerPushEvent
    | HTTPDisconnectEvent
)
HTTPSendCallable: TypeAlias = Callable[[HTTPSendEvent], Awaitable[None]]

# >>> Websocket

WebsocketReceiveEventT: TypeAlias = (
    WebsocketConnectEvent | WebsocketReceiveEvent | WebsocketDisconnectEvent
)
WebsocketReceiveCallable: TypeAlias = Callable[[], Awaitable[WebsocketReceiveEventT]]

WebsocketSendEventT: TypeAlias = (
    WebsocketAcceptEvent
    | WebsocketSendEvent
    | WebsocketResponseBodyEvent
    | WebsocketResponseStartEvent
    | WebsocketCloseEvent
)
WebsocketSendCallable: TypeAlias = Callable[[WebsocketSendEventT], Awaitable[None]]

# >>> WWW

WWWReceiveCallable = HTTPReceiveCallable | WebsocketReceiveCallable
WWWSendCallable = HTTPSendCallable | WebsocketSendCallable
