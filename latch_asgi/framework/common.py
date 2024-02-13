from typing import TypeAlias

from opentelemetry.trace import get_tracer

Headers: TypeAlias = dict[str | bytes, str | bytes]

tracer = get_tracer(__name__)

otel_header_whitelist = {
    "host",
    "content-type",
    "content-length",
    "accept",
    "accept-encoding",
    "accept-language",
    "user-agent",
    "dnt",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "sec-gpc",
    "te",
    "upgrade-insecure-requests",
    "device-memory",
    "downlink",
    "dpr",
    "ect",
    "rtt",
    "sec-ch-prefers-color-scheme",
    "sec-ch-prefers-reduced-motion",
    "sec-ch-ua",
    "sec-ch-ua-arch",
    "sec-ch-ua-full-version",
    "sec-ch-ua-mobile",
    "sec-ch-ua-model",
    "sec-ch-ua-platform",
    "sec-ch-ua-platfrom-version",
    "viewport-width",
}
