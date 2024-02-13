from typing import Self

from opentelemetry import trace
from opentelemetry.context.context import Context
from opentelemetry.propagators import textmap


class DDTraceContextTextMapPropagator(textmap.TextMapPropagator):
    # https://github.com/open-telemetry/opentelemetry-python-contrib/blob/934af7ea4f9b1e0294ced6a014d6eefdda156b2b/exporter/opentelemetry-exporter-datadog/src/opentelemetry/exporter/datadog/propagator.py
    def extract(
        self: Self,
        carrier: textmap.CarrierT,
        context: Context | None = None,
        getter: textmap.Getter[textmap.CarrierT] = textmap.default_getter,
    ) -> Context:
        # todo(maximsmol): warn on duplicate headers
        # todo(maximsmol): ignore invalid headers

        if context is None:
            context = Context()

        trace_id = getter.get(carrier, "x-datadog-trace-id")
        if trace_id is None or len(trace_id) == 0:
            return context

        parent_id = getter.get(carrier, "x-datadog-parent-id")
        if parent_id is None or len(parent_id) == 0:
            return context

        sampling_priority = getter.get(carrier, "x-datadog-sampling-priority")

        trace_flags = trace.TraceFlags()
        if (
            sampling_priority is not None
            and len(sampling_priority) > 0
            and int(sampling_priority[0])
            in (
                1,  # auto keep
                2,  # user keep
            )
        ):
            trace_flags = trace.TraceFlags(trace.TraceFlags.SAMPLED)

        dd_origin = getter.get(carrier, "x-datadog-origin")

        trace_state = []
        if dd_origin is not None and len(dd_origin) > 0:
            trace_state.append(("dd_origin", dd_origin[0]))

        span_context = trace.SpanContext(
            trace_id=int(trace_id[0]),
            span_id=int(parent_id[0]),
            is_remote=True,
            trace_flags=trace_flags,
            trace_state=trace.TraceState(trace_state),
        )
        return trace.set_span_in_context(trace.NonRecordingSpan(span_context), context)

    def inject(
        self: Self,
        carrier: textmap.CarrierT,
        context: Context | None = None,
        setter: textmap.Setter[textmap.CarrierT] = textmap.default_setter,
    ) -> None:
        span = trace.get_current_span(context)
        if span == trace.INVALID_SPAN:
            return

        span_context = span.get_span_context()
        if span_context == trace.INVALID_SPAN_CONTEXT:
            return

        setter.set(
            carrier,
            "x-datadog-trace-id",
            str(span_context.trace_id & 0xFFFF_FFFF_FFFF_FFFF),
        )
        setter.set(carrier, "x-datadog-parent-id", str(span_context.span_id))
        setter.set(
            carrier,
            "x-datadog-sampling-priority",
            "0"  # auto reject
            if span_context.trace_flags & trace.TraceFlags.SAMPLED == 0
            else "1",  # auto keep
        )
        if "dd_origin" in span_context.trace_state:
            setter.set(
                carrier, "x-datadog-origin", span_context.trace_state["dd_origin"]
            )

    @property
    def fields(self: Self) -> set[str]:
        return {
            "x-datadog-trace-id",
            "x-datadog-parent-id",
            "x-datadog-sampling-priority",
            "x-datadog-origin",
        }
