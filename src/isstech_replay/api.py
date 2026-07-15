"""FastAPI application assembly for the local replay facade."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
import uvicorn

from . import __version__
from .client import IsstechClient
from .config import Settings
from .routes import (
    attachments,
    materials,
    previews,
    purchase_requisitions,
    sessions,
    sync as sync_routes,
    work_items,
)
from .session_store import SessionStore, session_ttl_from_settings


def create_app(
    *,
    session_store: SessionStore | None = None,
    client_factory: Callable[[], IsstechClient] | None = None,
) -> FastAPI:
    application = FastAPI(
        title="iSStech Purchase Requisition Replay API",
        version=__version__,
        description=(
            "Browser-independent read-only facade for the authorized CTF target. "
            "Write operations are preview-only and never sent upstream. "
            "Local Bearer tokens never include upstream .iPSA cookie values."
        ),
    )
    application.state.session_store = session_store or SessionStore(
        ttl_seconds=session_ttl_from_settings()
    )
    application.state.client_factory = client_factory or (
        lambda: IsstechClient(settings=Settings.from_env())
    )

    application.include_router(sessions.router, prefix="/v1")
    application.include_router(purchase_requisitions.router, prefix="/v1")
    application.include_router(attachments.router, prefix="/v1")
    application.include_router(materials.router, prefix="/v1")
    application.include_router(previews.router, prefix="/v1")
    application.include_router(work_items.router, prefix="/v1")
    application.include_router(sync_routes.router, prefix="/v1")

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/v1/health", tags=["system"])
    def health_v1() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return application


app = create_app()


def run() -> None:
    uvicorn.run("isstech_replay.api:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
