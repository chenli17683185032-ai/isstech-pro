"""Minimal provider contract over already-structured local documents."""

from __future__ import annotations

from typing import Protocol

from isstech_replay.models.extraction import FieldSpec, ProposedField, StructuredDocument


class ExtractionProvider(Protocol):
    name: str
    model: str
    version: str

    def propose(
        self,
        document: StructuredDocument,
        field_specs: tuple[FieldSpec, ...],
    ) -> tuple[ProposedField, ...]: ...
