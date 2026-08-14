"""Emergency deterministic compaction (Host fallback, no LLM call).

Implements §4.2(e) deterministic extraction step ``facts_det``:
pure-Python structured field搬运 from canonical messages and ok turn
runs into an :class:`Episode`. Mirrors the legacy
``build_structured_history_summary`` pattern (``reader_ask/runtime_contract.py``).

Emergency path is the always-available fallback when the model compactor
fails or is unavailable (frozen decision #4). It MUST succeed for any
canonical input shape — there is no further fallback below this layer
except ``window_shrink`` (which discards memory entirely).

Excluded content categories are declared (not embedded) via
``excluded_content_markers``; emergency never reads reasoning_projection,
tool_trace_json, raw provider payload, evh_ handles, or secrets.

 ``_compute_watermark`` duplicate removed — emergency now
calls the shared :func:`allowlist.compute_watermark` so watermark
semantics are identical across emergency and coordinator paths.

 ``_extract_assistant_answer_facts`` now reads
``citation_ids`` from answer blocks. Blocks with article citations
produce ``source_type='article'`` facts (high confidence); blocks
without citations produce ``source_type='assistant_answer'`` facts
(medium confidence — never high, to avoid冒充 article-grounded).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.services.reader_record_ask.thread_memory.allowlist import (
    compute_watermark,
)
from app.services.reader_record_ask.thread_memory.mapping import (
    derive_source_bindings,
)
from app.services.reader_record_ask.thread_memory.redaction import (
    redact_for_compaction_input,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
)

# Canonical turn range for the recent verbatim window (§5).
# Emergency uses this to split aged vs. recent segments.
_RECENT_PAIRS_DEFAULT: int = 6

# Fact text truncation (§6 StructuredFact.text ≤ 280 chars).
_FACT_TEXT_MAX: int = 280

# Markers explicitly excluded by emergency compaction ( §6 episode
# excluded_content_markers closed set).
_EXCLUDED_MARKERS: tuple[str, ...] = (
    "reasoning",
    "raw_tool_payload",
    "failed_drafts",
    "secrets",
    "evh_handles",
)

# Heuristic user-correction triggers (§4.2(e) facts_det). The
# compactor model would do intent classification; emergency uses a
# keyword regex that errs on the side of marking a turn as correction
# (protected facts are never evicted during budget shrinking).
_USER_CORRECTION_RE = re.compile(
    r"(?:不对|纠正|其实|应该是|不是.*?是|说错了|改一下|更正|修正)"
)


def _now_iso_utc() -> str:
    return datetime.now(UTC).isoformat()


def _truncate_fact_text(text: str) -> str:
    if len(text) <= _FACT_TEXT_MAX:
        return text
    return text[: _FACT_TEXT_MAX - 1].rstrip() + "…"


def _redact(text: str) -> str:
    """Apply second-layer redaction to one compaction input text.

    Returns only the redacted text (metrics are discarded — emergency is
    deterministic and any hit here is an upstream incident, logged by the
    redaction layer itself). This is defense in depth: the upstream
    ``IncrementalRedactor`` should already have removed secrets; any hit
    here means an upstream leak into canonical messages.
    """
    redacted, _metrics = redact_for_compaction_input(text)
    return redacted


def _is_user_correction(text: str) -> bool:
    if not text:
        return False
    return bool(_USER_CORRECTION_RE.search(text))


def _extract_assistant_answer_facts(
    message: dict[str, Any],
    *,
    turn_origin: int,
    host_bindings: dict[str, SourceBinding] | None = None,
) -> list[StructuredFact]:
    """Extract one StructuredFact per answer_block from an ok assistant msg.

    ``answer_blocks`` may be present on the message DTO
    (``ReaderRecordAskCompletedDTO.answer_blocks``) or as part of a
    nested ``user_visible_output`` payload. Emergency handles both
    shapes; it never reads ``reasoning_projection_json`` or
    ``tool_trace_json``.

     blocks with ``citation_ids`` that map to Host article
    bindings produce ``source_type='article'`` facts (high confidence).
    Blocks without article citations produce
    ``source_type='assistant_answer'`` facts (medium confidence — never
    high, to avoid冒充 article-grounded). When ``host_bindings`` is
    None (emergency called without Host map), all blocks fall back to
    ``assistant_answer`` / medium.
    """
    msg_id = str(message.get("id") or message.get("message_id") or "")
    blocks: list[dict[str, Any]] = []
    raw_blocks = message.get("answer_blocks")
    if isinstance(raw_blocks, list):
        blocks = [b for b in raw_blocks if isinstance(b, dict)]
    else:
        visible = message.get("user_visible_output")
        if isinstance(visible, dict):
            inner = visible.get("answer_blocks")
            if isinstance(inner, list):
                blocks = [b for b in inner if isinstance(b, dict)]

    facts: list[StructuredFact] = []
    for index, block in enumerate(blocks):
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        # Second-layer redaction on assistant answer text.
        text = _redact(text)
        if not text:
            continue

        # Read citation_ids and classify.
        raw_citation_ids = block.get("citation_ids") or []
        if not isinstance(raw_citation_ids, list):
            raw_citation_ids = []
        citation_ids = [
            str(c) for c in raw_citation_ids if c
        ]

        if citation_ids and host_bindings is not None:
            # Check which citations map to Host article bindings.
            article_citations = [
                cid for cid in citation_ids
                if cid in host_bindings
                and host_bindings[cid].source_type == "article"
            ]
            if article_citations:
                # Article-grounded fact: high confidence.
                facts.append(
                    StructuredFact(
                        fact_id=f"{msg_id}_a{index}",
                        text=_truncate_fact_text(text),
                        source_type="article",
                        source_ids=article_citations,
                        confidence="high",
                        turn_origin=turn_origin,
                    )
                )
                continue
            # Citations exist but none map to article bindings → degrade
            # to prior_mention (cannot assert article provenance).
            facts.append(
                StructuredFact(
                    fact_id=f"{msg_id}_a{index}",
                    text=_truncate_fact_text(text),
                    source_type="prior_mention",
                    source_ids=citation_ids,
                    confidence="prior_context",
                    turn_origin=turn_origin,
                )
            )
            continue

        # No article citation (or no Host map) → general assistant
        # answer. Medium confidence — never high (不得冒充
        # article-grounded high confidence).
        facts.append(
            StructuredFact(
                fact_id=f"{msg_id}_a{index}",
                text=_truncate_fact_text(text),
                source_type="assistant_answer",
                source_ids=[msg_id] if msg_id else [],
                confidence="medium",
                turn_origin=turn_origin,
            )
        )
    return facts


def _extract_web_fact(
    message: dict[str, Any],
    *,
    turn_origin: int,
) -> StructuredFact | None:
    """Extract a single web-outcome fact from an ok assistant message.

    ``web_search_summary.outcome`` (PublicWebSearchSummary) is the only
    web-derived field admissible in emergency compaction. Web citations
    are degraded to hints elsewhere (H7); emergency does not pull them
    into ``structured_facts`` as citation truth.
    """
    msg_id = str(message.get("id") or message.get("message_id") or "")
    summary = message.get("web_search_summary") or message.get("web_search")
    if isinstance(summary, dict):
        outcome = summary.get("outcome")
    else:
        outcome = None
    if not outcome or not isinstance(outcome, str):
        return None
    # Second-layer redaction on web summary outcome text.
    outcome = _redact(outcome)
    if not outcome:
        return None
    text = f"搜索结果:{outcome}"
    return StructuredFact(
        fact_id=f"{msg_id}_web",
        text=_truncate_fact_text(text),
        source_type="web",
        source_ids=[msg_id] if msg_id else [],
        confidence="prior_context",
        turn_origin=turn_origin,
    )


def _extract_user_question_fact(
    message: dict[str, Any],
    *,
    turn_origin: int,
) -> StructuredFact | None:
    """Extract the user-question fact for one user message."""
    msg_id = str(message.get("id") or message.get("message_id") or "")
    text = str(
        message.get("content_md")
        or message.get("text")
        or message.get("user_message")
        or ""
    ).strip()
    if not text:
        return None
    # Second-layer redaction on user question text.
    text = _redact(text)
    if not text:
        return None
    is_correction = _is_user_correction(text)
    return StructuredFact(
        fact_id=f"{msg_id}_q",
        text=_truncate_fact_text(text),
        source_type="user_correction" if is_correction else "user_question",
        source_ids=[msg_id] if msg_id else [],
        confidence="high" if is_correction else "medium",
        turn_origin=turn_origin,
        protected=is_correction,
    )


def _collect_episode_bindings(
    ok_turn_runs: list[dict[str, Any]],
) -> list[SourceBinding]:
    """Derive SourceBindings from ok turn runs.

    Compactor (model or emergency) MUST NOT create bindings — only the
    Host does, via ``derive_source_bindings``. Emergency calls the same
    Host mapping layer so allowlist and fence checks see identical
    binding shapes.
    """
    bindings: list[SourceBinding] = []
    for run in ok_turn_runs or []:
        if not isinstance(run, dict):
            continue
        raw_bindings = (
            run.get("citation_bindings")
            or run.get("resolved_evidence_json")
            or []
        )
        if not isinstance(raw_bindings, list):
            continue
        bindings.extend(derive_source_bindings(raw_bindings))
    return bindings


def emergency_compact(
    canonical_messages: list[dict[str, Any]],
    ok_turn_runs: list[dict[str, Any]],
    *,
    turn_range: tuple[int, int],
    host_bindings: dict[str, SourceBinding] | None = None,
) -> Episode:
    """Deterministically compact one canonical segment into an Episode.

    Pure-Python structured extraction; no LLM call. Inputs are the
    canonical messages and ok turn runs **for the segment covered by
    ``turn_range``** (caller pre-filters). ``turn_range`` is a closed
    interval over canonical turn序号 ( §6: user message 1-based
    position by ``created_at ASC``; retry does not increment).

    The first user message in the input corresponds to ``turn_range[0]``.
    Each subsequent user message increments the turn序号 by 1. Assistant
    messages inherit the turn序号 of the preceding user message.

     ``Host_bindings`` is the Host binding map. When provided,
    answer blocks with article citations produce ``source_type='article'``
    facts; when None, all blocks fall back to ``assistant_answer``.

    Raises ``ValueError`` if ``turn_range`` is malformed.
    """
    start, end = turn_range
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("turn_range must be a tuple of two ints")
    if start < 1 or end < start:
        raise ValueError(
            f"turn_range malformed: got ({start}, {end}); "
            "expected start>=1 and end>=start"
        )

    facts: list[StructuredFact] = []
    current_turn = start - 1
    for message in canonical_messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role == "user":
            current_turn += 1
            fact = _extract_user_question_fact(message, turn_origin=current_turn)
            if fact is not None:
                facts.append(fact)
        elif role == "assistant":
            facts.extend(
                _extract_assistant_answer_facts(
                    message,
                    turn_origin=current_turn,
                    host_bindings=host_bindings,
                )
            )
            web_fact = _extract_web_fact(message, turn_origin=current_turn)
            if web_fact is not None:
                facts.append(web_fact)
        # other roles (system) are not part of canonical memory input

    bindings = _collect_episode_bindings(ok_turn_runs)

    return Episode(
        episode_id=f"ep_{start}_{end}",
        turn_range={"start": start, "end": end},
        structured_facts=facts,
        source_bindings=bindings,
        excluded_content_markers=list(_EXCLUDED_MARKERS),
        compaction_model="none",
        compaction_method="emergency_deterministic",
        compaction_timestamp=_now_iso_utc(),
        compaction_input_watermark="",
    )


def _count_user_messages(messages: list[dict[str, Any]]) -> int:
    return sum(
        1
        for m in messages
        if isinstance(m, dict) and str(m.get("role") or "").lower() == "user"
    )


def _split_aged_recent(
    canonical_messages: list[dict[str, Any]],
    *,
    recent_pairs: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split canonical messages into (aged, recent) by user-message count.

    Recent window = the last ``recent_pairs`` user messages and any
    assistant messages that follow each. Aged = everything before.
    Returns ``(aged_messages, recent_messages)`` preserving order.
    """
    total_users = _count_user_messages(canonical_messages)
    if total_users <= recent_pairs:
        return [], list(canonical_messages)

    # Walk from the end, counting user messages until we have
    # ``recent_pairs`` of them; the slice point is the start of recent.
    cut = len(canonical_messages)
    seen_users = 0
    for index in range(len(canonical_messages) - 1, -1, -1):
        msg = canonical_messages[index]
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").lower() == "user":
            seen_users += 1
            if seen_users == recent_pairs:
                cut = index
                break
    aged = canonical_messages[:cut]
    recent = canonical_messages[cut:]
    return aged, recent


def emergency_full_snapshot(
    canonical_messages: list[dict[str, Any]],
    ok_turn_runs: list[dict[str, Any]],
    *,
    recent_pairs: int = _RECENT_PAIRS_DEFAULT,
    thread_id: str = "",
    host_bindings: dict[str, SourceBinding] | None = None,
) -> ThreadMemorySnapshot:
    """Build a full ThreadMemorySnapshot from canonical thread state.

    Splits canonical messages into aged + recent (§4.2(e) step 3:
    window = ``recent_pairs×2`` + aged batch). Aged segment is
    emergency-compacted into one episode; recent segment is left
    verbatim (injected separately at assembly time, not stored here).

    The watermark covers ALL canonical messages (aged + recent), so CAS
    detects any concurrent append or regenerate.

    ``thread_id`` is required for the snapshot scope ( §6:
    thread-scoped, never cross-thread references).

     Watermark is computed via the shared
    :func:`allowlist.compute_watermark` — no duplicate implementation.
     ``host_bindings`` is forwarded to ``emergency_compact``
    so article-cited answer blocks produce ``source_type='article'`` facts.
    """
    if recent_pairs < 1:
        raise ValueError("recent_pairs must be >= 1")

    # Use the shared compute_watermark (no duplicate).
    watermark = compute_watermark(canonical_messages)
    aged_messages, _recent_messages = _split_aged_recent(
        canonical_messages, recent_pairs=recent_pairs
    )

    episodes: list[Episode] = []
    if aged_messages:
        aged_user_count = _count_user_messages(aged_messages)
        if aged_user_count > 0:
            turn_range = (1, aged_user_count)
            # Aged episode 只能吸收其 turn range 内对应的 ok
            # run/binding，不能把整条 thread 的 bindings 混入。过滤
            # ok_turn_runs 到 message_id ∈ aged message ids 的子集。
            aged_msg_ids = {
                str(m.get("id") or m.get("message_id") or "")
                for m in aged_messages
                if isinstance(m, dict)
            }
            aged_ok_runs = [
                run
                for run in (ok_turn_runs or [])
                if isinstance(run, dict)
                and str(run.get("message_id") or "") in aged_msg_ids
            ]
            episodes.append(
                emergency_compact(
                    aged_messages,
                    aged_ok_runs,
                    turn_range=turn_range,
                    host_bindings=host_bindings,
                )
            )

    return ThreadMemorySnapshot(
        version="thread_memory_v1",
        watermark=watermark,
        thread_id=thread_id,
        created_at=_now_iso_utc(),
        last_compacted_at=_now_iso_utc() if episodes else None,
        last_compaction_stats=None,
        episodes=episodes,
    )
