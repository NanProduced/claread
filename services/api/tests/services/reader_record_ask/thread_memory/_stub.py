"""A1 stub: 待 A1 完成后移除.

This module provides fallback Pydantic models and mapping helpers that
mirror the contracts A1 will deliver in
``app/services/reader_record_ask/thread_memory/schema.py`` and
``app/services/reader_record_ask/thread_memory/mapping.py``.

A2's production modules (emergency.py / allowlist.py / fence.py /
render.py / redaction.py) import the real schema/mapping modules by the
agreed interface. When A1's modules are not yet on disk, the test
conftest injects this stub into ``sys.modules`` so A2's tests can run
independently.

Once A1 lands ``schema.py`` and ``mapping.py``, the conftest injection
becomes a no-op (real imports win) and this stub can be deleted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Schema mirror (R0.1 §6)
# ---------------------------------------------------------------------------


class StructuredFact(BaseModel):
    """One structured fact extracted from a canonical turn segment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str
    text: str = Field(max_length=280)
    source_type: Literal[
        "article",
        "web",
        "user_correction",
        "user_question",
        "assistant_answer",
        "prior_mention",
    ]
    source_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "prior_context"]
    turn_origin: int
    supersedes: list[str] | None = None
    protected: bool = False
    # Mark facts that must survive budget shrinking (user_correction /
    # unresolved_question). R0.1 §4.2(f): protected facts are never
    # evicted; only ``prior_context`` and ``medium`` facts participate.


class SourceBinding(BaseModel):
    """Host-derived source binding (compactor never creates bindings)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str
    source_type: Literal["article", "web"]
    source_id: str
    fence_type: Literal[
        "reading_record", "stable_document", "base", "generation"
    ]
    fence_values: dict[str, Any] = Field(default_factory=dict)
    validity_check: dict[str, Any] = Field(default_factory=dict)


class Episode(BaseModel):
    """Append-only episode covering a closed canonical turn range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str
    turn_range: dict[str, int]  # {"start": int, "end": int}
    structured_facts: list[StructuredFact] = Field(default_factory=list)
    source_bindings: list[SourceBinding] = Field(default_factory=list)
    excluded_content_markers: list[str] = Field(default_factory=list)
    compaction_model: Literal["deepseek-v4-flash", "none"]
    compaction_method: Literal["model", "emergency_deterministic", "hybrid"]
    compaction_timestamp: str
    compaction_input_watermark: str = ""


class ThreadMemorySnapshot(BaseModel):
    """Derived read-only memory view for one Ask thread."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["thread_memory_v1"] = "thread_memory_v1"
    watermark: str
    thread_id: str
    created_at: str
    last_compacted_at: str | None = None
    last_compaction_stats: dict[str, Any] | None = None
    episodes: list[Episode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Mapping mirror (R0.1 §4.2(d) step 3, H6/H7)
# ---------------------------------------------------------------------------


def derive_source_bindings(
    turn_run_bindings: list[Any],
) -> list[SourceBinding]:
    """Stub: turn InternalCitationBinding-like dicts into SourceBindings.

    A1 will implement the real mapping from ``resolved_evidence_json``
    structures (``InternalCitationBinding`` + ``ArticleRagCitationEvidence``
    fields). This stub covers the minimum field set A2's emergency
    extractor and allowlist builder consume.

    ``turn_run_bindings`` items may be Pydantic models with ``model_dump``
    or plain dicts. The stub normalizes to dict, then derives:

    - article bindings from ``rag_citation`` (stable_document_id /
      base_id / record_generation / reading_record_id)
    - web bindings from top-level ``canonical_url`` (source_id) plus a
      ``web`` fence_values entry
    """
    out: list[SourceBinding] = []
    for raw in turn_run_bindings or []:
        if hasattr(raw, "model_dump"):
            b = raw.model_dump()
        elif isinstance(raw, dict):
            b = dict(raw)
        else:
            continue
        binding_id = str(
            b.get("citation_id") or b.get("handle_id") or ""
        ).strip()
        if not binding_id:
            continue
        source_kind = str(b.get("source_kind") or "article")
        if source_kind not in ("article", "web"):
            source_kind = "article"

        rag_dict = b.get("rag_citation")
        if not isinstance(rag_dict, dict):
            rag_dict = {}

        if source_kind == "web":
            source_id = str(
                b.get("canonical_url") or b.get("url") or ""
            ).strip()
            fence_type = "reading_record"
            fence_values: dict[str, Any] = {
                "canonical_url": source_id,
                "source_fingerprint": b.get("source_fingerprint"),
                "retrieved_at": b.get("retrieved_at"),
            }
        else:
            source_id = str(
                rag_dict.get("stable_document_id")
                or b.get("stable_document_id")
                or ""
            ).strip()
            fence_type = "stable_document"
            fence_values = {
                "reading_record_id": str(
                    rag_dict.get("reading_record_id")
                    or b.get("reading_record_id")
                    or ""
                ),
                "stable_document_id": source_id,
                "base_id": str(
                    rag_dict.get("base_id") or b.get("base_id") or ""
                ),
                "record_generation": rag_dict.get("record_generation")
                or b.get("record_generation"),
            }

        out.append(
            SourceBinding(
                binding_id=binding_id,
                source_type=source_kind,  # type: ignore[arg-type]
                source_id=source_id,
                fence_type=fence_type,  # type: ignore[arg-type]
                fence_values=fence_values,
                validity_check={
                    "status": "unchecked",
                    "last_validated_turn": 0,
                },
            )
        )
    return out


def degrade_web_citation_to_hint(binding: Any) -> dict[str, Any]:
    """Stub: produce a free-text web hint from a binding.

    Returns a dict (not a SourceBinding) so callers can render the hint
    inline without elevating it to citation-truth status. ``A_bind``
    allowlist must NOT include degraded hints (R0.1 H7).
    """
    if hasattr(binding, "model_dump"):
        b = binding.model_dump()
    elif isinstance(binding, dict):
        b = dict(binding)
    else:
        b = {}
    url = str(
        b.get("canonical_url")
        or b.get("source_id")
        or b.get("url")
        or ""
    )
    title = str(
        b.get("web_title")
        or b.get("title")
        or ""
    )
    retrieved = str(b.get("retrieved_at") or "")
    text_parts: list[str] = []
    if title:
        text_parts.append(title)
    if url:
        text_parts.append(url)
    if retrieved:
        text_parts.append(f"retrieved {retrieved}")
    return {
        "hint_text": " | ".join(text_parts) or "prior web source",
        "canonical_url": url,
        "retrieved_at": retrieved,
    }
