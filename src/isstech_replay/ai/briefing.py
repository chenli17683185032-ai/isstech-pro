"""Strict, tool-free model adapter for daily follow-up prioritization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Protocol
from urllib.parse import urlparse

import httpx

from isstech_replay.models.assistant import AssistantCandidate


ASSISTANT_ENDPOINT_SERVICE = "com.isstech.workflow-center.assistant.endpoint"
ASSISTANT_MODEL_SERVICE = "com.isstech.workflow-center.assistant.model"
ASSISTANT_API_KEY_SERVICE = "com.isstech.workflow-center.assistant.api-key"
DEFAULT_ASSISTANT_TIMEOUT_SECONDS = 45.0
DEFAULT_ASSISTANT_MAX_RESPONSE_BYTES = 256 * 1024
MAX_MODEL_PRIORITIES = 5


class BriefingProviderError(RuntimeError):
    """The model call or its output violated the bounded briefing contract."""


@dataclass(frozen=True, slots=True)
class ModelPriority:
    item_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class ModelBriefing:
    summary: str
    priorities: tuple[ModelPriority, ...]


class BriefingProvider(Protocol):
    name: str
    model: str

    def prioritize(
        self,
        candidates: tuple[AssistantCandidate, ...],
        preferences: tuple[str, ...],
    ) -> ModelBriefing: ...


@dataclass(frozen=True, slots=True)
class AssistantProviderConfig:
    endpoint: str
    model: str
    api_key: str


class HttpChatBriefingProvider:
    """Call one OpenAI-compatible Chat Completions endpoint without tools."""

    name = "openai_compatible_chat"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: float = DEFAULT_ASSISTANT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_ASSISTANT_MAX_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").lower().rstrip(".")
        normalized_model = model.strip()
        normalized_path = parsed.path.rstrip("/").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("assistant endpoint must be an absolute HTTP(S) URL")
        if host in {"ipsapro.isstech.com", "passport.isstech.com"}:
            raise ValueError("workflow target hosts cannot be used as assistant endpoints")
        if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("non-loopback assistant endpoints must use HTTPS")
        if "/v1/images/" in f"{normalized_path}/" or normalized_path.endswith(
            "/v1/images"
        ):
            raise ValueError("image endpoints cannot be used for the text assistant")
        if not normalized_model:
            raise ValueError("assistant model is required")
        if normalized_model.casefold() == "gpt-image-2":
            raise ValueError("gpt-image-2 cannot be used for the text assistant")
        if not api_key.strip():
            raise ValueError("assistant API key is required")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("assistant provider limits must be positive")
        self.endpoint = endpoint
        self.model = normalized_model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport

    def prioritize(
        self,
        candidates: tuple[AssistantCandidate, ...],
        preferences: tuple[str, ...],
    ) -> ModelBriefing:
        allowed_keys = {candidate.item_key for candidate in candidates}
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是只读催办排序器。只能在给定 item_key 中排序，不能创建任务，"
                        "不能建议提交、审批或修改业务数据。只返回 JSON 对象："
                        '{"summary":"不超过120字","priorities":'
                        '[{"item_key":"输入中的键","reason":"不超过60字"}]}。'
                        "priorities 最多5项，不得输出其他字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "preferences": list(preferences),
                            "candidates": [
                                {
                                    "item_key": candidate.item_key,
                                    "category": candidate.category,
                                    "reference": candidate.reference,
                                    "title": candidate.title,
                                    "project": candidate.project,
                                    "status": candidate.status,
                                    "current_approver": candidate.current_approver,
                                    "submitted_at": candidate.submitted_at,
                                    "waiting_days": candidate.waiting_days,
                                    "waiting_basis": candidate.waiting_basis.value,
                                }
                                for candidate in candidates
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            with client.stream(
                "POST",
                self.endpoint,
                headers=headers,
                json=request_body,
            ) as response:
                response.raise_for_status()
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > self.max_response_bytes:
                            raise BriefingProviderError(
                                "assistant response exceeds configured limit"
                            )
                    except ValueError:
                        pass
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise BriefingProviderError(
                            "assistant response exceeds configured limit"
                        )
        return _parse_chat_response(bytes(content), allowed_keys=allowed_keys)


def _parse_chat_response(content: bytes, *, allowed_keys: set[str]) -> ModelBriefing:
    try:
        response_payload = json.loads(content)
        choices = response_payload["choices"]
        raw_content = choices[0]["message"]["content"]
        payload = json.loads(raw_content)
    except (
        IndexError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise BriefingProviderError("assistant did not return the required JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"summary", "priorities"}:
        raise BriefingProviderError("assistant JSON fields are invalid")
    summary = payload.get("summary")
    raw_priorities = payload.get("priorities")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary.strip()) > 120
        or not isinstance(raw_priorities, list)
    ):
        raise BriefingProviderError("assistant summary or priorities are invalid")
    found: list[ModelPriority] = []
    seen: set[str] = set()
    for raw in raw_priorities:
        if not isinstance(raw, dict) or set(raw) != {"item_key", "reason"}:
            raise BriefingProviderError("assistant priority shape is invalid")
        item_key = raw.get("item_key")
        reason = raw.get("reason")
        if not isinstance(item_key, str) or not isinstance(reason, str):
            raise BriefingProviderError("assistant priority values are invalid")
        normalized_reason = " ".join(reason.split())
        if (
            item_key not in allowed_keys
            or item_key in seen
            or not normalized_reason
        ):
            continue
        if len(normalized_reason) > 60:
            raise BriefingProviderError("assistant priority reason is too long")
        seen.add(item_key)
        found.append(ModelPriority(item_key=item_key, reason=normalized_reason))
        if len(found) == MAX_MODEL_PRIORITIES:
            break
    if allowed_keys and not found:
        raise BriefingProviderError("assistant returned no known priority keys")
    return ModelBriefing(summary=summary.strip(), priorities=tuple(found))


def assistant_provider_config(
    *,
    account: str | None = None,
    credential_reader=None,
) -> AssistantProviderConfig | None:
    endpoint = os.getenv("ISSTECH_ASSISTANT_ENDPOINT", "").strip()
    model = os.getenv("ISSTECH_ASSISTANT_MODEL", "").strip()
    api_key = os.getenv("ISSTECH_ASSISTANT_API_KEY", "")
    if endpoint or model or api_key:
        if endpoint and model and api_key:
            return AssistantProviderConfig(endpoint=endpoint, model=model, api_key=api_key)
        return None
    if not account or credential_reader is None:
        return None
    try:
        endpoint = credential_reader(ASSISTANT_ENDPOINT_SERVICE, account, 10.0).strip()
        model = credential_reader(ASSISTANT_MODEL_SERVICE, account, 10.0).strip()
        api_key = credential_reader(ASSISTANT_API_KEY_SERVICE, account, 10.0)
    except Exception:
        return None
    if not endpoint or not model or not api_key:
        return None
    return AssistantProviderConfig(endpoint=endpoint, model=model, api_key=api_key)


def provider_from_config(
    config: AssistantProviderConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> HttpChatBriefingProvider:
    return HttpChatBriefingProvider(
        endpoint=config.endpoint,
        model=config.model,
        api_key=config.api_key,
        transport=transport,
    )
