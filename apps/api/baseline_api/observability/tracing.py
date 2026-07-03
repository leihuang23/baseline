"""Trace ID propagation for requests and background jobs."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import Request, Response

TRACE_HEADER = "X-Trace-Id"

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
_user_id_hash_var: ContextVar[str | None] = ContextVar("user_id_hash", default=None)
_internal_user_id_var: ContextVar[str | None] = ContextVar("internal_user_id", default=None)


@dataclass(frozen=True)
class TraceContext:
    """Serializable context passed from requests into background jobs."""

    trace_id: str
    job_id: str | None = None
    user_id_hash: str | None = None
    internal_user_id: str | None = None


async def trace_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Generate or accept a safe trace ID and return it on every response."""

    trace_id = _safe_trace_id(request.headers.get(TRACE_HEADER))
    with use_trace_context(TraceContext(trace_id=trace_id)):
        response = await call_next(request)
    response.headers[TRACE_HEADER] = trace_id
    return response


def create_job_context(
    job_id: str | None = None,
    *,
    trace_id: str | None = None,
    user_id_hash: str | None = None,
    internal_user_id: str | None = None,
) -> TraceContext:
    """Create a background-job trace context from the active request context."""

    return TraceContext(
        trace_id=trace_id or _trace_id_var.get() or generate_trace_id(),
        job_id=job_id or _job_id_var.get(),
        user_id_hash=user_id_hash or _user_id_hash_var.get(),
        internal_user_id=internal_user_id or _internal_user_id_var.get(),
    )


def get_trace_context() -> TraceContext:
    """Return the active context, generating a trace ID if needed."""

    return create_job_context()


@contextmanager
def use_trace_context(context: TraceContext) -> Iterator[TraceContext]:
    """Bind a trace context for sync or async work executing in this context."""

    tokens = _set_context(context)
    try:
        yield context
    finally:
        _reset_context(tokens)


def generate_trace_id() -> str:
    return str(uuid4())


def _safe_trace_id(candidate: str | None) -> str:
    if candidate is None:
        return generate_trace_id()
    try:
        return str(UUID(candidate))
    except ValueError:
        return generate_trace_id()


def _set_context(
    context: TraceContext,
) -> tuple[
    Token[str | None],
    Token[str | None],
    Token[str | None],
    Token[str | None],
]:
    return (
        _trace_id_var.set(context.trace_id),
        _job_id_var.set(context.job_id),
        _user_id_hash_var.set(context.user_id_hash),
        _internal_user_id_var.set(context.internal_user_id),
    )


def _reset_context(
    tokens: tuple[
        Token[str | None],
        Token[str | None],
        Token[str | None],
        Token[str | None],
    ],
) -> None:
    trace_token, job_token, user_hash_token, internal_user_token = tokens
    _trace_id_var.reset(trace_token)
    _job_id_var.reset(job_token)
    _user_id_hash_var.reset(user_hash_token)
    _internal_user_id_var.reset(internal_user_token)
