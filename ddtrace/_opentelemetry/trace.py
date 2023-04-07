from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry.context import Context as OtelContext
from opentelemetry.trace import SpanKind as OtelSpanKind
from opentelemetry.trace import Tracer as OtelTracer
from opentelemetry.trace import TracerProvider as OtelTracerProvider
from opentelemetry.trace import use_span
from opentelemetry.trace.propagation import get_current_span
from opentelemetry.trace.span import INVALID_SPAN
from opentelemetry.trace.span import Span as OtelSpan
from opentelemetry.trace.span import TraceFlags

import ddtrace
from ddtrace._opentelemetry.span import Span
from ddtrace.internal.constants import W3C_TRACESTATE_KEY
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Iterator
    from typing import Mapping
    from typing import Optional
    from typing import Sequence
    from typing import Union

    from opentelemetry.trace import Link as OtelLink
    from opentelemetry.util.types import AttributeValue as OtelAttributeValue

    from ddtrace import Tracer as DDTracer
    from ddtrace.span import _MetaDictType


log = get_logger(__name__)


class TracerProvider(OtelTracerProvider):
    """
    Entry point of the OpenTelemetry API and provides access to OpenTelemetry compatible Tracers.
    One TracerProvider should be initialized and set per application.
    """

    def __init__(self) -> None:
        self._ddtracer = ddtrace.tracer
        super().__init__()

    def get_tracer(
        self,
        instrumenting_module_name,
        instrumenting_library_version=None,
        schema_url=None,
    ):
        # type: (str, Optional[str], Optional[str]) -> OtelTracer
        """Returns an opentelmetry compatible Tracer."""
        return Tracer(self._ddtracer)


class Tracer(OtelTracer):
    """Starts and/or activates Open Telemetry compatible Spans using the global Datadog Tracer."""

    def __init__(self, datadog_tracer):
        # type: (DDTracer) -> None
        self._tracer = datadog_tracer
        super(Tracer, self).__init__()

    def start_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[OtelContext]
        kind=OtelSpanKind.INTERNAL,  # type: OtelSpanKind
        attributes=None,  # type: Optional[Mapping[str, OtelAttributeValue]]
        links=None,  # type: Optional[Sequence[OtelLink]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
    ):
        # type: (...) -> OtelSpan
        """Creates and starts an opentelemetry span."""
        # Get active otel span
        curr_otel_span = get_current_span(context)
        if curr_otel_span is INVALID_SPAN:
            # There is no active datadog/otel span
            dd_active = None  # type: Optional[Union[ddtrace.context.Context, ddtrace.Span]]
        elif isinstance(curr_otel_span, Span):
            # Get underlying ddtrace span from the active otel span
            dd_active = curr_otel_span._ddspan
        elif isinstance(curr_otel_span, OtelSpan):
            # Otel span was not generated by the ddtrace library and does not have an underlying ddtrace span.
            # Convert otel span to a ddtrace context object.
            trace_id, span_id, _, tf, ts, _ = curr_otel_span.get_span_context()
            sp = 1 if tf is TraceFlags.SAMPLED else 0
            meta = {W3C_TRACESTATE_KEY: ts.to_header()} if ts else None  # type: Optional[_MetaDictType]
            dd_active = ddtrace.context.Context(trace_id, span_id, sampling_priority=sp, meta=meta)
        else:
            log.error(
                "Programming Error: The current active Span is not supported by ddtrace. The following span will not "
                "have a parent: %s. Please open a github issue at: https://github.com/Datadog/dd-trace-py and avoid "
                "setting the ddtrace OpenTelemetry TracerProvider.",
                curr_otel_span,
            )
        # Create a new Datadog span (not activated), then return a valid OTel span
        dd_span = self._tracer.start_span(name, child_of=dd_active, activate=False)
        return Span(
            dd_span,
            kind=kind,
            attributes=attributes,
            start_time=start_time,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )

    @contextmanager
    def start_as_current_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[OtelContext]
        kind=OtelSpanKind.INTERNAL,  # type: OtelSpanKind
        attributes=None,  # type: Optional[Mapping[str, OtelAttributeValue]]
        links=None,  # type: Optional[Sequence[OtelLink]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
        end_on_exit=True,  # type: bool
    ):
        # type: (...) -> Iterator[OtelSpan]
        """Context manager for creating and activating a new opentelemetry span."""
        # Create a new non-active OTel span wrapper
        span = self.start_span(
            name,
            context=context,
            kind=kind,
            attributes=attributes,
            links=links,
            start_time=start_time,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )

        with use_span(
            span,
            end_on_exit=end_on_exit,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        ) as span:
            yield span
