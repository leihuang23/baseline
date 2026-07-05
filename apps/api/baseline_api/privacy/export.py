"""Scoped encrypted data export service."""

from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.util
import hashlib
import io
import json
import secrets
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from baseline_api.db.models import (
    ConsentRecord,
    DailyAnalysisJob,
    DailyCheckIn,
    DerivedDailyFeature,
    Goal,
    HealthImportBatch,
    MemorySummary,
    ModelRun,
    NormalizedHealthMetric,
    RawHealthSample,
    ReadinessAssessment,
    ReasoningTrace,
    Recommendation,
    SleepSession,
    User,
    WorkoutSession,
)
from baseline_api.db.models.enums import AuditEventType
from baseline_api.privacy.audit import emit_privacy_audit
from baseline_api.privacy.errors import PrivacyError
from baseline_api.privacy.model_runs import (
    model_run_ids_from_payload,
    sanitize_model_input_metadata,
    sanitize_model_safety_result,
)
from baseline_api.privacy.user import get_single_user
from baseline_api.schemas.api import DataExportRequest, DataExportResponse
from baseline_api.schemas.enums import DataExportFormat, DataExportScope, DataExportStatus

DEFAULT_EXPORT_RETENTION_HOURS = 1
EXPORT_MAGIC = b"BASELINE-EXPORT-AES256GCM-V1"
AES_GCM_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
AES_GCM_TAG_BYTES = 16
EVP_CTRL_GCM_SET_IVLEN = 0x9
EVP_CTRL_GCM_GET_TAG = 0x10
EVP_CTRL_GCM_SET_TAG = 0x11
_LIBCRYPTO: Any | None = None


@dataclass(frozen=True)
class StoredExport:
    job_id: UUID
    user_id: UUID
    path: Path
    key: bytes | None
    expires_at: datetime
    content_type: str
    file_sha256: str


class LocalExportStore:
    """Filesystem-backed encrypted export store with expiring metadata."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        retention_hours: int = DEFAULT_EXPORT_RETENTION_HOURS,
    ) -> None:
        self._root = root or Path(tempfile.gettempdir()) / "baseline_exports"
        self._ttl = timedelta(hours=retention_hours)
        self._root.mkdir(parents=True, exist_ok=True)
        self._exports: dict[UUID, StoredExport] = {}

    def create(
        self,
        plaintext: bytes,
        *,
        user_id: UUID,
        now: datetime | None = None,
    ) -> StoredExport:
        created_at = now or datetime.now(UTC)
        job_id = uuid4()
        key = secrets.token_bytes(AES_GCM_KEY_BYTES)
        encrypted = encrypt_bytes(plaintext, key)
        path = self._encrypted_path(job_id)
        path.write_bytes(encrypted)
        file_sha256 = hashlib.sha256(encrypted).hexdigest()
        stored = StoredExport(
            job_id=job_id,
            user_id=user_id,
            path=path,
            key=key,
            expires_at=created_at + self._ttl,
            content_type="application/octet-stream",
            file_sha256=file_sha256,
        )
        self._write_manifest(stored)
        self._exports[job_id] = stored
        return stored

    def get(self, job_id: UUID, *, now: datetime | None = None) -> StoredExport:
        stored = self._exports.get(job_id) or self._load_manifest(job_id)
        if stored is None:
            raise PrivacyError(
                code="export_not_found",
                message="Export job not found.",
                status_code=404,
            )
        if (now or datetime.now(UTC)) >= stored.expires_at:
            self._exports.pop(job_id, None)
            self._remove_files(stored)
            raise PrivacyError(
                code="export_expired",
                message="Export link has expired.",
                status_code=410,
            )
        return stored

    def read_encrypted(self, job_id: UUID) -> bytes:
        return self.get(job_id).path.read_bytes()

    def decrypt(self, job_id: UUID) -> bytes:
        stored = self.get(job_id)
        if stored.key is None:
            raise ValueError("Export decryption key is not available in this process.")
        return decrypt_bytes(stored.path.read_bytes(), stored.key)

    def purge_user(self, user_id: UUID) -> int:
        matching_job_ids = {
            job_id for job_id, stored in self._exports.items() if stored.user_id == user_id
        }
        for manifest_path in self._root.glob("*.export.json"):
            manifest = self._read_manifest(manifest_path)
            if manifest is None or manifest.get("user_id") != str(user_id):
                continue
            job_id = _job_id_from_manifest_path(manifest_path)
            if job_id is not None:
                matching_job_ids.add(job_id)
        for job_id in matching_job_ids:
            stored = self._exports.pop(job_id, None) or self._load_manifest(job_id)
            if stored is not None:
                self._remove_files(stored)
        return len(matching_job_ids)

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        removed = 0
        job_ids = set(self._exports)
        for manifest_path in self._root.glob("*.export.json"):
            job_id = _job_id_from_manifest_path(manifest_path)
            if job_id is not None:
                job_ids.add(job_id)
        for job_id in job_ids:
            stored = self._exports.get(job_id) or self._load_manifest(job_id)
            if stored is None or current_time < stored.expires_at:
                continue
            self._exports.pop(job_id, None)
            self._remove_files(stored)
            removed += 1
        return removed

    def _encrypted_path(self, job_id: UUID) -> Path:
        return self._root / f"{job_id}.export.enc"

    def _manifest_path(self, job_id: UUID) -> Path:
        return self._root / f"{job_id}.export.json"

    def _write_manifest(self, stored: StoredExport) -> None:
        self._manifest_path(stored.job_id).write_text(
            json.dumps(
                {
                    "schema_version": "v1",
                    "job_id": str(stored.job_id),
                    "user_id": str(stored.user_id),
                    "expires_at": stored.expires_at.isoformat(),
                    "content_type": stored.content_type,
                    "file_sha256": stored.file_sha256,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _load_manifest(self, job_id: UUID) -> StoredExport | None:
        manifest_path = self._manifest_path(job_id)
        manifest = self._read_manifest(manifest_path)
        if manifest is None:
            return None
        path = self._encrypted_path(job_id)
        if not path.exists():
            manifest_path.unlink(missing_ok=True)
            return None
        expires_at = _parse_manifest_datetime(manifest.get("expires_at"))
        if expires_at is None:
            manifest_path.unlink(missing_ok=True)
            return None
        try:
            user_id = UUID(str(manifest["user_id"]))
        except ValueError:
            manifest_path.unlink(missing_ok=True)
            return None
        stored = StoredExport(
            job_id=job_id,
            user_id=user_id,
            path=path,
            key=None,
            expires_at=expires_at,
            content_type=str(manifest["content_type"]),
            file_sha256=str(manifest["file_sha256"]),
        )
        self._exports[job_id] = stored
        return stored

    def _read_manifest(self, manifest_path: Path) -> dict[str, Any] | None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict):
            return None
        required = {"job_id", "user_id", "expires_at", "content_type", "file_sha256"}
        if not required.issubset(manifest):
            return None
        return cast(dict[str, Any], manifest)

    def _remove_files(self, stored: StoredExport) -> None:
        stored.path.unlink(missing_ok=True)
        self._manifest_path(stored.job_id).unlink(missing_ok=True)


class DataExportService:
    """Create encrypted exports for the current MVP user."""

    def __init__(self, session: Session, store: LocalExportStore) -> None:
        self._session = session
        self._store = store

    def create_export(self, request: DataExportRequest) -> DataExportResponse:
        user = get_single_user(self._session)
        payload = self._payload(user, request)
        plaintext = _payload_bytes(payload, request.format)
        stored = self._store.create(plaintext, user_id=user.id)
        emit_privacy_audit(
            self._session,
            event_type=AuditEventType.data_export_requested,
            user_id=user.id,
            metadata={
                "export_job_id": str(stored.job_id),
                "export_scope": request.export_scope.value,
                "format": request.format.value,
                "include_raw_data": request.include_raw_data,
                "include_model_traces": request.include_model_traces,
                "expires_at": stored.expires_at.isoformat(),
            },
        )
        self._session.commit()
        return DataExportResponse(
            export_job_id=stored.job_id,
            status=DataExportStatus.ready,
            expires_at=stored.expires_at,
            download_url=f"/v1/data/export/{stored.job_id}/file",
            encryption={
                "algorithm": "AES-256-GCM",
                "key_base64": base64.b64encode(cast(bytes, stored.key)).decode("ascii"),
                "key_custody": "client_response",
                "file_sha256": stored.file_sha256,
            },
        )

    def _payload(self, user: User, request: DataExportRequest) -> dict[str, Any]:
        sections: dict[str, list[dict[str, Any]]] = {}
        scopes = _resolved_scopes(request.export_scope)
        if DataExportScope.consent in scopes:
            sections["consent_records"] = self._rows(ConsentRecord, user.id)
        if DataExportScope.health in scopes:
            sections.update(self._health_sections(user.id, include_raw=request.include_raw_data))
        if DataExportScope.checkins in scopes:
            sections["daily_check_ins"] = self._rows(DailyCheckIn, user.id)
        if DataExportScope.memory in scopes:
            sections["memory_summaries"] = self._rows(MemorySummary, user.id)
        if request.export_scope == DataExportScope.all:
            sections["goals"] = self._rows(Goal, user.id)
        if DataExportScope.briefings in scopes:
            sections["daily_analysis_jobs"] = self._rows(DailyAnalysisJob, user.id)
            sections["readiness_assessments"] = self._rows(ReadinessAssessment, user.id)
            if request.export_scope == DataExportScope.all:
                sections["reasoning_traces"] = self._rows(ReasoningTrace, user.id)
            else:
                sections["reasoning_traces"] = [
                    _row_dict(trace) for trace in self._briefing_reasoning_traces(user.id)
                ]
        if DataExportScope.recommendations in scopes:
            sections["recommendations"] = self._rows(Recommendation, user.id)
        if request.include_model_traces:
            model_run_ids = None
            if request.export_scope != DataExportScope.all:
                model_run_ids = self._model_run_ids_for_scopes(user.id, scopes)
            sections["model_runs"] = self._model_run_rows(user.id, model_run_ids)

        return {
            "schema_version": "v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "export_scope": request.export_scope.value,
            "format": request.format.value,
            "include_raw_data": request.include_raw_data,
            "include_model_traces": request.include_model_traces,
            "sections": sections,
        }

    def _health_sections(
        self,
        user_id: UUID,
        *,
        include_raw: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        sections = {
            "health_import_batches": self._rows(HealthImportBatch, user_id),
            "normalized_health_metrics": self._rows(NormalizedHealthMetric, user_id),
            "derived_daily_features": self._rows(DerivedDailyFeature, user_id),
            "workout_sessions": self._rows(WorkoutSession, user_id),
            "sleep_sessions": self._rows(SleepSession, user_id),
        }
        if include_raw:
            sections["raw_health_samples"] = self._rows(RawHealthSample, user_id)
        return sections

    def _model_run_rows(
        self,
        user_id: UUID,
        model_run_ids: list[UUID] | None,
    ) -> list[dict[str, Any]]:
        if model_run_ids == []:
            return []
        statement = select(ModelRun).where(ModelRun.user_id == user_id)
        if model_run_ids is not None:
            statement = statement.where(col(ModelRun.id).in_(model_run_ids))
        rows = self._session.exec(statement.order_by(col(ModelRun.created_at).desc())).all()
        return [
            {
                "id": str(row.id),
                "created_at": row.created_at.isoformat(),
                "run_type": row.run_type.value,
                "model_provider": row.model_provider,
                "model_name": row.model_name,
                "prompt_version": row.prompt_version,
                "input_hash": row.input_hash,
                "output_hash": row.output_hash,
                "schema_version": row.schema_version,
                "token_usage": row.token_usage,
                "cost": row.cost,
                "latency_ms": row.latency_ms,
                "safety_result": sanitize_model_safety_result(row.safety_result),
                "input_metadata": sanitize_model_input_metadata(row.input_metadata),
            }
            for row in rows
        ]

    def _model_run_ids_for_scopes(
        self,
        user_id: UUID,
        scopes: set[DataExportScope],
    ) -> list[UUID]:
        direct_model_run_ids: list[UUID | None] = []
        trace_ids: list[UUID | None] = []
        traces: list[ReasoningTrace] = []

        if DataExportScope.checkins in scopes:
            checkin_dates = [
                row.date
                for row in self._session.exec(
                    select(DailyCheckIn).where(DailyCheckIn.user_id == user_id)
                ).all()
            ]
            if checkin_dates:
                jobs = self._rows_for_dates(DailyAnalysisJob, user_id, checkin_dates)
                recommendations = self._rows_for_dates(Recommendation, user_id, checkin_dates)
                assessments = self._rows_for_dates(ReadinessAssessment, user_id, checkin_dates)
                trace_ids.extend(job.reasoning_trace_id for job in jobs)
                trace_ids.extend(
                    recommendation.reasoning_trace_id for recommendation in recommendations
                )
                trace_ids.extend(assessment.reasoning_trace_id for assessment in assessments)
                direct_model_run_ids.extend(
                    recommendation.model_run_id for recommendation in recommendations
                )

        if DataExportScope.briefings in scopes:
            briefing_jobs = list(
                self._session.exec(
                    select(DailyAnalysisJob).where(DailyAnalysisJob.user_id == user_id)
                ).all()
            )
            briefing_assessments = list(
                self._session.exec(
                    select(ReadinessAssessment).where(ReadinessAssessment.user_id == user_id)
                ).all()
            )
            trace_ids.extend(job.reasoning_trace_id for job in briefing_jobs)
            trace_ids.extend(assessment.reasoning_trace_id for assessment in briefing_assessments)

        if DataExportScope.recommendations in scopes:
            recommendation_rows = list(
                self._session.exec(
                    select(Recommendation).where(Recommendation.user_id == user_id)
                ).all()
            )
            direct_model_run_ids.extend(
                recommendation.model_run_id for recommendation in recommendation_rows
            )
            trace_ids.extend(
                recommendation.reasoning_trace_id for recommendation in recommendation_rows
            )

        traces.extend(self._traces_by_ids(user_id, _unique_ids(trace_ids)))
        trace_model_run_ids = [
            model_run_id
            for trace in traces
            for model_run_id in model_run_ids_from_payload(trace.trace_payload)
        ]
        return _unique_ids([*direct_model_run_ids, *trace_model_run_ids])

    def _rows_for_dates(
        self,
        model: type[Any],
        user_id: UUID,
        dates: list[Any],
    ) -> list[Any]:
        return list(
            self._session.exec(
                select(model).where(model.user_id == user_id, col(model.date).in_(dates))
            ).all()
        )

    def _traces_by_ids(self, user_id: UUID, trace_ids: list[UUID]) -> list[ReasoningTrace]:
        return [
            trace
            for trace_id in trace_ids
            if (trace := self._session.get(ReasoningTrace, trace_id)) is not None
            and trace.user_id == user_id
        ]

    def _briefing_reasoning_traces(self, user_id: UUID) -> list[ReasoningTrace]:
        jobs = list(
            self._session.exec(
                select(DailyAnalysisJob).where(DailyAnalysisJob.user_id == user_id)
            ).all()
        )
        assessments = list(
            self._session.exec(
                select(ReadinessAssessment).where(ReadinessAssessment.user_id == user_id)
            ).all()
        )
        return self._traces_by_ids(
            user_id,
            _unique_ids(
                [
                    *[job.reasoning_trace_id for job in jobs],
                    *[assessment.reasoning_trace_id for assessment in assessments],
                ]
            ),
        )

    def _rows(self, model: type[Any], user_id: UUID) -> list[dict[str, Any]]:
        rows = self._session.exec(
            select(model).where(model.user_id == user_id).order_by(col(model.created_at))
        ).all()
        return [_row_dict(row) for row in rows]


def encrypt_bytes(plaintext: bytes, key: bytes) -> bytes:
    if len(key) != AES_GCM_KEY_BYTES:
        raise ValueError("Export encryption key must be 32 bytes.")
    nonce = secrets.token_bytes(AES_GCM_NONCE_BYTES)
    ciphertext, tag = _aes_256_gcm_encrypt(plaintext, key=key, nonce=nonce, aad=EXPORT_MAGIC)
    return EXPORT_MAGIC + nonce + tag + ciphertext


def decrypt_bytes(encrypted: bytes, key: bytes) -> bytes:
    if not encrypted.startswith(EXPORT_MAGIC):
        raise ValueError("Invalid export file header.")
    if len(key) != AES_GCM_KEY_BYTES:
        raise ValueError("Export encryption key must be 32 bytes.")
    offset = len(EXPORT_MAGIC)
    min_size = offset + AES_GCM_NONCE_BYTES + AES_GCM_TAG_BYTES
    if len(encrypted) < min_size:
        raise ValueError("Invalid export file header.")
    nonce = encrypted[offset : offset + AES_GCM_NONCE_BYTES]
    tag_start = offset + AES_GCM_NONCE_BYTES
    tag = encrypted[tag_start : tag_start + AES_GCM_TAG_BYTES]
    ciphertext = encrypted[tag_start + AES_GCM_TAG_BYTES :]
    return _aes_256_gcm_decrypt(ciphertext, key=key, nonce=nonce, tag=tag, aad=EXPORT_MAGIC)


def _aes_256_gcm_encrypt(
    plaintext: bytes,
    *,
    key: bytes,
    nonce: bytes,
    aad: bytes,
) -> tuple[bytes, bytes]:
    lib = _load_libcrypto()
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("Could not allocate export encryption context.")
    try:
        _check_crypto(
            lib.EVP_EncryptInit_ex(ctx, lib.EVP_aes_256_gcm(), None, None, None),
            "Could not initialize export encryption.",
            lib,
        )
        _check_crypto(
            lib.EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, len(nonce), None),
            "Could not configure export encryption nonce.",
            lib,
        )
        key_buffer = ctypes.create_string_buffer(key, len(key))
        nonce_buffer = ctypes.create_string_buffer(nonce, len(nonce))
        _check_crypto(
            lib.EVP_EncryptInit_ex(ctx, None, None, key_buffer, nonce_buffer),
            "Could not set export encryption key.",
            lib,
        )
        _update_aad(lib, ctx, aad, encrypt=True)

        output = ctypes.create_string_buffer(max(1, len(plaintext)))
        output_len = ctypes.c_int(0)
        total = 0
        if plaintext:
            plaintext_buffer = ctypes.create_string_buffer(plaintext, len(plaintext))
            _check_crypto(
                lib.EVP_EncryptUpdate(
                    ctx,
                    output,
                    ctypes.byref(output_len),
                    plaintext_buffer,
                    len(plaintext),
                ),
                "Could not encrypt export payload.",
                lib,
            )
            total = output_len.value

        final_len = ctypes.c_int(0)
        _check_crypto(
            lib.EVP_EncryptFinal_ex(ctx, ctypes.byref(output, total), ctypes.byref(final_len)),
            "Could not finalize export encryption.",
            lib,
        )
        total += final_len.value
        tag_buffer = ctypes.create_string_buffer(AES_GCM_TAG_BYTES)
        _check_crypto(
            lib.EVP_CIPHER_CTX_ctrl(
                ctx,
                EVP_CTRL_GCM_GET_TAG,
                AES_GCM_TAG_BYTES,
                tag_buffer,
            ),
            "Could not read export authentication tag.",
            lib,
        )
        return output.raw[:total], tag_buffer.raw
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def _aes_256_gcm_decrypt(
    ciphertext: bytes,
    *,
    key: bytes,
    nonce: bytes,
    tag: bytes,
    aad: bytes,
) -> bytes:
    lib = _load_libcrypto()
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("Could not allocate export decryption context.")
    try:
        _check_crypto(
            lib.EVP_DecryptInit_ex(ctx, lib.EVP_aes_256_gcm(), None, None, None),
            "Could not initialize export decryption.",
            lib,
        )
        _check_crypto(
            lib.EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, len(nonce), None),
            "Could not configure export decryption nonce.",
            lib,
        )
        key_buffer = ctypes.create_string_buffer(key, len(key))
        nonce_buffer = ctypes.create_string_buffer(nonce, len(nonce))
        _check_crypto(
            lib.EVP_DecryptInit_ex(ctx, None, None, key_buffer, nonce_buffer),
            "Could not set export decryption key.",
            lib,
        )
        _update_aad(lib, ctx, aad, encrypt=False)

        output = ctypes.create_string_buffer(max(1, len(ciphertext)))
        output_len = ctypes.c_int(0)
        total = 0
        if ciphertext:
            ciphertext_buffer = ctypes.create_string_buffer(ciphertext, len(ciphertext))
            _check_crypto(
                lib.EVP_DecryptUpdate(
                    ctx,
                    output,
                    ctypes.byref(output_len),
                    ciphertext_buffer,
                    len(ciphertext),
                ),
                "Could not decrypt export payload.",
                lib,
            )
            total = output_len.value

        tag_buffer = ctypes.create_string_buffer(tag, len(tag))
        _check_crypto(
            lib.EVP_CIPHER_CTX_ctrl(
                ctx,
                EVP_CTRL_GCM_SET_TAG,
                AES_GCM_TAG_BYTES,
                tag_buffer,
            ),
            "Could not configure export authentication tag.",
            lib,
        )
        final_len = ctypes.c_int(0)
        if lib.EVP_DecryptFinal_ex(ctx, ctypes.byref(output, total), ctypes.byref(final_len)) != 1:
            raise ValueError("Invalid export authentication tag.")
        total += final_len.value
        return output.raw[:total]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def _update_aad(lib: Any, ctx: int, aad: bytes, *, encrypt: bool) -> None:
    aad_buffer = ctypes.create_string_buffer(aad, len(aad))
    aad_len = ctypes.c_int(0)
    update = lib.EVP_EncryptUpdate if encrypt else lib.EVP_DecryptUpdate
    _check_crypto(
        update(ctx, None, ctypes.byref(aad_len), aad_buffer, len(aad)),
        "Could not authenticate export header.",
        lib,
    )


def _load_libcrypto() -> Any:
    global _LIBCRYPTO
    if _LIBCRYPTO is not None:
        return _LIBCRYPTO

    candidate_paths = (
        "/opt/homebrew/opt/openssl@3/lib/libcrypto.dylib",
        "/usr/local/opt/openssl@3/lib/libcrypto.dylib",
    )
    lib_path = next((path for path in candidate_paths if Path(path).exists()), None)
    lib_path = lib_path or ctypes.util.find_library("crypto")
    if lib_path is None:
        raise RuntimeError("OpenSSL libcrypto is required for encrypted exports.")
    if sys.platform == "darwin" and lib_path.startswith("/usr/lib/"):
        raise RuntimeError("OpenSSL 3 libcrypto is required for encrypted exports.")
    lib = ctypes.CDLL(lib_path)
    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_aes_256_gcm.restype = ctypes.c_void_p
    lib.EVP_EncryptInit_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.EVP_DecryptInit_ex.argtypes = lib.EVP_EncryptInit_ex.argtypes
    lib.EVP_EncryptUpdate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    lib.EVP_DecryptUpdate.argtypes = lib.EVP_EncryptUpdate.argtypes
    lib.EVP_EncryptFinal_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.EVP_DecryptFinal_ex.argtypes = lib.EVP_EncryptFinal_ex.argtypes
    lib.EVP_CIPHER_CTX_ctrl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    lib.ERR_get_error.restype = ctypes.c_ulong
    lib.ERR_error_string_n.argtypes = [ctypes.c_ulong, ctypes.c_char_p, ctypes.c_size_t]
    _LIBCRYPTO = lib
    return lib


def _check_crypto(result: int, message: str, lib: Any) -> None:
    if result == 1:
        return
    error_code = lib.ERR_get_error()
    if not error_code:
        raise RuntimeError(message)
    buffer = ctypes.create_string_buffer(256)
    lib.ERR_error_string_n(error_code, buffer, len(buffer))
    raise RuntimeError(f"{message} {buffer.value.decode('ascii', errors='replace')}")


def _payload_bytes(payload: dict[str, Any], export_format: DataExportFormat) -> bytes:
    if export_format == DataExportFormat.json:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["section", "index", "record_json"])
    writer.writeheader()
    sections = payload["sections"]
    for section, rows in sections.items():
        for index, row in enumerate(rows):
            writer.writerow(
                {
                    "section": section,
                    "index": index,
                    "record_json": json.dumps(row, sort_keys=True, default=str),
                }
            )
    return output.getvalue().encode()


def _resolved_scopes(scope: DataExportScope) -> set[DataExportScope]:
    if scope == DataExportScope.all:
        return {
            DataExportScope.consent,
            DataExportScope.health,
            DataExportScope.checkins,
            DataExportScope.memory,
            DataExportScope.briefings,
            DataExportScope.recommendations,
        }
    return {scope}


def _unique_ids(values: list[UUID | None]) -> list[UUID]:
    seen: set[UUID] = set()
    result: list[UUID] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _parse_manifest_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _job_id_from_manifest_path(path: Path) -> UUID | None:
    name = path.name.removesuffix(".export.json")
    try:
        return UUID(name)
    except ValueError:
        return None


def _row_dict(row: Any) -> dict[str, Any]:
    payload = row.model_dump(mode="json")
    return cast(dict[str, Any], _redact_bytes(payload))


def _redact_bytes(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_bytes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_bytes(item) for item in value]
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value
