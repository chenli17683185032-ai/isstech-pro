"""Stable API error codes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "details": details or {},
            },
        )


def auth_expired(message: str = "Session missing or expired") -> ApiError:
    return ApiError(401, "AUTH_EXPIRED", message)


def upstream_error(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(502, "UPSTREAM_ERROR", message, details=details)


def parse_error(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(422, "PARSE_ERROR", message, details=details)


def write_blocked(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(403, "WRITE_BLOCKED", message, details=details)


def bad_request(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(400, "BAD_REQUEST", message, details=details)


def not_captured(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(501, "NOT_CAPTURED", message, details=details)


def not_found(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(404, "NOT_FOUND", message, details=details)


def payload_too_large(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(413, "PAYLOAD_TOO_LARGE", message, details=details)


def local_storage_error(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(500, "LOCAL_STORAGE_ERROR", message, details=details)
