"""Deterministic minimization of private reasoning before projector input.

Scrub reduces secrets, handles, URLs, and internal IDs. It does **not**
guarantee that authorized article text or the user question are absent.
This is provider-local retransmission, not a claim of zero article/query
leakage.
"""

from __future__ import annotations

import re

# Reuse patterns aligned with reasoning_projection redaction (ASCII lookarounds).
_IDENTITY_KEYS = (
    r"envelope_fingerprint|content_sha256|stable_document_id|"
    r"reading_record_id|analysis_record_id|record_id|base_id|generation|"
    r"user_id|turn_run_id|thread_id|message_id|handle_id|rag_substrate_id|"
    r"fingerprint|provider_response_id"
)

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9_])evh_[0-9A-Fa-f]{8,64}"), ""),
    (re.compile(r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._\-]{12,}"), ""),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{12,}"), ""),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9_])(?:api[_-]?key|access[_-]?token|secret|"
            r"authorization)\s*[:=：]\s*['\"]?(?:(?=[!-~])[^'\",;)}\]])+)"
        ),
        "",
    ),
    (
        re.compile(
            rf"(?<![A-Za-z0-9_])(?:{_IDENTITY_KEYS})"
            r"\s*[:=：]\s*['\"]?(?:(?=[!-~])[^'\",;)}\]])+"
        ),
        "",
    ),
    (
        re.compile(
            r"(?<![0-9A-Fa-f])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            r"(?![0-9A-Fa-f-])"
        ),
        "",
    ),
    (
        re.compile(r"(?<![A-Za-z0-9])https?://(?:(?=[!-~])[^<>\s\"')\]])+"),
        "",
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), ""),
    (re.compile(r"(?i:<\/?\|?\s*think(?:ing)?\s*\|?>)"), ""),
    (
        re.compile(
            r"(?m:^(?:You are Claread|## Answer correctness|## Tools|"
            r"## Evidence|## Output contract|SYSTEM:|<system>)[^\n]*)"
        ),
        "",
    ),
    # Long quoted article-like blocks: collapse very long single lines.
    (re.compile(r"(?m:^.{600,}$)"), "〔长引用已省略〕"),
)


def scrub_private_reasoning_for_projector(text: str) -> str:
    """Return a minimized window safe enough to retransmit to the projector.

    Never log the input or output. Empty after scrub → caller must skip
    dispatch.
    """
    if not text:
        return ""
    out = text
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    # Collapse excessive whitespace created by removals.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


__all__ = ["scrub_private_reasoning_for_projector"]
