"""Authenticated local material ingestion and immutable-original access."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from isstech_replay.errors import (
    bad_request,
    local_storage_error,
    not_found,
    payload_too_large,
)
from isstech_replay.materials import (
    MaterialIngestError,
    MaterialService,
    MaterialTooLargeError,
)
from isstech_replay.models.materials import Material, MaterialStatus
from isstech_replay.routes.deps import get_session
from isstech_replay.session_store import SessionRecord
from isstech_replay.storage import WorkflowStorage, default_data_dir, default_database_path


router = APIRouter(tags=["materials"])


class MaterialOut(BaseModel):
    id: str
    sha256: str
    size_bytes: int
    original_name: str
    declared_mime_type: str
    detected_mime_type: str
    extension: str
    status: str
    review_reason: str
    created_at: str


class MaterialIngestOut(BaseModel):
    material: MaterialOut
    deduplicated: bool
    blob_created: bool


def _service(request: Request) -> MaterialService:
    factory = getattr(request.app.state, "material_service_factory", None)
    if factory is not None:
        return factory()
    return MaterialService(
        data_dir=default_data_dir(),
        storage=WorkflowStorage(default_database_path()),
    )


def _material_out(material: Material) -> MaterialOut:
    return MaterialOut(
        id=material.id,
        sha256=material.sha256,
        size_bytes=material.size_bytes,
        original_name=material.original_name,
        declared_mime_type=material.declared_mime_type,
        detected_mime_type=material.detected_mime_type,
        extension=material.extension,
        status=material.status.value,
        review_reason=material.review_reason,
        created_at=material.created_at,
    )


@router.post(
    "/materials",
    response_model=MaterialIngestOut,
    status_code=status.HTTP_201_CREATED,
)
def ingest_material(
    _session: Annotated[SessionRecord, Depends(get_session)],
    service: Annotated[MaterialService, Depends(_service)],
    file: Annotated[UploadFile, File(...)],
) -> MaterialIngestOut:
    try:
        result = service.ingest_stream(
            file.file,
            original_name=file.filename or "",
            declared_mime_type=file.content_type,
        )
    except MaterialTooLargeError as exc:
        raise payload_too_large(str(exc)) from exc
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    except MaterialIngestError as exc:
        raise local_storage_error(str(exc)) from exc
    except Exception as exc:
        raise local_storage_error(f"material ingest failed: {type(exc).__name__}") from exc
    return MaterialIngestOut(
        material=_material_out(result.material),
        deduplicated=result.deduplicated,
        blob_created=result.blob_created,
    )


@router.get("/materials", response_model=list[MaterialOut])
def list_materials(
    _session: Annotated[SessionRecord, Depends(get_session)],
    service: Annotated[MaterialService, Depends(_service)],
    ingest_status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[MaterialOut]:
    parsed_status: MaterialStatus | None = None
    if ingest_status is not None:
        try:
            parsed_status = MaterialStatus(ingest_status)
        except ValueError as exc:
            raise bad_request(
                f"unknown material status: {ingest_status}",
                details={"allowed": [status.value for status in MaterialStatus]},
            ) from exc
    try:
        return [
            _material_out(material)
            for material in service.list(status=parsed_status, limit=limit)
        ]
    except Exception as exc:
        raise local_storage_error(f"material list failed: {type(exc).__name__}") from exc


@router.get("/materials/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: str,
    _session: Annotated[SessionRecord, Depends(get_session)],
    service: Annotated[MaterialService, Depends(_service)],
) -> MaterialOut:
    try:
        material = service.get(material_id)
    except Exception as exc:
        raise local_storage_error(f"material lookup failed: {type(exc).__name__}") from exc
    if material is None:
        raise not_found("material not found", details={"material_id": material_id})
    return _material_out(material)


@router.get("/materials/{material_id}/content", response_class=FileResponse)
def get_material_content(
    material_id: str,
    _session: Annotated[SessionRecord, Depends(get_session)],
    service: Annotated[MaterialService, Depends(_service)],
) -> FileResponse:
    try:
        material = service.get(material_id)
    except Exception as exc:
        raise local_storage_error(f"material lookup failed: {type(exc).__name__}") from exc
    if material is None:
        raise not_found("material not found", details={"material_id": material_id})
    try:
        path = service.resolve_original(material)
    except MaterialIngestError as exc:
        raise local_storage_error(str(exc)) from exc
    except Exception as exc:
        raise local_storage_error(f"material content failed: {type(exc).__name__}") from exc
    return FileResponse(
        path,
        media_type=material.detected_mime_type,
        filename=material.original_name,
    )
