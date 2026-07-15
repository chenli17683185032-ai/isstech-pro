"""Authenticated local document parsing and evidence-backed field extraction."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from isstech_replay.ai.provider import ProviderResponseError, provider_from_env
from isstech_replay.errors import (
    bad_request,
    local_storage_error,
    not_found,
    parse_error,
    upstream_error,
)
from isstech_replay.extraction import (
    DocumentExtractionError,
    FieldExtractionService,
    UnsupportedDocumentType,
)
from isstech_replay.field_mapping import DEFAULT_CONFIDENCE_THRESHOLD
from isstech_replay.materials import MaterialService
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage, default_data_dir, default_database_path
from isstech_replay.sync import safe_error_message


router = APIRouter(tags=["extractions"])


class ExtractionCreateIn(BaseModel):
    provider: Literal["local_rules", "http_json"] = "local_rules"
    profile: Literal["purchase_requisition"] = "purchase_requisition"
    confidence_threshold: float = Field(
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        ge=0,
        le=1,
    )


class FieldIssueOut(BaseModel):
    code: str
    field_name: str
    message: str


class FieldEvidenceOut(BaseModel):
    material_id: str
    source_kind: str
    source_index: int
    source_label: str
    source_text: str


class ExtractedFieldOut(BaseModel):
    field_name: str
    proposed_value: str
    confidence: float
    required: bool
    evidence: FieldEvidenceOut | None = None
    evidence_valid: bool
    validation_issues: list[FieldIssueOut] = Field(default_factory=list)
    review_status: str
    confirmed_value: str | None = None


class ExtractionOut(BaseModel):
    extraction_id: str
    material_id: str
    profile: str
    provider: str
    model: str
    extractor_version: str
    status: str
    confidence_threshold: float
    can_advance: bool
    document_path: str
    result_path: str
    started_at: str
    finished_at: str | None = None
    field_count: int
    issue_count: int
    issues: list[FieldIssueOut] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    fields: list[ExtractedFieldOut] = Field(default_factory=list)


class ExtractionSummaryOut(BaseModel):
    extraction_id: str
    material_id: str
    profile: str
    provider: str
    model: str
    status: str
    confidence_threshold: float
    can_advance: bool
    started_at: str
    finished_at: str | None = None
    field_count: int
    issue_count: int
    error_type: str | None = None
    error_message: str | None = None


def _material_service(request: Request) -> MaterialService:
    factory = getattr(request.app.state, "material_service_factory", None)
    if factory is not None:
        return factory()
    return MaterialService(
        data_dir=default_data_dir(),
        storage=WorkflowStorage(default_database_path()),
    )


def _provider(request: Request, name: str):
    factory = getattr(request.app.state, "extraction_provider_factory", None)
    if factory is not None:
        return factory(name)
    return provider_from_env(name)


def _decode_issues(value: object) -> list[FieldIssueOut]:
    if not isinstance(value, str):
        raise ValueError("stored extraction issues must be JSON text")
    payload = json.loads(value)
    if not isinstance(payload, list):
        raise ValueError("stored extraction issues must be a list")
    return [FieldIssueOut.model_validate(item) for item in payload]


def _extraction_out(record: dict[str, object]) -> ExtractionOut:
    fields_raw = record.get("fields")
    if not isinstance(fields_raw, list):
        raise ValueError("stored extraction fields must be a list")
    fields: list[ExtractedFieldOut] = []
    for field in fields_raw:
        if not isinstance(field, dict):
            raise ValueError("stored extraction field must be an object")
        evidence = None
        source_material_id = field.get("source_material_id")
        if source_material_id is not None:
            evidence = FieldEvidenceOut(
                material_id=str(source_material_id),
                source_kind=str(field["source_kind"]),
                source_index=int(field["source_index"]),
                source_label=str(field["source_label"]),
                source_text=str(field["source_text"]),
            )
        fields.append(
            ExtractedFieldOut(
                field_name=str(field["field_name"]),
                proposed_value=str(field["proposed_value"]),
                confidence=float(field["confidence"]),
                required=bool(field["required"]),
                evidence=evidence,
                evidence_valid=bool(field["evidence_valid"]),
                validation_issues=_decode_issues(field["validation_issues_json"]),
                review_status=str(field["review_status"]),
                confirmed_value=(
                    str(field["confirmed_value"])
                    if field.get("confirmed_value") is not None
                    else None
                ),
            )
        )
    return ExtractionOut(
        extraction_id=str(record["extraction_id"]),
        material_id=str(record["material_id"]),
        profile=str(record["profile"]),
        provider=str(record["provider"]),
        model=str(record["model"]),
        extractor_version=str(record["extractor_version"]),
        status=str(record["status"]),
        confidence_threshold=float(record["confidence_threshold"]),
        can_advance=bool(record["can_advance"]),
        document_path=str(record["document_path"]),
        result_path=str(record["result_path"]),
        started_at=str(record["started_at"]),
        finished_at=(
            str(record["finished_at"]) if record.get("finished_at") is not None else None
        ),
        field_count=int(record["field_count"]),
        issue_count=int(record["issue_count"]),
        issues=_decode_issues(record["issues_json"]),
        error_type=(
            str(record["error_type"]) if record.get("error_type") is not None else None
        ),
        error_message=(
            str(record["error_message"])
            if record.get("error_message") is not None
            else None
        ),
        fields=fields,
    )


@router.post(
    "/materials/{material_id}/extractions",
    response_model=ExtractionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_extraction(
    material_id: str,
    body: ExtractionCreateIn,
    _session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
    request: Request,
) -> ExtractionOut:
    try:
        material = material_service.get(material_id)
    except Exception as exc:
        raise local_storage_error(
            f"material lookup failed: {type(exc).__name__}"
        ) from exc
    if material is None:
        raise not_found("material not found", details={"material_id": material_id})
    try:
        provider = _provider(request, body.provider)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc

    extraction_id = uuid4().hex
    try:
        FieldExtractionService(material_service, provider).extract(
            material_id,
            profile=body.profile,
            confidence_threshold=body.confidence_threshold,
            extraction_id=extraction_id,
        )
        record = material_service.storage.get_extraction(extraction_id)
        if record is None:
            raise RuntimeError("completed extraction record is missing")
        return _extraction_out(record)
    except UnsupportedDocumentType as exc:
        raise parse_error(
            str(exc),
            details={"extraction_id": extraction_id},
        ) from exc
    except DocumentExtractionError as exc:
        raise parse_error(
            safe_error_message(exc),
            details={"extraction_id": extraction_id},
        ) from exc
    except (ProviderResponseError, httpx.HTTPError) as exc:
        raise upstream_error(
            f"AI extraction provider failed: {safe_error_message(exc)}",
            details={"extraction_id": extraction_id},
        ) from exc
    except ValueError as exc:
        raise bad_request(
            safe_error_message(exc),
            details={"extraction_id": extraction_id},
        ) from exc
    except Exception as exc:
        raise local_storage_error(
            f"extraction failed: {type(exc).__name__}",
            details={"extraction_id": extraction_id},
        ) from exc


@router.get("/extractions", response_model=list[ExtractionSummaryOut])
def list_extractions(
    _session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
    material_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[ExtractionSummaryOut]:
    try:
        records = material_service.storage.list_extractions(
            material_id=material_id,
            limit=limit,
        )
        return [
            ExtractionSummaryOut(
                extraction_id=str(record["extraction_id"]),
                material_id=str(record["material_id"]),
                profile=str(record["profile"]),
                provider=str(record["provider"]),
                model=str(record["model"]),
                status=str(record["status"]),
                confidence_threshold=float(record["confidence_threshold"]),
                can_advance=bool(record["can_advance"]),
                started_at=str(record["started_at"]),
                finished_at=(
                    str(record["finished_at"])
                    if record.get("finished_at") is not None
                    else None
                ),
                field_count=int(record["field_count"]),
                issue_count=int(record["issue_count"]),
                error_type=(
                    str(record["error_type"])
                    if record.get("error_type") is not None
                    else None
                ),
                error_message=(
                    str(record["error_message"])
                    if record.get("error_message") is not None
                    else None
                ),
            )
            for record in records
        ]
    except Exception as exc:
        raise local_storage_error(
            f"extraction list failed: {type(exc).__name__}"
        ) from exc


@router.get("/extractions/{extraction_id}", response_model=ExtractionOut)
def get_extraction(
    extraction_id: str,
    _session: Annotated[SessionRecord, Depends(get_session)],
    material_service: Annotated[MaterialService, Depends(_material_service)],
) -> ExtractionOut:
    try:
        record = material_service.storage.get_extraction(extraction_id)
        if record is None:
            raise not_found(
                "extraction not found",
                details={"extraction_id": extraction_id},
            )
        return _extraction_out(record)
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            raise
        raise local_storage_error(
            f"extraction lookup failed: {type(exc).__name__}"
        ) from exc
