"""Curation and privacy boundary gates for the external knowledge corpus."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel
from packages.knowledge.models import (
    KnowledgeChunkPayload,
    KnowledgeSourceDocument,
    normalized_citation_urls,
)

ACCEPTED_TRUST_LEVELS = frozenset(
    {TrustLevel.peer_reviewed, TrustLevel.authoritative, TrustLevel.curated}
)

INTERNAL_PROVENANCE_KEYS = frozenset(
    {
        "applehealthrecordid",
        "applehealthsampleid",
        "checkinid",
        "dailycheckinid",
        "deriveddailyfeatureid",
        "energyscore",
        "freetextnotereference",
        "freetextnote",
        "freetextnotes",
        "healthimportbatchid",
        "healthkitsampleid",
        "healthkitidentifier",
        "hksampleid",
        "journalentry",
        "manualcheckinid",
        "manualnote",
        "manualnotes",
        "memorysummaryid",
        "modelrun",
        "modelrunid",
        "modelresponse",
        "normalizedhealthmetricid",
        "personaldata",
        "personalevidence",
        "promptmessages",
        "promptpayload",
        "prompttext",
        "rawhealthsampleid",
        "reasoningtrace",
        "reasoningtraceid",
        "sensitivenotepolicy",
        "sleepsessionid",
        "sourceplatform",
        "sourcesampleid",
        "sourcesampleids",
        "structurednotes",
        "systemprompt",
        "tokenusage",
        "usernote",
        "userprompt",
        "userid",
        "workoutsessionid",
    }
)

EXTERNAL_SOURCE_METADATA_KEYS = frozenset(
    {
        "accessedat",
        "canonicalurl",
        "citationurl",
        "citationurls",
        "doi",
        "edition",
        "governmentpublicationnumber",
        "isbn",
        "issn",
        "journal",
        "licenseurl",
        "pages",
        "publisher",
        "pubmedid",
        "volume",
    }
)

INTERNAL_PROVENANCE_TEXT_MARKERS = (
    "apple health",
    "daily check-in",
    "daily_check_in",
    "free text note",
    "free_text_note",
    "healthkit",
    "health_kit",
    "hkquantitysample",
    "hkworkout",
    "manual check-in",
    "manual_check_in",
    "model_run",
    "prompt_payload",
    "raw_health_sample",
    "reasoning_trace",
    "normalized_health_metric",
    "derived_daily_feature",
    "source_sample_ids",
    "structured_notes",
    "sensitive_note_policy",
)


class KnowledgeIngestionError(ValueError):
    """Base error for rejected corpus ingestion attempts."""


class CurationError(KnowledgeIngestionError):
    """Raised when a source fails curation or metadata requirements."""


class PersonalDataBoundaryError(KnowledgeIngestionError):
    """Raised when personal-health provenance appears in corpus input."""


def validate_document(document: KnowledgeSourceDocument) -> None:
    """Reject sources that cannot enter the curated external corpus."""

    _validate_external_boundary(document)
    missing_fields = [
        field_name
        for field_name, value in (
            ("title", document.title),
            ("author_or_org", document.author_or_org),
            ("source_type", document.source_type),
            ("url_or_identifier", document.url_or_identifier),
            ("license_status", document.license_status),
            ("published_at", document.published_at),
            ("version", document.version),
            ("content", document.content),
        )
        if _is_missing_required_metadata(value)
    ]
    if missing_fields:
        raise CurationError(f"Knowledge source missing required fields: {missing_fields}")

    source_type = _required_source_type(document.source_type)
    if document.trust_level not in ACCEPTED_TRUST_LEVELS:
        raise CurationError(
            "Knowledge sources require explicit peer_reviewed/authoritative/curated trust"
        )

    if source_type == KnowledgeSourceType.article and not normalized_citation_urls(
        document.citation_urls
    ):
        raise CurationError("Web/article sources require supporting citations")


def validate_chunk_payload(chunk: KnowledgeChunkPayload) -> None:
    """Reject direct-store chunk writes that carry personal-health provenance."""

    if _is_missing_required_metadata(chunk.text):
        raise CurationError("Knowledge chunks require non-empty external text")
    if _contains_internal_provenance_text(chunk.text):
        raise PersonalDataBoundaryError(
            "Personal-health provenance is not allowed in knowledge corpus chunks"
        )


def _validate_external_boundary(document: KnowledgeSourceDocument) -> None:
    for key in _metadata_keys(document.source_metadata):
        if _normalized_key(key) in INTERNAL_PROVENANCE_KEYS:
            raise PersonalDataBoundaryError(
                f"Personal-data metadata key is not allowed in knowledge corpus: {key}"
            )

    for key in document.source_metadata:
        if _normalized_key(str(key)) not in EXTERNAL_SOURCE_METADATA_KEYS:
            raise PersonalDataBoundaryError(
                f"Only external-source metadata keys are allowed in knowledge corpus: {key}"
            )

    if _contains_internal_provenance_text(_external_boundary_text(document)):
        raise PersonalDataBoundaryError(
            "Personal-health provenance is not allowed in knowledge corpus"
        )


def _required_source_type(source_type: object) -> KnowledgeSourceType:
    if not isinstance(source_type, KnowledgeSourceType):
        raise CurationError("Knowledge source source_type must be a KnowledgeSourceType")
    return source_type


def _contains_internal_provenance_text(text: str) -> bool:
    normalized_text = text.lower()
    compact_text = _normalized_key(text)
    return any(marker in normalized_text for marker in INTERNAL_PROVENANCE_TEXT_MARKERS) or any(
        key in compact_text for key in INTERNAL_PROVENANCE_KEYS
    )


def _external_boundary_text(document: KnowledgeSourceDocument) -> str:
    return json.dumps(
        {
            "title": document.title,
            "author_or_org": document.author_or_org,
            "source_type": document.source_type,
            "url_or_identifier": document.url_or_identifier,
            "license_status": document.license_status,
            "published_at": document.published_at,
            "version": document.version,
            "trust_level": document.trust_level,
            "citation_urls": document.citation_urls,
            "source_metadata": document.source_metadata,
            "content": document.content,
        },
        sort_keys=True,
        default=str,
    )


def _metadata_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            yield str(key)
            yield from _metadata_keys(nested_value)
    elif isinstance(value, list):
        for item in value:
            yield from _metadata_keys(item)


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def _is_missing_required_metadata(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return value is None
