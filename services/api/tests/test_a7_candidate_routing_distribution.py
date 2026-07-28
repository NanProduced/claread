"""A7 — candidate routing distribution statistics (评审 R3/P0-4).

Runs the deterministic ``InputSuitabilityGate`` against:

1. All 11 G0 fixtures under ``tests/fixtures/markdown_structured_source/`` —
   these are the parser-contract fixtures; the gate sees the same text the
   parser sees, so every fixture that triggers ``raw_html_block`` /
   ``inline_html`` / ``footnote_reference`` / ``has_unclosed_fence`` parser
   warnings MUST surface as ``markdown_complex_structure`` gate flags.
2. Real-style samples (Notion / Feishu export style markdown) defined
   inline — these carry enough English natural-language content to clear
   the gate's ``_MIN_ENGLISH_WORDS`` threshold so the report reflects
   real routing outcomes (``stable_document_ready`` vs
   ``candidate_document_required``) rather than the short-text rejection
   that the minimal fixtures hit.

The aggregate report is a pure in-memory data structure; no LLM, no DB,
no network. The report's purpose (per plan §A7) is to quantify the
candidate rate attributable to raw HTML / footnote / unclosed fence /
table / math so future copy relaxation and policy tweaks have a
measurable baseline.

Tests assert:
- Report structure (totals, per-outcome, per-flag, per-sample).
- Every fixture is represented.
- Raw HTML / footnote / unclosed fence fixtures surface the
  ``markdown_complex_structure`` flag (the gate's signal for "complex
  markdown that requires candidate review").
- Real-style samples route as expected (clean → stable; table/math/html
  → candidate).
- The human-readable report text includes the candidate rate and the
  flag distribution.
- ``markdown_complex_structure`` flag correlates with non-stable
  outcomes across the full sample set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.schemas.reader_input_adapter import (
    InputAdapterSourceType,
    InputSuitabilityOutcome,
    InputSuitabilityRequest,
    InputSuitabilityResult,
    SourceLossFlag,
)
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityGate,
)

_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "markdown_structured_source"

# All 11 G0 fixtures (mirrors test_markdown_source_parser.py inventory).
_ALL_FIXTURES: tuple[str, ...] = (
    "code_mermaid",
    "footnote",
    "gfm_table",
    "nested_list",
    "r14_complex",
    "raw_html",
    "real_list_wrapper",
    "reject_empty",
    "simple_paragraph",
    "unclosed_fence",
    "unsafe_link",
)

# Fixtures whose parser contract declares a ``candidate_document_required``
# outcome due to content-check markdown complexity (footnote / unclosed
# fence). The gate MUST surface ``markdown_complex_structure`` for each of
# these. L1: raw HTML and unsafe links are deterministic adaptations
# (``adaptation_notice``) and no longer surface the flag.
_FIXTURES_WITH_MARKDOWN_COMPLEXITY: frozenset[str] = frozenset(
    {
        "footnote",
        "unclosed_fence",
    }
)


# ---------------------------------------------------------------------------
# Real-style samples (Notion / Feishu export style)
# ---------------------------------------------------------------------------

_NOTION_CLEAN = """\
# Project Notes

> This callout summarizes the key decisions from our last planning meeting.

- [ ] Draft the project proposal and circulate it to the committee
- [ ] Review the budget allocation for the upcoming fiscal quarter
- [x] Schedule the kickoff meeting with all stakeholders

The team agreed that the next phase should focus on user research and
prototype iteration. We will circulate a detailed timeline by end of week
and collect feedback from all stakeholders before proceeding with the
implementation plan. The committee emphasized transparency and asked for
weekly progress updates throughout the execution phase.
"""

_NOTION_WITH_TABLE = """\
# Team Directory

| Name | Role | Team |
|------|------|------|
| Alice | Engineer | Platform |
| Bob | Designer | Product |
| Carol | Manager | Operations |

The directory above lists all active team members along with their current
roles. Each row will be updated quarterly to reflect any organizational
changes or new hires that occur during the planning cycle. The team
reviewed the directory during the last sync and confirmed the information
is accurate as of this morning.
"""

_NOTION_WITH_HTML = """\
# Weekly Digest

<div class="callout">
This week focused on finalizing the quarterly report and aligning with
the design team on the new dashboard layout.
</div>

The team completed all planned tasks ahead of schedule. We expect to ship
the updated interface by the end of next sprint after completing the
final round of usability testing with selected participants from the
community engagement program.
"""

_FEISHU_WITH_MATH = """\
# Research Notes

The expected value is computed as $$E[X] = \\sum_{i=1}^{n} x_i \\cdot p_i$$
where each $x_i$ represents the outcome and $p_i$ is the probability.

This formula applies to discrete distributions. For continuous cases, we
replace the sum with an integral over the probability density function.
The team should verify the assumptions before applying this model to the
production forecasting pipeline.
"""

_FEISHU_CLEAN = """\
# Meeting Minutes

The committee reviewed three proposals for the upcoming community
engagement initiative. After extensive discussion, the members agreed to
prioritize transparency and allocate resources to the most impactful
projects.

## Action Items

- Finalize the budget by next Tuesday and notify all department leads
- Send out the stakeholder survey to collect feedback on the proposal
- Schedule a follow-up review session with the executive team

The next meeting will focus on implementation details and resource
allocation across the selected initiatives.
"""


@dataclass(frozen=True, slots=True)
class _RealStyleSample:
    name: str
    source_type: InputAdapterSourceType
    text: str


_REAL_STYLE_SAMPLES: tuple[_RealStyleSample, ...] = (
    _RealStyleSample(
        name="notion_export_clean",
        source_type="pasted_text",
        text=_NOTION_CLEAN,
    ),
    _RealStyleSample(
        name="notion_export_database_table",
        source_type="pasted_text",
        text=_NOTION_WITH_TABLE,
    ),
    _RealStyleSample(
        name="notion_export_with_raw_html",
        source_type="pasted_text",
        text=_NOTION_WITH_HTML,
    ),
    _RealStyleSample(
        name="feishu_export_with_math",
        source_type="pasted_text",
        text=_FEISHU_WITH_MATH,
    ),
    _RealStyleSample(
        name="feishu_export_clean",
        source_type="pasted_text",
        text=_FEISHU_CLEAN,
    ),
)


# ---------------------------------------------------------------------------
# Report data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RoutingSample:
    """Per-sample routing result."""

    name: str
    source_type: InputAdapterSourceType
    outcome: InputSuitabilityOutcome
    flags: tuple[SourceLossFlag, ...]
    word_count: int
    english_word_ratio: float
    has_markdown_complexity: bool
    is_fixture: bool


@dataclass(frozen=True, slots=True)
class _RoutingReport:
    """Aggregate routing distribution report."""

    total_samples: int
    by_outcome: dict[str, int]
    by_flag: dict[str, int]
    by_source_type: dict[str, dict[str, int]]
    samples: tuple[_RoutingSample, ...]
    candidate_rate: float
    rejected_rate: float
    stable_rate: float
    markdown_complexity_rate: float
    candidate_rate_among_complex: float


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------


def _load_fixture_text(name: str) -> str:
    fixture_dir = _FIXTURES_ROOT / name
    return (fixture_dir / "input.md").read_text(encoding="utf-8")


def _evaluate(
    *,
    source_type: InputAdapterSourceType,
    text: str,
) -> InputSuitabilityResult:
    gate = InputSuitabilityGate()
    request = InputSuitabilityRequest(
        source_type=source_type,
        text=text,
        source_metadata={},
    )
    return gate.evaluate(request)


def _has_markdown_complexity(flags: tuple[SourceLossFlag, ...]) -> bool:
    return "markdown_complex_structure" in flags


def _build_sample(
    *,
    name: str,
    source_type: InputAdapterSourceType,
    text: str,
    is_fixture: bool,
) -> _RoutingSample:
    result = _evaluate(source_type=source_type, text=text)
    return _RoutingSample(
        name=name,
        source_type=source_type,
        outcome=result.outcome,
        flags=tuple(result.flags),
        word_count=result.word_count,
        english_word_ratio=result.english_word_ratio,
        has_markdown_complexity=_has_markdown_complexity(tuple(result.flags)),
        is_fixture=is_fixture,
    )


def _build_routing_report(
    samples: tuple[_RoutingSample, ...],
) -> _RoutingReport:
    total = len(samples)
    by_outcome: dict[str, int] = {}
    by_flag: dict[str, int] = {}
    by_source_type: dict[str, dict[str, int]] = {}
    complex_count = 0
    complex_and_candidate_count = 0

    for sample in samples:
        by_outcome[sample.outcome] = by_outcome.get(sample.outcome, 0) + 1

        st = sample.source_type
        if st not in by_source_type:
            by_source_type[st] = {}
        by_source_type[st][sample.outcome] = by_source_type[st].get(sample.outcome, 0) + 1

        for flag in sample.flags:
            by_flag[flag] = by_flag.get(flag, 0) + 1

        if sample.has_markdown_complexity:
            complex_count += 1
            if sample.outcome == "candidate_document_required":
                complex_and_candidate_count += 1

    stable = by_outcome.get("stable_document_ready", 0)
    candidate = by_outcome.get("candidate_document_required", 0)
    rejected = by_outcome.get("input_rejected_or_action_required", 0)

    return _RoutingReport(
        total_samples=total,
        by_outcome=by_outcome,
        by_flag=by_flag,
        by_source_type=by_source_type,
        samples=samples,
        candidate_rate=candidate / total if total else 0.0,
        rejected_rate=rejected / total if total else 0.0,
        stable_rate=stable / total if total else 0.0,
        markdown_complexity_rate=complex_count / total if total else 0.0,
        candidate_rate_among_complex=(
            complex_and_candidate_count / complex_count if complex_count else 0.0
        ),
    )


def _format_report_text(report: _RoutingReport) -> str:
    lines: list[str] = []
    lines.append("=== A7 Candidate Routing Distribution Report ===")
    lines.append("")
    lines.append(f"Total samples: {report.total_samples}")
    lines.append(
        f"Outcome distribution: stable={report.stable_rate:.1%} "
        f"candidate={report.candidate_rate:.1%} "
        f"rejected={report.rejected_rate:.1%}"
    )
    lines.append(f"Markdown complexity rate: {report.markdown_complexity_rate:.1%}")
    lines.append(f"Candidate rate among complex: {report.candidate_rate_among_complex:.1%}")
    lines.append("")
    lines.append("By outcome:")
    for outcome, count in sorted(report.by_outcome.items()):
        lines.append(f"  {outcome}: {count}")
    lines.append("")
    lines.append("By flag:")
    for flag, count in sorted(report.by_flag.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {flag}: {count}")
    lines.append("")
    lines.append("By source type:")
    for st, outcomes in sorted(report.by_source_type.items()):
        parts = ", ".join(f"{o}={c}" for o, c in sorted(outcomes.items()))
        lines.append(f"  {st}: {parts}")
    lines.append("")
    lines.append("Per-sample:")
    for sample in report.samples:
        kind = "fixture" if sample.is_fixture else "real-style"
        flags_str = ",".join(sample.flags) if sample.flags else "-"
        lines.append(
            f"  [{kind}] {sample.name}: {sample.outcome} "
            f"(words={sample.word_count}, flags={flags_str})"
        )
    return "\n".join(lines)


def _build_full_report() -> _RoutingReport:
    """Build the report across all fixtures + real-style samples."""
    samples: list[_RoutingSample] = []
    for fixture_name in _ALL_FIXTURES:
        text = _load_fixture_text(fixture_name)
        samples.append(
            _build_sample(
                name=fixture_name,
                source_type="pasted_text",
                text=text,
                is_fixture=True,
            )
        )
    for real_sample in _REAL_STYLE_SAMPLES:
        samples.append(
            _build_sample(
                name=real_sample.name,
                source_type=real_sample.source_type,
                text=real_sample.text,
                is_fixture=False,
            )
        )
    return _build_routing_report(tuple(samples))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_a7_report_contains_all_fixtures_and_real_style_samples() -> None:
    """Report MUST cover all 11 fixtures + 5 real-style samples."""
    report = _build_full_report()
    assert report.total_samples == len(_ALL_FIXTURES) + len(_REAL_STYLE_SAMPLES)
    fixture_names = {s.name for s in report.samples if s.is_fixture}
    real_names = {s.name for s in report.samples if not s.is_fixture}
    assert fixture_names == set(_ALL_FIXTURES)
    assert real_names == {s.name for s in _REAL_STYLE_SAMPLES}


def test_a7_report_has_expected_structure() -> None:
    """Report MUST expose totals, per-outcome, per-flag, per-source-type."""
    report = _build_full_report()
    assert report.total_samples > 0
    assert sum(report.by_outcome.values()) == report.total_samples
    assert 0.0 <= report.candidate_rate <= 1.0
    assert 0.0 <= report.rejected_rate <= 1.0
    assert 0.0 <= report.stable_rate <= 1.0
    assert report.candidate_rate + report.rejected_rate + report.stable_rate == pytest.approx(1.0)
    assert 0.0 <= report.markdown_complexity_rate <= 1.0
    assert 0.0 <= report.candidate_rate_among_complex <= 1.0
    assert len(report.samples) == report.total_samples


def test_a7_fixture_raw_html_no_longer_triggers_markdown_complexity() -> None:
    """L1: ``raw_html`` fixture is a deterministic adaptation — the gate
    MUST NOT surface ``markdown_complex_structure`` anymore."""
    report = _build_full_report()
    raw_html_sample = next(s for s in report.samples if s.name == "raw_html")
    assert raw_html_sample.has_markdown_complexity is False
    assert "markdown_complex_structure" not in raw_html_sample.flags


def test_a7_fixture_footnote_triggers_markdown_complexity() -> None:
    """``footnote`` fixture MUST surface ``markdown_complex_structure``."""
    report = _build_full_report()
    footnote_sample = next(s for s in report.samples if s.name == "footnote")
    assert footnote_sample.has_markdown_complexity is True
    assert "markdown_complex_structure" in footnote_sample.flags


def test_a7_fixture_unclosed_fence_triggers_markdown_complexity() -> None:
    """``unclosed_fence`` fixture MUST surface ``markdown_complex_structure``."""
    report = _build_full_report()
    unclosed_sample = next(s for s in report.samples if s.name == "unclosed_fence")
    assert unclosed_sample.has_markdown_complexity is True
    assert "markdown_complex_structure" in unclosed_sample.flags


def test_a7_fixture_unsafe_link_no_longer_triggers_markdown_complexity() -> None:
    """L1: ``unsafe_link`` fixture is a deterministic adaptation — the gate
    MUST NOT surface ``markdown_complex_structure`` anymore."""
    report = _build_full_report()
    unsafe_sample = next(s for s in report.samples if s.name == "unsafe_link")
    assert unsafe_sample.has_markdown_complexity is False
    assert "markdown_complex_structure" not in unsafe_sample.flags


def test_a7_all_complexity_fixtures_surface_markdown_complex_structure() -> None:
    """Every fixture in ``_FIXTURES_WITH_MARKDOWN_COMPLEXITY`` MUST surface
    the ``markdown_complex_structure`` flag — this is the gate's signal that
    raw HTML / footnote / unclosed fence / unsafe link content was detected."""
    report = _build_full_report()
    for fixture_name in _FIXTURES_WITH_MARKDOWN_COMPLEXITY:
        sample = next(s for s in report.samples if s.name == fixture_name)
        assert sample.has_markdown_complexity is True, (
            f"fixture {fixture_name!r} must surface "
            f"markdown_complex_structure flag; got flags={sample.flags}"
        )


def test_a7_real_style_notion_clean_routes_to_stable() -> None:
    """Notion-style export without complex markdown → stable_document_ready.

    The sample carries enough English natural-language content (≥50 words)
    and has no raw HTML / footnote / unclosed fence / table / math, so the
    gate MUST route it to ``stable_document_ready``.
    """
    report = _build_full_report()
    sample = next(s for s in report.samples if s.name == "notion_export_clean")
    assert sample.outcome == "stable_document_ready", (
        f"expected stable_document_ready; got {sample.outcome} (flags={sample.flags})"
    )
    assert sample.has_markdown_complexity is False


def test_a7_real_style_feishu_clean_routes_to_stable() -> None:
    """Feishu-style export without complex markdown → stable_document_ready."""
    report = _build_full_report()
    sample = next(s for s in report.samples if s.name == "feishu_export_clean")
    assert sample.outcome == "stable_document_ready", (
        f"expected stable_document_ready; got {sample.outcome} (flags={sample.flags})"
    )
    assert sample.has_markdown_complexity is False


def test_a7_real_style_notion_with_table_routes_to_stable() -> None:
    """L1: Notion-style export with a deterministic GFM table → stable.

    A table with a complete header separator row and consistent raw cell
    counts no longer triggers ``table_structure_uncertain``; it freezes as
    a stable document with first-class table blocks.
    """
    report = _build_full_report()
    sample = next(s for s in report.samples if s.name == "notion_export_database_table")
    assert sample.outcome == "stable_document_ready", (
        f"expected stable_document_ready; got {sample.outcome} (flags={sample.flags})"
    )
    assert "table_structure_uncertain" not in sample.flags


def test_a7_real_style_feishu_with_math_routes_to_candidate() -> None:
    """Feishu-style export with math syntax → candidate_document_required.

    Math triggers ``markdown_complex_structure`` + ``document_block_degraded``
    flags, which route to candidate review.
    """
    report = _build_full_report()
    sample = next(s for s in report.samples if s.name == "feishu_export_with_math")
    assert sample.outcome == "candidate_document_required", (
        f"expected candidate_document_required; got {sample.outcome} (flags={sample.flags})"
    )
    assert sample.has_markdown_complexity is True


def test_a7_real_style_notion_with_html_routes_to_stable() -> None:
    """L1: Notion-style export with cleaned raw HTML → stable.

    Raw HTML is stripped to text with an ``adaptation_notice``; it no
    longer routes to candidate review by itself.
    """
    report = _build_full_report()
    sample = next(s for s in report.samples if s.name == "notion_export_with_raw_html")
    assert sample.outcome == "stable_document_ready", (
        f"expected stable_document_ready; got {sample.outcome} (flags={sample.flags})"
    )
    assert sample.has_markdown_complexity is False


def test_a7_markdown_complexity_correlates_with_non_stable_outcome() -> None:
    """Every sample with ``markdown_complex_structure`` MUST NOT route to
    ``stable_document_ready`` — complex markdown always forces candidate
    review or rejection, never direct stable freeze."""
    report = _build_full_report()
    for sample in report.samples:
        if sample.has_markdown_complexity:
            assert sample.outcome != "stable_document_ready", (
                f"sample {sample.name!r} has markdown_complex_structure "
                f"but routed to stable_document_ready; this breaks the "
                f"gate's fail-closed contract for complex markdown"
            )


def test_a7_report_text_includes_candidate_rate_summary() -> None:
    """The human-readable report MUST include the candidate rate line."""
    report = _build_full_report()
    text = _format_report_text(report)
    assert "=== A7 Candidate Routing Distribution Report ===" in text
    assert "Outcome distribution:" in text
    assert "candidate=" in text
    assert "Markdown complexity rate:" in text
    assert "Candidate rate among complex:" in text


def test_a7_report_text_includes_flag_distribution() -> None:
    """The report text MUST include the flag distribution section so
    reviewers can see which flags drive the candidate rate."""
    report = _build_full_report()
    text = _format_report_text(report)
    assert "By flag:" in text
    assert "markdown_complex_structure:" in text


def test_a7_report_text_includes_per_sample_lines() -> None:
    """The report text MUST include per-sample lines so reviewers can
    trace which fixture/sample produced which outcome."""
    report = _build_full_report()
    text = _format_report_text(report)
    assert "Per-sample:" in text
    assert "[fixture]" in text
    assert "[real-style]" in text
    # Spot-check: raw_html fixture must appear.
    assert "raw_html:" in text


def test_a7_report_flag_distribution_counts_are_consistent() -> None:
    """``by_flag`` counts MUST sum to the total flag occurrences across
    all samples (a sample can contribute multiple flags)."""
    report = _build_full_report()
    total_flags_from_samples = sum(len(s.flags) for s in report.samples)
    total_flags_from_report = sum(report.by_flag.values())
    assert total_flags_from_report == total_flags_from_samples


def test_a7_report_candidate_rate_among_complex_is_consistent() -> None:
    """``candidate_rate_among_complex`` MUST equal
    (complex AND candidate) / complex."""
    report = _build_full_report()
    complex_samples = [s for s in report.samples if s.has_markdown_complexity]
    if not complex_samples:
        pytest.skip("no complex samples in report")
    complex_and_candidate = sum(
        1 for s in complex_samples if s.outcome == "candidate_document_required"
    )
    expected = complex_and_candidate / len(complex_samples)
    assert report.candidate_rate_among_complex == pytest.approx(expected)


def test_a7_real_style_samples_have_enough_english_to_clear_short_gate() -> None:
    """Real-style samples MUST carry ≥50 English words so the gate's
    ``too_short_for_learning`` rejection does not mask the actual
    routing signal. This is the prerequisite for the report to be a
    meaningful candidate-rate baseline (per plan §A7)."""
    report = _build_full_report()
    for real_sample in _REAL_STYLE_SAMPLES:
        sample = next(s for s in report.samples if s.name == real_sample.name)
        # _MIN_ENGLISH_WORDS in the gate is 50; we assert ≥50 so the
        # short-text rejection never fires on real-style samples.
        # word_count is the total token count (English + CJK + digits),
        # but english_word_ratio * word_count gives the English count.
        english_words = round(sample.english_word_ratio * sample.word_count)
        assert english_words >= 50, (
            f"real-style sample {sample.name!r} has only {english_words} "
            f"English words (word_count={sample.word_count}, "
            f"ratio={sample.english_word_ratio}); the short-text gate "
            f"will mask the actual routing signal"
        )
