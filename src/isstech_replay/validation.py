"""Validation for values interpolated into upstream URL path segments."""

from __future__ import annotations

import re


_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def require_path_segment(value: str, label: str = "id") -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{label} is required")
    if not _PATH_SEGMENT_RE.fullmatch(value):
        raise ValueError(f"{label} contains unsupported path characters")
    return value
