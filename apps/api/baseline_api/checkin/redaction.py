"""Local redaction/summarization boundary for free-text check-in notes.

The real summarizer model is wired in P3-04; this module exposes the interface and a
privacy-safe stub so that downstream code never passes raw notes to an external LLM
unless the user explicitly permits it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from baseline_api.db.models.enums import SensitiveNotePolicy
from baseline_api.schemas.enums import RedactionStatus


def _note_hash(note: str) -> str:
    return hashlib.sha256(note.encode("utf-8")).hexdigest()[:16]


def _local_summary(_note: str) -> str:
    """Privacy-preserving local stub summarizer.

    The real implementation (P3-04) will run an on-device or local model and return a
    condensed, non-identifying summary. The stub returns a fixed placeholder so tests can
    prove the *interface* is used and raw text is not echoed.
    """

    return "User-provided note summarized locally."


@dataclass(frozen=True)
class RedactionResult:
    """Output of redacting a free-text note."""

    reference: str | None
    summary: str | None
    status: RedactionStatus


class NoteRedactionService(Protocol):
    """Redact or summarize a free-text note according to the user's policy."""

    async def redact(
        self,
        note: str | None,
        policy: SensitiveNotePolicy,
    ) -> RedactionResult: ...


class StubNoteRedactionService:
    """Default-deny stub: never returns raw text unless the policy explicitly allows it."""

    async def redact(
        self,
        note: str | None,
        policy: SensitiveNotePolicy,
    ) -> RedactionResult:
        if not note:
            return RedactionResult(
                reference=None,
                summary=None,
                status=RedactionStatus.none,
            )

        reference = f"{policy.value}:{_note_hash(note)}"

        if policy == SensitiveNotePolicy.exclude_from_external_llm:
            return RedactionResult(
                reference=reference,
                summary=None,
                status=RedactionStatus.redacted,
            )

        if policy == SensitiveNotePolicy.summarize_before_external_llm:
            summary = _local_summary(note)
            reference = f"{policy.value}:{_note_hash(note)}:{_note_hash(summary)}"
            return RedactionResult(
                reference=reference,
                summary=summary,
                status=RedactionStatus.partial,
            )

        # allow_external_llm
        return RedactionResult(
            reference=reference,
            summary=None,
            status=RedactionStatus.none,
        )
