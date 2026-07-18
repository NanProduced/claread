"""Tests for context_support evaluator (P0-6 atomic fact contract).

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: context_support atomic fact contract（P0-6）.

Covers:
- Required fact mentioned + grounded → PASS.
- Required fact mentioned but not grounded → FAIL (high severity).
- Required fact not mentioned → FAIL (high severity).
- Synonymous paraphrase of a required fact → PASS (alias group).
- Fact in late article body (public snippet truncated) → still PASSES
  because grounding uses ``source_aliases``, not the 500-char snippet.
- Non-required fact absent → PASS (informational only).
- Metadata-only fact (no answer aliases, no source aliases) → PASS.
- Capability boundary: case with no ``atomic_facts`` → coverage_incomplete
  signal in details (still PASS, soft signal).
- Multiple alias groups (AND) and aliases within a group (OR).
- Legacy ``required_article_facts`` migration through the loader.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawEvidenceObservation,
)
from claread_eval.reader_record_ask.evaluators.context_support import (
    evaluate_context_support,
)
from claread_eval.reader_record_ask.loader import (
    _migrate_legacy_required_article_facts,
)
from claread_eval.reader_record_ask.schema import (
    AtomicExpectedFact,
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case_with_atomic_facts(
    facts: list[AtomicExpectedFact],
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id="t-context-support",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category="city_enumeration",
        expected=ReaderRecordAskR4A3Expected(atomic_facts=facts),
    )


def _make_case_with_legacy_facts(facts: list[str]) -> ReaderRecordAskR4A3Case:
    """Build a case using the deprecated ``required_article_facts`` field."""
    return ReaderRecordAskR4A3Case(
        id="t-context-support-legacy",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category="city_enumeration",
        expected=ReaderRecordAskR4A3Expected(required_article_facts=facts),
    )


def _make_artifact(
    *,
    final_text: str,
    resolved_snippets: list[str],
) -> RawArtifact:
    return RawArtifact(
        case_id="t-context-support",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
        resolved_evidence=[
            RawEvidenceObservation(
                handle_id=f"evh_{i:032x}",
                kind="article_seed",
                snippet=snippet,
                provenance="baseline_context",
            )
            for i, snippet in enumerate(resolved_snippets)
        ],
    )


# ---------------------------------------------------------------------------
# Basic positive / negative cases (new atomic_facts contract)
# ---------------------------------------------------------------------------


def test_positive_fact_mentioned_and_grounded() -> None:
    """Required fact mentioned in answer AND grounded in evidence → PASS."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto"]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到的城市包括 Thunder Bay 和 Toronto。",
        resolved_snippets=["文中列出 Thunder Bay", "以及 Toronto"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True
    assert result.severity == "none"
    assert "all required atomic facts mentioned and grounded" in result.details


def test_negative_fact_mentioned_but_not_grounded() -> None:
    """Required fact in answer but no evidence supports → FAIL (high)."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到的城市包括 Thunder Bay。",
        resolved_snippets=[],  # no supporting evidence
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "city-thunder-bay" in result.details
    assert "not grounded" in result.details


def test_negative_required_fact_not_mentioned() -> None:
    """Required fact not mentioned in answer → FAIL (high)."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了多伦多。",  # no Thunder Bay mentioned
        resolved_snippets=["Thunder Bay 出现在文中"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "not mentioned" in result.details
    assert "city-thunder-bay" in result.details


def test_case_insensitive_match() -> None:
    """Aliases match case-insensitively."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["thunder bay"]],
            source_aliases=["thunder bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="The cities include THUNDER BAY.",
        resolved_snippets=["thunder bay listed"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# P0-6 regression: synonymous paraphrase PASSES
# ---------------------------------------------------------------------------


def test_synonymous_paraphrase_passes() -> None:
    """Spec: "正确同义改写 PASS".

    The previous implementation rejected synonymous paraphrases because
    it required the exact hand-written sentence. The new contract
    accepts any alias in the alias group.

    Case: article discusses "Buffalo received 36 inches of snow".
    The author aliases this fact with multiple synonymous phrases.
    The model's answer uses a paraphrase that matches one alias.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="snowfall-amount",
            answer_alias_groups=[[
                "36 inches of snow",
                "降雪量达到36英寸",
                "36英寸的雪",
                "snowfall reached 36 inches",
            ]],
            source_aliases=["36 inches"],
            required=True,
            severity="high",
        ),
    ])
    # The model paraphrases using one of the allowed aliases.
    artifact = _make_artifact(
        final_text="文章指出降雪量达到36英寸，受影响最严重。",
        resolved_snippets=["Buffalo received 36 inches of snow"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_paraphrase_outside_alias_group_fails() -> None:
    """Paraphrase that does not match any alias → FAIL.

    This guards against false positives: if the model says "大约一米的
    降雪" (≈ 1 meter of snow) without using any of the curated aliases,
    the evaluator cannot verify the claim is grounded → FAIL.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="snowfall-amount",
            answer_alias_groups=[[
                "36 inches of snow",
                "降雪量达到36英寸",
            ]],
            source_aliases=["36 inches"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到大约一米的降雪。",  # no alias match
        resolved_snippets=["Buffalo received 36 inches of snow"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "not mentioned" in result.details


# ---------------------------------------------------------------------------
# P0-6 regression: fact in late article body, public snippet truncated
# ---------------------------------------------------------------------------


def test_fact_in_late_article_body_not_falsely_rejected() -> None:
    """Spec: "事实在正文后半部分但 public snippet 截断，仍不应误判".

    The previous implementation required the fact to appear in the
    500-char public snippet. If the fact was in the article body but
    the snippet was truncated before reaching it, the fact was marked
    "unsupported" — a false positive.

    The new contract uses ``source_aliases`` (canonical tokens curated
    by the case author). The author lists the canonical tokens that
    appear ANYWHERE in the article body — not just in the snippet. As
    long as at least one resolved evidence snippet contains the
    canonical token, the fact is grounded.

    In this test, the resolved evidence snippet contains the canonical
    token even though the public-facing 500-char snippet might have
    been truncated.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="late-fact-egypt-port",
            answer_alias_groups=[["Egypt", "埃及"]],
            source_aliases=["Alexandria"],  # canonical token, appears late in article
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章后段提到 Egypt 港口城市 Alexandria。",
        # The snippet contains the canonical token "Alexandria" — the
        # fact is grounded even though the public snippet might have
        # been truncated at 500 chars before reaching the relevant
        # paragraph in the original article body.
        resolved_snippets=["... earlier article body truncated ...\nAlexandria"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# P0-6: multiple alias groups (AND) vs aliases within group (OR)
# ---------------------------------------------------------------------------


def test_multiple_alias_groups_require_all_groups_hit() -> None:
    """Multiple alias groups = AND across groups.

    The fact is "mentioned" only when EVERY group has at least one
    alias hit. This is useful for compound facts like "Buffalo received
    36 inches of snow" where the answer must mention both the city
    AND the amount.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="compound-buffalo-snow",
            answer_alias_groups=[
                ["Buffalo", "布法罗"],  # group 1: city
                ["36 inches", "36英寸"],  # group 2: amount
            ],
            source_aliases=["Buffalo", "36 inches"],
            required=True,
            severity="high",
        ),
    ])
    # Answer mentions both groups → PASS
    artifact = _make_artifact(
        final_text="布法罗降雪量36英寸。",
        resolved_snippets=["Buffalo 36 inches"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True

    # Answer mentions only one group → FAIL
    artifact_missing_amount = _make_artifact(
        final_text="布法罗受到暴风雪影响。",  # no amount
        resolved_snippets=["Buffalo 36 inches"],
    )
    result_missing = evaluate_context_support(case, artifact_missing_amount)
    assert result_missing.passed is False
    assert "not mentioned" in result_missing.details


def test_aliases_within_group_are_or() -> None:
    """Aliases within a single group = OR.

    Any alias in the group satisfies the group. This is useful when
    the same fact can be expressed in multiple languages or phrasings.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto", "多伦多", "T.O."]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    # Any one of the aliases is sufficient.
    for alias in ["Toronto", "多伦多", "T.O."]:
        artifact = _make_artifact(
            final_text=f"文章提到了 {alias}。",
            resolved_snippets=["Toronto"],
        )
        result = evaluate_context_support(case, artifact)
        assert result.passed is True, f"failed for alias {alias!r}"


# ---------------------------------------------------------------------------
# P0-6: non-required facts and metadata-only facts
# ---------------------------------------------------------------------------


def test_non_required_fact_absent_passes() -> None:
    """Spec: "required=False 缺失不导致失败"."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="optional-context",
            answer_alias_groups=[["snowstorm warning"]],
            source_aliases=["warning"],
            required=False,  # informational only
            severity="low",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了降雪量。",  # no mention of "snowstorm warning"
        resolved_snippets=["warning"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_metadata_only_fact_passes() -> None:
    """Fact with no answer aliases and no source aliases → metadata-only.

    This covers facts like "the article does not mention year X" where
    there is no article evidence to cite. The fact is treated as
    "mentioned" (vacuously true) and "grounded" (vacuously true).
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="metadata-no-year",
            answer_alias_groups=[],  # no answer constraint
            source_aliases=[],       # no grounding constraint
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章未提及具体年份。",
        resolved_snippets=[],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# P0-6: capability boundary signal
# ---------------------------------------------------------------------------


def test_case_with_no_atomic_facts_signals_coverage_incomplete() -> None:
    """Spec: "明确报告 deterministic evaluator 的能力边界".

    When a case has no ``atomic_facts`` (and no legacy
    ``required_article_facts``), the deterministic evaluator cannot
    assert coverage. It signals this via ``coverage_incomplete=true``
    in the details string but does NOT fail the dimension.
    """
    case = _make_case_with_atomic_facts([])  # no atomic facts
    artifact = _make_artifact(
        final_text="文章提到了一些城市。",
        resolved_snippets=["city"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True
    assert "coverage_incomplete=true" in result.details


# ---------------------------------------------------------------------------
# P0-6: legacy required_article_facts migration
# ---------------------------------------------------------------------------


def test_legacy_required_article_facts_migration() -> None:
    """Legacy ``required_article_facts`` is auto-converted to ``atomic_facts``.

    The loader's migration function wraps each legacy sentence as a
    single-alias :class:`AtomicExpectedFact` with ``required=True``
    and ``severity="high"``. The evaluator then treats it as a normal
    atomic fact.
    """
    case = _make_case_with_legacy_facts(["Thunder Bay", "Toronto"])
    # Before migration: atomic_facts is empty
    assert case.expected.atomic_facts == []
    # Run the loader's migration
    _migrate_legacy_required_article_facts(case)
    # After migration: two atomic facts with single-alias groups
    assert len(case.expected.atomic_facts) == 2
    assert case.expected.atomic_facts[0].fact_id == "legacy-0"
    assert case.expected.atomic_facts[0].answer_alias_groups == [["Thunder Bay"]]
    assert case.expected.atomic_facts[0].required is True
    assert case.expected.atomic_facts[0].severity == "high"

    # The evaluator should now work on the migrated case.
    artifact = _make_artifact(
        final_text="文章提到的城市包括 Thunder Bay 和 Toronto。",
        resolved_snippets=["Thunder Bay", "Toronto"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_legacy_required_article_facts_skipped_when_atomic_facts_present() -> None:
    """When both fields are present, ``atomic_facts`` wins (new contract)."""
    case = ReaderRecordAskR4A3Case(
        id="t-both",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="...",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            required_article_facts=["legacy sentence"],
            atomic_facts=[
                AtomicExpectedFact(
                    fact_id="new-contract-fact",
                    answer_alias_groups=[["new alias"]],
                    source_aliases=["new source"],
                    required=True,
                    severity="high",
                )
            ],
        ),
    )
    _migrate_legacy_required_article_facts(case)
    # atomic_facts unchanged — legacy field ignored.
    assert len(case.expected.atomic_facts) == 1
    assert case.expected.atomic_facts[0].fact_id == "new-contract-fact"


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------


def test_highest_severity_among_failing_facts() -> None:
    """When multiple facts fail, the dimension severity is the highest."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="low-severity-fact",
            answer_alias_groups=[["missing-alias-low"]],
            source_aliases=["x"],
            required=True,
            severity="low",
        ),
        AtomicExpectedFact(
            fact_id="high-severity-fact",
            answer_alias_groups=[["missing-alias-high"]],
            source_aliases=["y"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了一些城市。",  # neither alias present
        resolved_snippets=["x", "y"],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
