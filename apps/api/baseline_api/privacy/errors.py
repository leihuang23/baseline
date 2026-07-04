"""Privacy service errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PrivacyError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None
