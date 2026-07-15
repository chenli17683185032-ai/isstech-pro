"""Provider output remains evidence-backed, bounded, and unable to bypass review."""

from __future__ import annotations

from dataclasses import replace
import json

import httpx
import pytest

from isstech_replay.ai.provider import (
    HttpJsonExtractionProvider,
    ProviderResponseError,
    RuleBasedExtractionProvider,
)
from isstech_replay.field_mapping import (
    PURCHASE_REQUISITION_FIELDS,
    validate_proposals,
)
from isstech_replay.models.extraction import (
    FieldEvidence,
    ProposedField,
    SourceKind,
    StructuredDocument,
    DocumentUnit,
)


MATERIAL_ID = "material-redacted"
DOCUMENT_TEXT = "\n".join(
    (
        "项目编号：PRJ-001",
        "项目名称：REDACTED PROJECT",
        "采购方式：公开询价",
        "签署主体：REDACTED ENTITY",
        "备注：REDACTED NOTE",
    )
)


def _document(*, issues: tuple[str, ...] = ()) -> StructuredDocument:
    return StructuredDocument(
        material_id=MATERIAL_ID,
        material_sha256="a" * 64,
        detected_mime_type="text/plain",
        parser_name="utf8-text",
        parser_version="structured-document/1+1",
        units=(DocumentUnit(SourceKind.DOCUMENT, 1, "Document", DOCUMENT_TEXT),),
        issues=issues,
    )


def _proposals(document: StructuredDocument) -> tuple[ProposedField, ...]:
    return RuleBasedExtractionProvider().propose(document, PURCHASE_REQUISITION_FIELDS)


def _replace_field(
    proposals: tuple[ProposedField, ...],
    field_name: str,
    **changes: object,
) -> tuple[ProposedField, ...]:
    return tuple(
        replace(proposal, **changes) if proposal.field_name == field_name else proposal
        for proposal in proposals
    )


def _issue_codes(validation) -> set[tuple[str, str]]:
    return {(issue.code, issue.field_name) for issue in validation.issues}


def _http_field(**changes: object) -> dict[str, object]:
    field: dict[str, object] = {
        "field_name": "PR_PrjNo",
        "proposed_value": "PRJ-001",
        "confidence": 0.97,
        "evidence": {
            "material_id": MATERIAL_ID,
            "source_kind": "document",
            "source_index": 1,
            "source_label": "Document",
            "source_text": "项目编号：PRJ-001",
        },
    }
    field.update(changes)
    return field


def _http_provider(payload: object, **changes: object) -> HttpJsonExtractionProvider:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=json.dumps(payload, allow_nan=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    )
    return HttpJsonExtractionProvider(
        endpoint="https://ai.example.test/extract",
        model="redacted-extractor",
        transport=transport,
        **changes,
    )


def test_local_rules_extract_all_profile_fields_with_exact_evidence() -> None:
    document = _document()
    proposals = _proposals(document)
    validation = validate_proposals(
        document,
        PURCHASE_REQUISITION_FIELDS,
        proposals,
    )

    assert [proposal.field_name for proposal in proposals] == [
        spec.name for spec in PURCHASE_REQUISITION_FIELDS
    ]
    assert all(proposal.evidence is not None for proposal in proposals)
    assert validation.proposals == proposals
    assert validation.issues == ()
    assert validation.can_advance is True


def test_local_rules_accept_structured_table_cell_separator() -> None:
    document = replace(
        _document(),
        units=(
            DocumentUnit(
                SourceKind.SHEET,
                1,
                "Project Sheet",
                "项目编号\tPRJ-001\n项目名称\tREDACTED PROJECT\n采购方式\t公开询价",
            ),
        ),
    )

    proposals = _proposals(document)
    validation = validate_proposals(
        document,
        PURCHASE_REQUISITION_FIELDS,
        proposals,
    )

    assert [proposal.field_name for proposal in proposals] == [
        "PR_PrjNo",
        "PR_PrjName",
        "PR_ProcurementMethod",
    ]
    assert all(proposal.evidence.source_kind is SourceKind.SHEET for proposal in proposals)  # type: ignore[union-attr]
    assert validation.can_advance is True


def test_missing_required_field_cannot_advance() -> None:
    document = _document()
    proposals = tuple(
        proposal
        for proposal in _proposals(document)
        if proposal.field_name != "PR_ProcurementMethod"
    )

    validation = validate_proposals(document, PURCHASE_REQUISITION_FIELDS, proposals)

    assert ("missing_required", "PR_ProcurementMethod") in _issue_codes(validation)
    assert validation.can_advance is False


def test_low_confidence_cannot_advance() -> None:
    document = _document()
    proposals = _replace_field(
        _proposals(document),
        "PR_PrjNo",
        confidence=0.84,
    )

    validation = validate_proposals(document, PURCHASE_REQUISITION_FIELDS, proposals)

    assert ("low_confidence", "PR_PrjNo") in _issue_codes(validation)
    assert validation.can_advance is False


def test_missing_evidence_cannot_advance() -> None:
    document = _document()
    proposals = _replace_field(
        _proposals(document),
        "PR_PrjNo",
        evidence=None,
    )

    validation = validate_proposals(document, PURCHASE_REQUISITION_FIELDS, proposals)

    assert ("missing_evidence", "PR_PrjNo") in _issue_codes(validation)
    assert validation.can_advance is False


@pytest.mark.parametrize(
    ("evidence", "expected_code"),
    [
        (
            FieldEvidence(
                "another-material",
                SourceKind.DOCUMENT,
                1,
                "Document",
                "项目编号：PRJ-001",
            ),
            "wrong_material",
        ),
        (
            FieldEvidence(
                MATERIAL_ID,
                SourceKind.PAGE,
                99,
                "Page 99",
                "项目编号：PRJ-001",
            ),
            "unknown_source",
        ),
        (
            FieldEvidence(
                MATERIAL_ID,
                SourceKind.DOCUMENT,
                1,
                "Wrong label",
                "项目编号：PRJ-001",
            ),
            "source_label_mismatch",
        ),
    ],
)
def test_wrong_material_or_source_cannot_advance(
    evidence: FieldEvidence,
    expected_code: str,
) -> None:
    document = _document()
    proposals = _replace_field(
        _proposals(document),
        "PR_PrjNo",
        evidence=evidence,
    )

    validation = validate_proposals(document, PURCHASE_REQUISITION_FIELDS, proposals)

    assert (expected_code, "PR_PrjNo") in _issue_codes(validation)
    assert validation.can_advance is False


def test_source_excerpt_must_exist_exactly_in_unit() -> None:
    document = _document()
    evidence = FieldEvidence(
        MATERIAL_ID,
        SourceKind.DOCUMENT,
        1,
        "Document",
        "项目编号：NOT-IN-DOCUMENT",
    )
    proposals = _replace_field(
        _proposals(document),
        "PR_PrjNo",
        evidence=evidence,
    )

    validation = validate_proposals(document, PURCHASE_REQUISITION_FIELDS, proposals)

    assert ("source_text_mismatch", "PR_PrjNo") in _issue_codes(validation)
    assert validation.can_advance is False


def test_proposed_value_must_be_locatable_in_source_excerpt() -> None:
    document = _document()
    proposals = _replace_field(
        _proposals(document),
        "PR_PrjNo",
        proposed_value="PRJ-999",
    )

    validation = validate_proposals(document, PURCHASE_REQUISITION_FIELDS, proposals)

    assert ("value_not_in_source", "PR_PrjNo") in _issue_codes(validation)
    assert validation.can_advance is False


def test_duplicate_and_unknown_fields_are_rejected() -> None:
    document = _document()
    proposals = _proposals(document)
    unknown = ProposedField(
        field_name="UNREQUESTED_FIELD",
        proposed_value="REDACTED",
        confidence=0.99,
        evidence=proposals[0].evidence,
    )

    validation = validate_proposals(
        document,
        PURCHASE_REQUISITION_FIELDS,
        (*proposals, proposals[0], unknown),
    )

    assert ("duplicate_field", "PR_PrjNo") in _issue_codes(validation)
    assert ("unknown_field", "UNREQUESTED_FIELD") in _issue_codes(validation)
    assert validation.proposals == proposals
    assert validation.can_advance is False


def test_document_issue_blocks_otherwise_valid_proposals() -> None:
    document = _document(issues=("OCR/manual review required",))

    validation = validate_proposals(
        document,
        PURCHASE_REQUISITION_FIELDS,
        _proposals(document),
    )

    assert ("document_issue", "") in _issue_codes(validation)
    assert validation.can_advance is False


def test_http_provider_sends_structured_contract_and_parses_valid_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"fields": [_http_field()]})

    provider = HttpJsonExtractionProvider(
        endpoint="https://ai.example.test/extract",
        model="redacted-extractor",
        api_key="REDACTED",
        transport=httpx.MockTransport(handler),
    )

    proposals = provider.propose(_document(), PURCHASE_REQUISITION_FIELDS[:1])

    assert captured["authorization"] == "Bearer REDACTED"
    request_payload = captured["payload"]
    assert isinstance(request_payload, dict)
    assert request_payload["task"] == "extract_fields_with_evidence"
    assert request_payload["document"]["material_id"] == MATERIAL_ID
    assert proposals[0].field_name == "PR_PrjNo"
    assert proposals[0].evidence is not None


def test_http_provider_missing_evidence_is_preserved_for_review_gate() -> None:
    field = _http_field()
    field.pop("evidence")
    provider = _http_provider({"fields": [field]})
    document = _document()

    proposals = provider.propose(document, PURCHASE_REQUISITION_FIELDS[:1])
    validation = validate_proposals(
        document,
        PURCHASE_REQUISITION_FIELDS[:1],
        proposals,
    )

    assert proposals[0].evidence is None
    assert ("missing_evidence", "PR_PrjNo") in _issue_codes(validation)
    assert validation.can_advance is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"fields": {}},
        {"fields": ["not-an-object"]},
        {"fields": [_http_field(field_name=123)]},
        {"fields": [_http_field(proposed_value=["PRJ-001"])]},
        {"fields": [_http_field(evidence=[])]},
        {
            "fields": [
                _http_field(
                    evidence={
                        "material_id": MATERIAL_ID,
                        "source_kind": "document",
                        "source_index": "1",
                        "source_label": "Document",
                        "source_text": "项目编号：PRJ-001",
                    }
                )
            ]
        },
    ],
)
def test_http_provider_rejects_malformed_json_contract(payload: object) -> None:
    provider = _http_provider(payload)

    with pytest.raises(ProviderResponseError):
        provider.propose(_document(), PURCHASE_REQUISITION_FIELDS[:1])


@pytest.mark.parametrize("confidence", [True, "0.9", -0.1, 1.1, float("inf")])
def test_http_provider_rejects_invalid_confidence(confidence: object) -> None:
    provider = _http_provider({"fields": [_http_field(confidence=confidence)]})

    with pytest.raises(ProviderResponseError, match="confidence"):
        provider.propose(_document(), PURCHASE_REQUISITION_FIELDS[:1])


def test_http_provider_enforces_streamed_response_size_limit() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=b"{" + b"x" * 128 + b"}")
    )
    provider = HttpJsonExtractionProvider(
        endpoint="https://ai.example.test/extract",
        model="redacted-extractor",
        max_response_bytes=64,
        transport=transport,
    )

    with pytest.raises(ProviderResponseError, match="configured limit"):
        provider.propose(_document(), PURCHASE_REQUISITION_FIELDS[:1])


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ipsapro.isstech.com/extract",
        "https://passport.isstech.com/extract",
        "https://ipsapro.isstech.com./extract",
        "http://ai.example.test/extract",
    ],
)
def test_http_provider_refuses_workflow_hosts_and_remote_plain_http(endpoint: str) -> None:
    with pytest.raises(ValueError):
        HttpJsonExtractionProvider(endpoint=endpoint, model="redacted-extractor")
