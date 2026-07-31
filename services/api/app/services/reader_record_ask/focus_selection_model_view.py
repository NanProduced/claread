"""ASK-UX-COT-COMPOSER-R3 P2 — focus selections model view.

Renders the user-pinned focus anchors beyond the primary selection
(``envelope.focus_anchors[1:]``) as XML-escaped untrusted article text
blocks for the production user prompt.

Design constraints:
- Emphasis, not restriction: a fixed server-authored framing text tells
  the model the focus selections highlight what the user cares about and
  never limit the answer scope — the full current article and authorized
  web evidence remain available.
- Snippets come ONLY from the server-validated canonical anchor text
  (gate-validated against the active base/unit/segment; the client
  ``selected_text`` is never trusted as a source of truth).
- Shares the existing ``selection`` budget account with the primary
  selection (no 10th account — the nine-account reserve sum invariant is
  untouched). Each snippet is prefix-fit to the remaining account budget
  under a 2000-char hard cap; exhaustion is fail-soft (a snippet that
  does not fit is dropped, never a turn abort).
- The whole section (framing + all blocks + separators) is charged as a
  single rendered view, so every model-visible character is metered
  exactly once and the account partition stays exact.
- Blocks are untrusted article data: XML-escaped, tagged, and excluded
  from the request_frame trusted surface (the runtime appends the
  section to the final user prompt; it charges ``selection``).
- No evidence registry semantics: focus text is user emphasis, not
  citeable server evidence; no handles are minted.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.reader_record_ask.context_envelope import EnvelopeInitialAnchor
from app.services.reader_record_ask.model_view_budget import (
    BudgetChargeOk,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
)

FOCUS_SELECTION_ROLE = "focus_selection"

# Same per-snippet hard cap as the primary selection evidence snippet.
FOCUS_SNIPPET_HARD_CAP = 2000

# Fixed server-authored framing. Never interpolates client text outside
# the escaped untrusted blocks; never implies the answer must stay within
# the selections. The leading newline is the separator from the trusted
# frame and is part of the charged section.
FOCUS_SECTION_HEADER = (
    "## User focus selections (untrusted article text)\n"
    "以下原文选段是用户特别关注的重点。选区仅表示强调关注，不意味着只能"
    "围绕选区回答；你仍可利用当前文章全文与已授权的网页证据。"
)
_FOCUS_SECTION_PREFIX = "\n" + FOCUS_SECTION_HEADER + "\n"


class FocusSelectionBudgetExhausted(Exception):
    """All visible focus selections must fit or the model call must not run.

    Composer chips are an explicit user promise.  Silently omitting one would
    make the browser state disagree with the model-visible context, so this is
    a typed Host failure rather than a best-effort degradation.
    """


def _render_focus_section(
    *,
    anchors: Sequence[EnvelopeInitialAnchor],
    prefix_char_limit: int,
    renderer: ModelViewRenderer,
) -> str:
    blocks: list[str] = []
    for index, anchor in enumerate(anchors):
        canonical = anchor.selected_text
        if not canonical:
            raise ValueError("focus selection canonical text must be non-empty")
        ordinal = index + 1
        handle_id = f"focus_selection:{ordinal}:{anchor.text_hash}"
        blocks.append(
            renderer.render_untrusted_article_text(
                handle_id=handle_id,
                ordinal=ordinal,
                role=FOCUS_SELECTION_ROLE,
                text=canonical[: min(len(canonical), prefix_char_limit)],
            ).text
        )
    return _FOCUS_SECTION_PREFIX + "\n".join(blocks)


def assemble_focus_selections_section(
    *,
    focus_anchors: Sequence[EnvelopeInitialAnchor],
    budget: ModelVisibleTurnBudget,
    renderer: ModelViewRenderer,
) -> tuple[str, int]:
    """Render + charge the focus selections section.

    Returns ``(section_text, charged_chars)``; the section starts with
    the framing header and contains one ``<untrusted_article_text
    role="focus_selection" ordinal=N>`` block per fitting anchor.
    Empty string and zero cost only when no anchors are provided.
    For one or more anchors, every anchor receives a non-empty, XML-escaped
    block.  The Host uses a common prefix cap so an earlier long selection
    cannot starve a later selection.  If even one character per anchor cannot
    fit, :class:`FocusSelectionBudgetExhausted` is raised before provider I/O;
    visible Composer context is never silently discarded.
    ``focus_anchors`` here is the EXTRA set beyond the primary selection
    (the caller slices ``envelope.focus_anchors[1:]``).
    """
    if not focus_anchors:
        return "", 0

    minimum_section = _render_focus_section(
        anchors=focus_anchors,
        prefix_char_limit=1,
        renderer=renderer,
    )
    if not budget.can_charge("selection", renderer.render_plain(minimum_section)):
        raise FocusSelectionBudgetExhausted(
            "focus_selection_budget_exhausted"
        )

    lo = 1
    hi = FOCUS_SNIPPET_HARD_CAP
    section = minimum_section
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _render_focus_section(
            anchors=focus_anchors,
            prefix_char_limit=mid,
            renderer=renderer,
        )
        if budget.can_charge("selection", renderer.render_plain(candidate)):
            section = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    result = budget.try_charge("selection", renderer.render_plain(section))
    if not isinstance(result, BudgetChargeOk):
        raise FocusSelectionBudgetExhausted(
            "focus_selection_budget_exhausted"
        )
    return section, int(result.cost)
