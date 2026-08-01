"""Fail-closed validation of projector model output and cold payloads."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.services.reader_record_ask.learner_reasoning.schemas import (
    LEARNER_REASONING_POLICY_VERSION,
    LEARNER_REASONING_SCHEMA_VERSION,
    TEXT_ZH_MAX_CHARS,
    TEXT_ZH_MIN_CHARS,
    LearnerReasoningDraft,
)

_URL_RE = re.compile(r"https?://", re.I)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]+\)")
_HTML_RE = re.compile(r"<[^>]+>")
_EVH_RE = re.compile(r"(?<![A-Za-z0-9_])evh_[0-9A-Fa-f]{6,}", re.I)
_BEARER_RE = re.compile(r"Bearer\s+\S+", re.I)
_SK_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_PROVIDER_LEAK_RE = re.compile(
    r"(?i:\b(?:deepseek|qwen|dashscope|anthropic|openai|pydantic|"
    r"provider|api\s*key|token|evh_|turn_run|message_id|tool_name)\b)"
)
_INJECTION_RE = re.compile(
    r"(?i:(?:ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"忽略(?:之前|以上)?(?:所有)?指令|你必须|system\s*:))"
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_VALID_STAGES = frozenset({"analyzing", "article", "web", "synthesizing"})
_VALID_BASIS = frozenset({"article", "web", "general"})


def _grapheme_len(text: str) -> int:
    normalized = unicodedata.normalize("NFKC", text)
    cleaned = "".join(
        ch for ch in normalized if unicodedata.category(ch) not in {"Cf", "Cc"}
    )
    return len(cleaned)


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(
        1
        for ch in text
        if "\u4e00" <= ch <= "\u9fff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\uf900" <= ch <= "\ufaff"
    )
    return cjk / max(len(text), 1)


def validate_learner_text_zh(text: str) -> str | None:
    """Return cleaned text or None when invalid (fail-closed)."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if "\n" in cleaned or "\r" in cleaned:
        return None
    if _CTRL_RE.search(cleaned):
        return None
    length = _grapheme_len(cleaned)
    if length < TEXT_ZH_MIN_CHARS or length > TEXT_ZH_MAX_CHARS:
        return None
    if _cjk_ratio(cleaned) < 0.5:
        return None
    if _URL_RE.search(cleaned):
        return None
    if _MD_LINK_RE.search(cleaned):
        return None
    if _HTML_RE.search(cleaned):
        return None
    if _EVH_RE.search(cleaned):
        return None
    if _BEARER_RE.search(cleaned) or _SK_RE.search(cleaned):
        return None
    if _UUID_RE.search(cleaned):
        return None
    if _PROVIDER_LEAK_RE.search(cleaned):
        return None
    if _INJECTION_RE.search(cleaned):
        return None
    return cleaned


def validate_learner_draft(draft: LearnerReasoningDraft | object) -> str | None:
    text = getattr(draft, "text_zh", None)
    if not isinstance(text, str):
        return None
    return validate_learner_text_zh(text)


def validate_cold_learner_payload(
    payload: dict[str, Any] | None,
) -> tuple[str | None, str | None, list[str] | None]:
    """Validate a persisted learner_reasoning_v1 payload for cold restore.

    Returns ``(text, stage, basis)`` or ``(None, None, None)`` fail-closed.
    """
    if not isinstance(payload, dict):
        return None, None, None
    policy = payload.get("projection_policy_version") or payload.get(
        "policy_version"
    )
    if policy != LEARNER_REASONING_POLICY_VERSION:
        return None, None, None
    schema = payload.get("schema")
    if schema is not None and schema != LEARNER_REASONING_SCHEMA_VERSION:
        return None, None, None
    text = validate_learner_text_zh(str(payload.get("text") or ""))
    if text is None:
        return None, None, None
    stage = payload.get("stage")
    if stage not in _VALID_STAGES:
        return None, None, None
    basis_raw = payload.get("basis")
    if basis_raw is None:
        basis_list: list[str] = []
    elif not isinstance(basis_raw, list):
        return None, None, None
    else:
        basis_list = []
        for item in basis_raw:
            if item not in _VALID_BASIS:
                return None, None, None
            basis_list.append(str(item))
    revision = payload.get("revision")
    sequence = payload.get("sequence")
    if not isinstance(revision, int) or revision < 1:
        return None, None, None
    if not isinstance(sequence, int) or sequence < 1:
        return None, None, None
    return text, str(stage), basis_list


__all__ = [
    "validate_cold_learner_payload",
    "validate_learner_draft",
    "validate_learner_text_zh",
]
