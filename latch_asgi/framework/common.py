from typing import TypeAlias

from opentelemetry.trace import get_tracer

Headers: TypeAlias = dict[str | bytes, str | bytes]

tracer = get_tracer(__name__)
