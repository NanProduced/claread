"""Render thread memory snapshot into a model-visible data block.

Implements §6 注入形态约束 + §8.3 安全门第 7 条:

- Memory is injected as a user-role data block wrapped in an XML fence
  ``<transcript_data role="data" not_instructions="true">…</transcript_data>``.
- Memory is NEVER a system message and NEVER parsed as instructions by
  downstream logic.
- Article facts whose bindings are fence-invalid are rendered as
  "此前讨论过（来源已变化）" WITHOUT citation_ids.
- Web facts are rendered with the "线索" prefix to mark them as prior
  context / search hints (frozen decision #6).
- ``user_correction`` facts are annotated ``[已纠正]``.
- Budget shrinking follows ``confidence × recency``:
  ``prior_context`` is evicted first, ``high`` last. ``protected``
  facts (user corrections / unresolved questions) are never evicted.

Output is a renderer-minted :class:`RenderedModelView` produced via
``ModelViewRenderer.render_plain`` — the only path that brands a
chargeable view (model_view_budget.py:143-149).
"""

from __future__ import annotations

from xml.sax.saxutils import escape as _xml_escape

from app.services.reader_record_ask.model_view_budget import (
    ModelViewRenderer,
    RenderedModelView,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
)

# Confidence eviction order: lowest first, highest last ( §6
# StructuredFact.confidence). ``prior_context`` is web history (always
# evicted first); ``high`` is article+valid-binding or user correction.
_CONFIDENCE_RANK: dict[str, int] = {
    "prior_context": 0,
    "medium": 1,
    "high": 2,
}

_XML_OPEN = '<transcript_data role="data" not_instructions="true">'
_XML_CLOSE = "</transcript_data>"


def _binding_index(
    bindings: list[SourceBinding],
) -> tuple[dict[str, SourceBinding], set[str]]:
    by_id: dict[str, SourceBinding] = {b.binding_id: b for b in bindings}
    invalid_ids = {
        b.binding_id
        for b in bindings
        if isinstance(b.validity_check, dict)
        and b.validity_check.get("status") == "invalid"
    }
    return by_id, invalid_ids


def _fact_bound_to_invalid_binding(
    fact: StructuredFact,
    bindings_by_id: dict[str, SourceBinding],
    invalid_ids: set[str],
) -> bool:
    """True if this fact is an article fact whose referenced binding is invalid."""
    if fact.source_type != "article":
        return False
    if not fact.source_ids:
        return False
    for sid in fact.source_ids:
        if sid in invalid_ids:
            return True
        binding = bindings_by_id.get(sid)
        if binding is not None and isinstance(binding.validity_check, dict):
            if binding.validity_check.get("status") == "invalid":
                return True
    return False


def _render_fact_line(
    fact: StructuredFact,
    *,
    bindings_by_id: dict[str, SourceBinding],
    invalid_ids: set[str],
) -> str:
    """Render one fact as a single bullet line per §6 注入形态约束.

     All untrusted dynamic fields (``fact.text``) are XML-escaped
    before insertion so ``</transcript_data>``, prompt injection, evh /
    Bearer / sk- fragments cannot break the XML fence boundary.
    """
    turn = fact.turn_origin
    if fact.source_type == "web":
        # Web fact: always "线索：…" (frozen #6: prior context / search hint).
        return f"- [web] 线索：{_xml_escape(fact.text)} (turn {turn})"

    if _fact_bound_to_invalid_binding(fact, bindings_by_id, invalid_ids):
        # Article fact whose binding fence-invalidated: render as prior
        # mention, NO citation_ids. The text itself is dropped to avoid
        # leaking stale article claims.
        return f"- [prior_mention] 此前讨论过（来源已变化）(turn {turn})"

    if fact.source_type == "user_correction":
        return (
            f"- [user_correction] [已纠正] {_xml_escape(fact.text)} "
            f"(turn {turn})"
        )

    return f"- [{fact.source_type}] {_xml_escape(fact.text)} (turn {turn})"


def _episode_header(episode: Episode) -> str:
    """Return the escaped, line-atomic header for one episode."""
    # TurnRange is a Pydantic model (TurnRange), not a dict.
    # The old ``isinstance(episode.turn_range, dict)`` check was always
    # False, so the turn range display was silently dropped.
    turn_start = episode.turn_range.start
    turn_end = episode.turn_range.end
    # Escape episode_id (untrusted dynamic field).
    header = f"### episode {_xml_escape(episode.episode_id)}"
    if turn_start is not None and turn_end is not None:
        header += f" (turns {turn_start}-{turn_end})"
    return header


def _fact_priority(fact: StructuredFact) -> tuple[int, int, int]:
    """Global keep priority: protected, confidence, then recency."""
    return (
        1 if fact.protected else 0,
        _CONFIDENCE_RANK.get(fact.confidence, 1),
        fact.turn_origin,
    )


def render_memory_block(
    snapshot: ThreadMemorySnapshot | None,
    *,
    budget_chars: int,
) -> RenderedModelView | None:
    """Render a snapshot into a fenced data block.

    Returns ``None`` when:
    - ``snapshot`` is ``None``
    - ``budget_chars`` ≤ 0
    - the snapshot has no episodes or all episodes are empty after
      budget shrinking (rendering an empty fence would mislead the
      model into thinking memory exists)

    Otherwise returns a renderer-minted :class:`RenderedModelView`
    whose ``char_cost`` equals ``len(text)`` (the only path that
    produces a chargeable view).

     Budget boxing is **line-atomic**. A fact line is either
    kept in full or dropped entirely — never truncated mid-line. This
    forbids ``joined[:inner_budget]`` and ``inner[:max_inner]`` which
    could split a user correction, source type, turn marker, or XML
    entity. The output always satisfies:
      - ``len(text) <= budget_chars``
      - exactly one opening and one closing fence
      - every output fact is a complete line
      - no half XML entity / tag
    """
    if snapshot is None or budget_chars <= 0:
        return None
    if not snapshot.episodes:
        return None

    # Account for the XML fence wrapper (open + newline + close).
    wrapper_overhead = len(_XML_OPEN) + 1 + len(_XML_CLOSE)
    if wrapper_overhead >= budget_chars:
        # Budget too small even for the fences alone → return None
        # (rendering an empty/malformed fence would mislead the model).
        return None
    inner_budget = budget_chars - wrapper_overhead

    # Build one global candidate set before spending any budget.  Selection
    # priority must not reset at episode boundaries: a newer protected user
    # correction in a later episode outranks an old high-confidence answer.
    candidates: list[tuple[tuple[int, int, int], int, int, str]] = []
    headers: dict[int, str] = {}
    for episode_index, episode in enumerate(snapshot.episodes):
        headers[episode_index] = _episode_header(episode)
        bindings_by_id, invalid_ids = _binding_index(episode.source_bindings)
        for fact_index, fact in enumerate(episode.structured_facts):
            candidates.append(
                (
                    _fact_priority(fact),
                    episode_index,
                    fact_index,
                    _render_fact_line(
                        fact,
                        bindings_by_id=bindings_by_id,
                        invalid_ids=invalid_ids,
                    ),
                )
            )
    candidates.sort(
        key=lambda candidate: (
            candidate[0],
            candidate[1],
            candidate[2],
        ),
        reverse=True,
    )

    selected_by_episode: dict[int, list[tuple[tuple[int, int, int], int, str]]] = {}
    remaining = inner_budget
    for priority, episode_index, fact_index, line in candidates:
        first_for_episode = episode_index not in selected_by_episode
        cost = len(line) + 1
        if first_for_episode:
            cost += len(headers[episode_index]) + 1
        if cost > remaining:
            continue
        selected_by_episode.setdefault(episode_index, []).append(
            (priority, fact_index, line)
        )
        remaining -= cost

    # Append order remains the presentation order, but only episodes with at
    # least one selected fact receive a header.  This avoids orphan headers
    # consuming budget or implying remembered content where none survived.
    kept_lines: list[str] = []
    for episode_index in range(len(snapshot.episodes)):
        selected = selected_by_episode.get(episode_index)
        if not selected:
            continue
        kept_lines.append(headers[episode_index])
        selected.sort(
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        kept_lines.extend(item[2] for item in selected)

    if not kept_lines:
        return None

    inner = "\n".join(kept_lines)
    text = f"{_XML_OPEN}\n{inner}\n{_XML_CLOSE}"

    # The line-atomic loop above guarantees every kept line
    # fits in inner_budget, so len(text) <= budget_chars is an invariant.
    # No further truncation is performed — the old ``inner[:max_inner]``
    # fallback that could split a line mid-way is removed. The assert is
    # a defensive safety net; if it fires it indicates a bug in the
    # boxing loop (e.g. a line whose cost was miscalculated).
    assert len(text) <= budget_chars, (
        f"render_memory_block budget invariant violated: "
        f"{len(text)} > {budget_chars} (kept_lines={len(kept_lines)})"
    )

    renderer = ModelViewRenderer()
    return renderer.render_plain(text)


def render_compaction_notice(
    *,
    method: str,
    duration_ms: int,
) -> str:
    """Render the user-visible compaction notice (§7.3).

    ``method`` ∈ {'model', 'emergency_deterministic', 'hybrid',
    'window_shrink'} selects the phrasing:

    - ``model`` / ``hybrid`` → "对话记忆已整理"
    - ``emergency_deterministic`` / ``window_shrink`` → "整理遇到问题，已使用备用方案"

    The notice NEVER contains token counts, percentages, or a context
    meter (frozen decision #7 + RL3). ``duration_ms`` is accepted
    for telemetry symmetry but is NOT surfaced to the user.
    """
    del duration_ms  # not surfaced (frozen #7)
    if method in ("model", "hybrid"):
        return (
            "对话记忆已整理\nConversation memory organized"
        )
    # emergency_deterministic, window_shrink, or unknown fallback
    return (
        "整理遇到问题，已使用备用方案\nUsing backup method"
    )
