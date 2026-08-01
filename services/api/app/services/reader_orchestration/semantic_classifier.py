"""Deterministic semantic role classifier for Stable Document blocks.

This module is the **single classification seam** for content-role
assignment. Parser / candidate / normalizer must import and call it;
they must not copy classification rules.

Contract versions:
  - ``SEMANTIC_CONTRACT_V1`` — first frozen contract (``semrules_v1`` rules).

Legacy activation:
  - A block is **legacy** only when ``payload_json.semantic.contract_version``
    is missing. ``content_role is None`` is NOT legacy (code/table/heading
    intentionally carry a contract marker with null role).

First-round enforce roles:
  - ``link_only`` (explicit link coverage ≥ 90%)
  - ``citation_reference`` (References/Bibliography section only)
  - structural types (heading/code/table/…) receive contract markers with
    ``content_role=None``

Shadow / fail-open only (no policy change):
  - quotation without strong signals
  - prompt_question
  - bibliography without a titled section
  - source_callout without an explicit marker
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

SEMANTIC_CONTRACT_V1: Final[str] = "semantic_contract_v1"
SEMRULES_V1: Final[str] = "semrules_v1"

ContentRole = Literal[
    "prose",
    "quotation",
    "source_callout",
    "citation_reference",
    "prompt_question",
    "link_only",
]

CONTENT_ROLES: Final[frozenset[str]] = frozenset(
    {
        "prose",
        "quotation",
        "source_callout",
        "citation_reference",
        "prompt_question",
        "link_only",
    }
)

# Structural block types already fully expressed by block_type; role stays null
# but they still receive a contract_version marker on new freezes.
_STRUCTURAL_NULL_ROLE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "heading",
        "code_block",
        "table",
        "table_row",
        "table_cell",
        "thematic_break",
        "image",
        "image_ocr",
        "footnote",
        "unknown",
        "list",  # wrapper
        "caption",
    }
)

_ROLE_BEARING_TYPES: Final[frozenset[str]] = frozenset(
    {
        "paragraph",
        "blockquote",
        "list_item",
    }
)

_REFERENCE_SECTION_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*("
    r"references"
    r"|reference\s+list(?:\s*\([^)]*\))?"
    r"|bibliography"
    r"|works\s+cited"
    r"|参考文献"
    r"|引用文献"
    r")\s*$",
    re.IGNORECASE,
)

_GFM_ALERT_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|ABSTRACT|INFO)\]\s*",
    re.IGNORECASE,
)

# Stable payload hints written by the Markdown parser for callout sources.
# Classifier is the only consumer of these codes.
SOURCE_SEMANTIC_HINT_HTML_ASIDE: Final[str] = "html_aside"
SOURCE_SEMANTIC_HINT_GFM_ALERT: Final[str] = "gfm_alert"

_PUNCT_RE: Final[re.Pattern[str]] = re.compile(r"[\s\W_]+", re.UNICODE)

# Shadow-only weak signals (never alone change enforced policy).
_NUMBERED_CITATION_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\[\d+\]|\d+\.)\s+\S+"
)
_QUESTION_TAIL_RE: Final[re.Pattern[str]] = re.compile(r"[?？]\s*$")


@dataclass(frozen=True, slots=True)
class SemanticClassification:
    """Frozen classification result written under ``payload_json.semantic``."""

    contract_version: str
    content_role: ContentRole | None
    source: Literal["deterministic", "llm_fallback", "user_confirmed"]
    confidence: float
    rules_version: str
    signals: tuple[str, ...]
    # When True the role is observational only; resolver must treat the block
    # as prose/fail-open for automatic policy (question / ambiguous quote).
    shadow_only: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "content_role": self.content_role,
            "classification": {
                "source": self.source,
                "confidence": self.confidence,
                "rules_version": self.rules_version,
                "signals": list(self.signals),
            },
        }
        if self.shadow_only:
            payload["classification"]["shadow_only"] = True
        return payload


def extract_contract_version(payload_json: Mapping[str, Any] | None) -> str | None:
    """Return semantic contract_version, or None for legacy blocks."""
    if not payload_json:
        return None
    semantic = payload_json.get("semantic")
    if not isinstance(semantic, Mapping):
        return None
    version = semantic.get("contract_version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def extract_content_role(payload_json: Mapping[str, Any] | None) -> str | None:
    """Return content_role when present and in the closed set; else None."""
    if not payload_json:
        return None
    semantic = payload_json.get("semantic")
    if not isinstance(semantic, Mapping):
        return None
    role = semantic.get("content_role")
    if role is None:
        return None
    if isinstance(role, str) and role in CONTENT_ROLES:
        return role
    return None


def is_legacy_semantic(payload_json: Mapping[str, Any] | None) -> bool:
    """Legacy iff contract_version is missing — role nullity is irrelevant."""
    return extract_contract_version(payload_json) is None


def is_shadow_only_classification(payload_json: Mapping[str, Any] | None) -> bool:
    if not payload_json:
        return False
    semantic = payload_json.get("semantic")
    if not isinstance(semantic, Mapping):
        return False
    classification = semantic.get("classification")
    if not isinstance(classification, Mapping):
        return False
    return bool(classification.get("shadow_only"))


def _normalize_for_coverage(text: str) -> str:
    return _PUNCT_RE.sub("", text).casefold()


def _link_coverage_ratio(text: str, links: Sequence[Mapping[str, Any]]) -> float:
    """Fraction of non-punctuation text covered by link display texts."""
    if not text or not links:
        return 0.0
    normalized_text = _normalize_for_coverage(text)
    if not normalized_text:
        return 0.0
    covered_parts: list[str] = []
    for link in links:
        if not isinstance(link, Mapping):
            continue
        # Parser stores ``text`` (display) and optionally ``href``.
        display = link.get("text") or link.get("title") or ""
        if not isinstance(display, str):
            continue
        part = _normalize_for_coverage(display)
        if part:
            covered_parts.append(part)
    if not covered_parts:
        return 0.0
    # Union coverage via greedy multi-occurrence removal.
    remaining = normalized_text
    covered_chars = 0
    for part in sorted(covered_parts, key=len, reverse=True):
        while part and part in remaining:
            remaining = remaining.replace(part, "", 1)
            covered_chars += len(part)
    return covered_chars / len(normalized_text)


def _heading_text(block: Mapping[str, Any]) -> str:
    text = block.get("text_content")
    return text.strip() if isinstance(text, str) else ""


def _nearest_preceding_heading_text(
    blocks: Sequence[Mapping[str, Any]],
    index: int,
) -> str | None:
    for i in range(index - 1, -1, -1):
        block = blocks[i]
        if block.get("block_type") == "heading":
            text = _heading_text(block)
            return text or None
    return None


def _classify_one(
    *,
    block: Mapping[str, Any],
    index: int,
    blocks: Sequence[Mapping[str, Any]],
) -> SemanticClassification:
    block_type = str(block.get("block_type") or "")
    text = block.get("text_content") if isinstance(block.get("text_content"), str) else ""
    payload = block.get("payload_json") if isinstance(block.get("payload_json"), Mapping) else {}

    if block_type in _STRUCTURAL_NULL_ROLE_TYPES:
        return SemanticClassification(
            contract_version=SEMANTIC_CONTRACT_V1,
            content_role=None,
            source="deterministic",
            confidence=1.0,
            rules_version=SEMRULES_V1,
            signals=(f"structural:{block_type}",),
        )

    if block_type not in _ROLE_BEARING_TYPES:
        # Unknown / future types: contract marker, no role.
        return SemanticClassification(
            contract_version=SEMANTIC_CONTRACT_V1,
            content_role=None,
            source="deterministic",
            confidence=1.0,
            rules_version=SEMRULES_V1,
            signals=(f"unhandled_type:{block_type or 'empty'}",),
        )

    signals: list[str] = []
    hint = payload.get("source_semantic_hint")

    # --- enforce: Notion/HTML <aside> or GFM alert via stable parser hint ---
    # Parser is the only writer of these hints; classifier is the only role
    # seam. Structural containers carry text_content=None, so the classifier
    # must rely on the hint (not text matching) to route to source_callout.
    if hint in (SOURCE_SEMANTIC_HINT_HTML_ASIDE, SOURCE_SEMANTIC_HINT_GFM_ALERT):
        signals.append(f"source_semantic_hint:{hint}")
        return SemanticClassification(
            contract_version=SEMANTIC_CONTRACT_V1,
            content_role="source_callout",
            source="deterministic",
            confidence=1.0,
            rules_version=SEMRULES_V1,
            signals=tuple(signals),
        )

    # --- enforce: explicit GFM alert marker → source_callout ---
    if block_type == "blockquote" and text and _GFM_ALERT_MARKER_RE.match(text):
        signals.append("gfm_alert_marker")
        return SemanticClassification(
            contract_version=SEMANTIC_CONTRACT_V1,
            content_role="source_callout",
            source="deterministic",
            confidence=1.0,
            rules_version=SEMRULES_V1,
            signals=tuple(signals),
        )

    # --- enforce: ordinary Markdown blockquote → quotation (T-only via policy) ---
    # Structural blockquote always gets an enforced quotation role so automatic
    # V/G/S cannot fail-open. Further callout discrimination requires markers.
    if block_type == "blockquote":
        signals.append("blockquote_structure")
        return SemanticClassification(
            contract_version=SEMANTIC_CONTRACT_V1,
            content_role="quotation",
            source="deterministic",
            confidence=1.0,
            rules_version=SEMRULES_V1,
            signals=tuple(signals),
            shadow_only=False,
        )

    # --- enforce: link_only (paragraph with high link coverage) ---
    if block_type == "paragraph":
        links = payload.get("links") if isinstance(payload.get("links"), list) else []
        coverage = _link_coverage_ratio(text or "", links)
        if links and coverage >= 0.90:
            signals.append("link_coverage_ge_90")
            return SemanticClassification(
                contract_version=SEMANTIC_CONTRACT_V1,
                content_role="link_only",
                source="deterministic",
                confidence=1.0,
                rules_version=SEMRULES_V1,
                signals=tuple(signals),
            )

    # --- enforce: citation_reference under References/Bibliography heading ---
    if block_type in {"paragraph", "list_item"}:
        section = _nearest_preceding_heading_text(blocks, index)
        if section and _REFERENCE_SECTION_HEADING_RE.match(section):
            signals.append("section_heading:references")
            return SemanticClassification(
                contract_version=SEMANTIC_CONTRACT_V1,
                content_role="citation_reference",
                source="deterministic",
                confidence=1.0,
                rules_version=SEMRULES_V1,
                signals=tuple(signals),
            )
        # Weak citation signals → shadow only (no policy change → prose fail-open).
        if text and _NUMBERED_CITATION_PREFIX_RE.match(text):
            signals.append("weak_numbered_citation_prefix")
            return SemanticClassification(
                contract_version=SEMANTIC_CONTRACT_V1,
                content_role="citation_reference",
                source="deterministic",
                confidence=0.5,
                rules_version=SEMRULES_V1,
                signals=tuple(signals),
                shadow_only=True,
            )

    # --- shadow: prompt_question ---
    if block_type == "paragraph" and text and _QUESTION_TAIL_RE.search(text):
        word_count = len(text.split())
        if word_count <= 40:
            signals.append("question_mark_tail")
            return SemanticClassification(
                contract_version=SEMANTIC_CONTRACT_V1,
                content_role="prompt_question",
                source="deterministic",
                confidence=0.5,
                rules_version=SEMRULES_V1,
                signals=tuple(signals),
                shadow_only=True,
            )

    # Default prose for role-bearing types.
    signals.append("default_prose")
    return SemanticClassification(
        contract_version=SEMANTIC_CONTRACT_V1,
        content_role="prose",
        source="deterministic",
        confidence=1.0,
        rules_version=SEMRULES_V1,
        signals=tuple(signals),
    )


def classify_blocks(
    blocks: Sequence[Mapping[str, Any]],
) -> list[SemanticClassification]:
    """Classify every block, then inherit explicit callout semantics.

    ``source_callout`` is a semantic role for the visible content inside the
    callout, not a reason to flatten that content into the wrapper.  Wrapper
    rows keep their structural-null role; text-bearing descendants inherit
    the deterministic role so their automatic layer policy remains T-only
    after the generic Stable Block → Unit projection.
    """
    classifications = [
        _classify_one(block=block, index=index, blocks=blocks)
        for index, block in enumerate(blocks)
    ]
    by_id = {
        block.get("block_id"): index
        for index, block in enumerate(blocks)
        if block.get("block_id")
    }

    for index, block in enumerate(blocks):
        if block.get("block_type") not in _ROLE_BEARING_TYPES:
            continue
        parent_id = block.get("parent_block_id")
        seen: set[object] = set()
        inherited = False
        while parent_id is not None and parent_id not in seen:
            seen.add(parent_id)
            parent_index = by_id.get(parent_id)
            if parent_index is None:
                break
            parent_classification = classifications[parent_index]
            if (
                parent_classification.content_role == "source_callout"
                and not parent_classification.shadow_only
            ):
                inherited = True
                break
            parent_id = blocks[parent_index].get("parent_block_id")
        if inherited and classifications[index].content_role != "source_callout":
            classifications[index] = replace(
                classifications[index],
                content_role="source_callout",
                source="deterministic",
                confidence=1.0,
                signals=(*classifications[index].signals, "inherited_source_callout"),
                shadow_only=False,
            )
    return classifications


def annotate_payload_with_semantic(
    payload_json: Mapping[str, Any] | None,
    classification: SemanticClassification,
) -> dict[str, Any]:
    """Return a new payload_json with ``semantic`` written (overwrite)."""
    payload = dict(payload_json or {})
    payload["semantic"] = classification.to_payload()
    return payload


def _block_as_mapping(block: Any) -> dict[str, Any]:
    """Project StableDocumentBlock / ParsedBlock / dict to a mapping view."""
    if isinstance(block, Mapping):
        return {
            "block_type": block.get("block_type"),
            "text_content": block.get("text_content"),
            "payload_json": block.get("payload_json") or {},
            "block_id": block.get("block_id"),
            "parent_block_id": block.get("parent_block_id"),
            "order_index": block.get("order_index"),
        }
    # Pydantic / dataclass style
    payload = getattr(block, "payload_json", None) or {}
    return {
        "block_type": getattr(block, "block_type", None),
        "text_content": getattr(block, "text_content", None),
        "payload_json": payload if isinstance(payload, Mapping) else {},
        "block_id": getattr(block, "block_id", None),
        "parent_block_id": getattr(block, "parent_block_id", None),
        "order_index": getattr(block, "order_index", None),
    }


def annotate_blocks_with_semantic(blocks: Sequence[Any]) -> list[Any]:
    """Annotate a sequence of StableDocumentBlock-like objects in place-safe way.

    Returns a new list. For Pydantic ``StableDocumentBlock`` models, returns
    model copies with updated ``payload_json``. For plain dicts, returns
    deep-copied dicts. Unknown types with ``model_copy`` / ``copy`` fall back
    to attribute mutation on a shallow clone when possible.
    """
    if not blocks:
        return []

    projections = [_block_as_mapping(b) for b in blocks]
    classifications = classify_blocks(projections)
    annotated: list[Any] = []

    for block, classification in zip(blocks, classifications, strict=True):
        new_payload = annotate_payload_with_semantic(
            projections[len(annotated)].get("payload_json"),
            classification,
        )
        if isinstance(block, Mapping) and not hasattr(block, "model_copy"):
            cloned = deepcopy(dict(block))
            cloned["payload_json"] = new_payload
            annotated.append(cloned)
            continue

        if hasattr(block, "model_copy"):
            # Pydantic v2 StableDocumentBlock
            annotated.append(block.model_copy(update={"payload_json": new_payload}))
            continue

        # Dataclass / simple object: try replace via constructor fields.
        try:
            from dataclasses import is_dataclass
            from dataclasses import replace as dc_replace

            if is_dataclass(block) and not isinstance(block, type):
                annotated.append(dc_replace(block, payload_json=new_payload))
                continue
        except (TypeError, ValueError):
            pass

        # Last resort: mutate a shallow-copied __dict__ object is unsafe;
        # raise so callers fix the type boundary.
        raise TypeError(
            f"annotate_blocks_with_semantic cannot annotate block type "
            f"{type(block)!r}; pass StableDocumentBlock or dict"
        )

    return annotated


def attach_semantic_to_stable_blocks(blocks: Iterable[Any]) -> list[Any]:
    """Public seam used by normalizer / candidate / freeze callers."""
    return annotate_blocks_with_semantic(list(blocks))
