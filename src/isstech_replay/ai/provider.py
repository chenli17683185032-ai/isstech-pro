"""Local deterministic and explicitly configured HTTP JSON providers."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
import os
import re
from urllib.parse import urlparse

import httpx

from isstech_replay.models.extraction import (
    FieldEvidence,
    FieldSpec,
    ProposedField,
    SourceKind,
    StructuredDocument,
)


class ProviderResponseError(RuntimeError):
    """The provider response violates the extraction-only JSON contract."""


class RuleBasedExtractionProvider:
    name = "local_rules"
    model = "label_value_lines"
    version = "1"

    def propose(
        self,
        document: StructuredDocument,
        field_specs: tuple[FieldSpec, ...],
    ) -> tuple[ProposedField, ...]:
        proposals: list[ProposedField] = []
        for spec in field_specs:
            labels = tuple(dict.fromkeys((spec.label, *spec.aliases)))
            pattern = re.compile(
                r"^\s*(?:"
                + "|".join(re.escape(label) for label in labels)
                + r")(?:\s*[:：]\s*|\t+)(.+?)\s*$",
                re.IGNORECASE,
            )
            found = False
            for unit in document.units:
                for line in unit.text.splitlines():
                    match = pattern.match(line)
                    if not match:
                        continue
                    source_text = line.strip()
                    proposals.append(
                        ProposedField(
                            field_name=spec.name,
                            proposed_value=match.group(1).strip(),
                            confidence=0.98,
                            evidence=FieldEvidence(
                                material_id=document.material_id,
                                source_kind=unit.kind,
                                source_index=unit.index,
                                source_label=unit.label,
                                source_text=source_text,
                            ),
                        )
                    )
                    found = True
                    break
                if found:
                    break
        return tuple(proposals)


class HttpJsonExtractionProvider:
    """Call an operator-configured extraction endpoint with a strict JSON shape."""

    name = "http_json"
    version = "1"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("AI extraction endpoint must be an absolute HTTP(S) URL")
        if host in {"ipsapro.isstech.com", "passport.isstech.com"}:
            raise ValueError("workflow target hosts cannot be used as AI endpoints")
        if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("non-loopback AI extraction endpoints must use HTTPS")
        if not model.strip():
            raise ValueError("AI extraction model is required")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("provider limits must be positive")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport

    def propose(
        self,
        document: StructuredDocument,
        field_specs: tuple[FieldSpec, ...],
    ) -> tuple[ProposedField, ...]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request_body = {
            "schema_version": 1,
            "task": "extract_fields_with_evidence",
            "model": self.model,
            "document": asdict(document),
            "field_specs": [asdict(spec) for spec in field_specs],
            "output_contract": {
                "fields": [
                    {
                        "field_name": "string",
                        "proposed_value": "string",
                        "confidence": "number 0..1",
                        "evidence": {
                            "material_id": "string",
                            "source_kind": "page|document|sheet|slide",
                            "source_index": "positive integer",
                            "source_label": "string",
                            "source_text": "exact source excerpt",
                        },
                    }
                ]
            },
        }
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            with client.stream(
                "POST",
                self.endpoint,
                headers=headers,
                json=request_body,
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = -1
                    if declared_size > self.max_response_bytes:
                        raise ProviderResponseError(
                            "AI provider response exceeds configured limit"
                        )
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise ProviderResponseError(
                            "AI provider response exceeds configured limit"
                        )
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("AI provider did not return JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
            raise ProviderResponseError("AI provider response must contain a fields array")
        proposals: list[ProposedField] = []
        for raw in payload["fields"]:
            if not isinstance(raw, dict):
                raise ProviderResponseError("AI provider field entries must be objects")
            field_name = raw.get("field_name")
            proposed_value = raw.get("proposed_value")
            if not isinstance(field_name, str) or not isinstance(proposed_value, str):
                raise ProviderResponseError(
                    "AI provider field name and proposed value must be strings"
                )
            evidence_raw = raw.get("evidence")
            evidence = None
            if evidence_raw is not None:
                if not isinstance(evidence_raw, dict):
                    raise ProviderResponseError("AI provider evidence must be an object")
                material_id = evidence_raw.get("material_id")
                source_kind = evidence_raw.get("source_kind")
                source_index = evidence_raw.get("source_index")
                source_label = evidence_raw.get("source_label")
                source_text = evidence_raw.get("source_text")
                if (
                    not isinstance(material_id, str)
                    or not isinstance(source_kind, str)
                    or isinstance(source_index, bool)
                    or not isinstance(source_index, int)
                    or source_index < 1
                    or not isinstance(source_label, str)
                    or not isinstance(source_text, str)
                ):
                    raise ProviderResponseError("AI provider evidence shape is invalid")
                try:
                    evidence = FieldEvidence(
                        material_id=material_id,
                        source_kind=SourceKind(source_kind),
                        source_index=source_index,
                        source_label=source_label,
                        source_text=source_text,
                    )
                except ValueError as exc:
                    raise ProviderResponseError("AI provider evidence shape is invalid") from exc
            confidence_raw = raw.get("confidence")
            if isinstance(confidence_raw, bool) or not isinstance(
                confidence_raw, (int, float)
            ):
                raise ProviderResponseError("AI provider confidence is invalid")
            confidence = float(confidence_raw)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ProviderResponseError("AI provider confidence is invalid")
            proposals.append(
                ProposedField(
                    field_name=field_name,
                    proposed_value=proposed_value,
                    confidence=confidence,
                    evidence=evidence,
                )
            )
        return tuple(proposals)


def provider_from_env(name: str):
    if name == "local_rules":
        return RuleBasedExtractionProvider()
    if name != "http_json":
        raise ValueError(f"unknown extraction provider: {name}")
    endpoint = os.getenv("ISSTECH_AI_ENDPOINT", "").strip()
    model = os.getenv("ISSTECH_AI_MODEL", "").strip()
    if not endpoint or not model:
        raise ValueError("ISSTECH_AI_ENDPOINT and ISSTECH_AI_MODEL are required")
    return HttpJsonExtractionProvider(
        endpoint=endpoint,
        model=model,
        api_key=os.getenv("ISSTECH_AI_API_KEY") or None,
    )
