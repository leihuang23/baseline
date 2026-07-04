"""Stable hashing helpers for model run telemetry.

Raw prompts and outputs may contain health context, so persistence stores only
canonical hashes and non-sensitive metadata.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Serialize JSON-compatible data deterministically."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def hash_payload(payload: Any) -> str:
    """Return a SHA-256 hash for a JSON-compatible payload."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
