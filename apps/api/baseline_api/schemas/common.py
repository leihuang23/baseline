"""Common API envelope and error contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base model for externally visible API contracts."""

    model_config = ConfigDict(extra="forbid")


class APIError(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] | None = None


class APIResponseMeta(ContractModel):
    schema_version: str = "v1"
    request_id: str | None = None
    trace_id: str | None = None
    generated_at: datetime | None = None


EnvelopeDataT = TypeVar("EnvelopeDataT")


class APIEnvelope[EnvelopeDataT](ContractModel):
    status: Literal["success", "error"]
    data: EnvelopeDataT | None = None
    error: APIError | None = None
    meta: APIResponseMeta = Field(default_factory=APIResponseMeta)


def not_implemented_envelope() -> APIEnvelope[None]:
    return APIEnvelope(
        status="error",
        error=APIError(
            code="not_implemented",
            message="This API contract is published, but endpoint behavior is not implemented yet.",
        ),
    )
