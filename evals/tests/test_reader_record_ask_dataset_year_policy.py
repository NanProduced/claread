"""Dataset year-policy tests.

Covers audit-finding repair scenarios (4) and (5):

* (4) synthetic absent-year fixture contains no year — the design property
  that an ``absent_year`` synthetic fixture MUST NOT carry year / ISO-date /
  CN-date / relative-time tokens in its article body. Otherwise the
  evaluator's ``must_declare_no_year=True`` check is meaningless.
* (5) BBC fixture contains 2015 → evaluator must not judge unsupported —
  when a BBC fixture's article body legitimately contains ``2015`` (e.g. as
  a passing historical reference), the case MUST list ``2015`` in
  ``allowed_temporal_claims`` so the evaluator does not flag it as an
  unsupported temporal claim.

Constraint (dataset Git governance, see
``test_reader_record_ask_dataset.py``): tests MUST NOT depend on the local
ignored working dataset under ``evals/tmp/``. Each test builds a minimal
synthetic case via factory helpers and exercises the evaluator directly.
No real Reading Record UUID, BBC body, or run artifact appears in tracked
fixtures.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.unsupported_temporal_claims import (
    CN_DATE_RE,
    ISO_DATE_RE,
    RELATIVE_TIME_WORDS,
    YEAR_RE,
    evaluate_unsupported_temporal_claims,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskCase,
    ReaderRecordAskExpected,
)

# ---------------------------------------------------------------------------
# Representative synthetic absent-year article body.
#
# Mirrors the design of the canonical synthetic absent-year fixture: a
# purely descriptive scene with NO year / ISO date / CN date / relative
# time tokens. Hardcoded here (not loaded from ``evals/tmp/``) to keep the
# test self-contained and reproducible.
# ---------------------------------------------------------------------------
_SYN_ABSENT_YEAR_ARTICLE = (
    "清晨的滨江公园里，雾气还未散去。几位老人在凉亭里下棋，旁边的石桌上摆着茶壶和保温杯。\n\n"
    "沿着江边的步道，有人慢跑，有人遛狗。草坪上，几位家长带着孩子在做拉伸操。"
    "远处传来鸟鸣，偶尔夹杂着自行车铃铛声。\n\n"
    "公园的管理员老张推着小车巡视，捡起地上的落叶和纸屑。他说，公园每天清晨五点开门，"
    "傍晚七点关门，免费向市民开放。\n\n"
    "凉亭里的棋局结束，老人们收拾棋盘，相约明天再来。江风微微吹过，带起一阵清新的草香。"
)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_synthetic_absent_year_case() -> ReaderRecordAskCase:
    """Build a synthetic absent-year case mirroring the canonical fixture."""
    return ReaderRecordAskCase(
        id="syn-absent-year",
        source_kind="synthetic_short",
        record_id=None,
        article_text=_SYN_ABSENT_YEAR_ARTICLE,
        article_title="滨江公园的清晨",
        input_mode="manual",
        selection=None,
        rag_mode="off",
        source_metadata="unknown",
        baseline_mode="complete",
        external_knowledge_policy="forbidden",
        question="文章没有提到的年份是什么？不得猜测。",
        question_category="absent_year",
        expected=ReaderRecordAskExpected(
            expected_entity_set={},
            allowed_temporal_claims=[],
            allowed_numerics=[],
            allowed_entities_by_type={},
            required_article_facts=[
                "正文为纯描写性场景，不包含任何年份信息",
                "答案必须显式声明文章未提供年份，不得编造具体年份",
            ],
            forbidden_answer_patterns=["2024", "2025", "2026"],
            answer_language="zh",
            expect_tool_calls="forbidden",
            must_declare_no_year=True,
            must_distinguish_external_knowledge=False,
        ),
        tags=["synthetic", "absent_year", "manual_input", "absent_temporal"],
        phase_tags=["real_phase1"],
    )


def _make_bbc_case_with_2015_allowed(
    *, question_category: str = "main_idea"
) -> ReaderRecordAskCase:
    """Build a BBC-unknown case that allows 2015 in answers.

    Models the post-repair design: BBC fixtures whose article body contains
    ``2015`` MUST list ``2015`` in ``allowed_temporal_claims`` so the
    evaluator does not flag it as unsupported.
    """
    return ReaderRecordAskCase(
        id="bbc-fixture-2015",
        source_kind="bbc_record",
        record_id=None,  # no real UUID in tracked fixtures
        article_text=None,
        article_title=None,
        input_mode="no_selection",
        selection=None,
        rag_mode="off",
        source_metadata="unknown",
        baseline_mode="complete",
        external_knowledge_policy="forbidden",
        question="这篇文章主要说了什么？",
        question_category=question_category,
        expected=ReaderRecordAskExpected(
            expected_entity_set={},
            allowed_temporal_claims=["2015"],
            allowed_numerics=[],
            allowed_entities_by_type={},
            required_article_facts=[
                "BBC 正文包含 2015 年份信息，allowed_temporal_claims 必须包含 2015"
            ],
            forbidden_answer_patterns=["2025", "2026"],
            answer_language="zh",
            expect_tool_calls="forbidden",
            must_declare_no_year=False,
            must_distinguish_external_knowledge=False,
        ),
        tags=["bbc", question_category, "source_unknown"],
        phase_tags=["real_phase1"],
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-year-policy",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


# ---------------------------------------------------------------------------
# Scenario 4 — synthetic absent-year fixture contains no year
# ---------------------------------------------------------------------------


class TestSyntheticAbsentYearFixtureHasNoYearTokens:
    """Scenario (4): the synthetic absent-year fixture MUST NOT contain
    year / ISO-date / CN-date / relative-time tokens in its article body.

    If it did, the evaluator's ``must_declare_no_year=True`` check would
    be testing the model's restraint against a year that is actually
    visible in the article — which is the exact BBC-2015 failure mode we
    are repairing. The synthetic fixture exists precisely to give the
    model a clean "no year anywhere" context.
    """

    def test_no_4_digit_year_in_article(self) -> None:
        matches = YEAR_RE.findall(_SYN_ABSENT_YEAR_ARTICLE)
        assert matches == [], (
            f"synthetic absent-year article must not contain 4-digit year "
            f"tokens; found: {matches}"
        )

    def test_no_iso_date_in_article(self) -> None:
        matches = ISO_DATE_RE.findall(_SYN_ABSENT_YEAR_ARTICLE)
        assert matches == [], (
            f"synthetic absent-year article must not contain ISO date "
            f"tokens; found: {matches}"
        )

    def test_no_cn_date_in_article(self) -> None:
        matches = CN_DATE_RE.findall(_SYN_ABSENT_YEAR_ARTICLE)
        assert matches == [], (
            f"synthetic absent-year article must not contain CN short-date "
            f"tokens; found: {matches}"
        )

    def test_no_relative_time_word_in_article(self) -> None:
        offenders = [
            w for w in RELATIVE_TIME_WORDS if w in _SYN_ABSENT_YEAR_ARTICLE
        ]
        assert offenders == [], (
            f"synthetic absent-year article must not contain relative-time "
            f"words; found: {offenders}"
        )

    def test_design_invariants_hold(self) -> None:
        """The synthetic absent-year case must combine:
        * ``allowed_temporal_claims=[]`` (no year allowed in answer)
        * ``must_declare_no_year=True`` (answer must declare no year)
        * ``phase_tags`` includes ``real_phase1`` (eligible for real eval)
        """
        case = _make_synthetic_absent_year_case()
        assert case.expected.allowed_temporal_claims == []
        assert case.expected.must_declare_no_year is True
        assert "real_phase1" in case.phase_tags


class TestSyntheticAbsentYearEvaluatorBehavior:
    """Scenario (4) extension: the evaluator behaves correctly on a
    synthetic absent-year case.

    * Answer that declares no year → pass
    * Answer that contains a year token → fail (must_declare_no_year=True)
    * Answer without year token and without declaration → fail
    """

    def test_passes_when_answer_declares_no_year(self) -> None:
        case = _make_synthetic_absent_year_case()
        artifact = _make_artifact("文章未提供具体年份。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is True
        assert result.severity == "none"

    def test_fails_when_answer_contains_year_token(self) -> None:
        case = _make_synthetic_absent_year_case()
        artifact = _make_artifact("文章提到的年份是 2025 年。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is False
        assert result.severity == "high"
        # must_declare_no_year check fires on the year token
        assert "must_declare_no_year" in result.details

    def test_fails_when_answer_lacks_declaration(self) -> None:
        case = _make_synthetic_absent_year_case()
        # No year token but also no "未提供/未提及/没有提到" declaration.
        artifact = _make_artifact("文章讨论了公园清晨的场景。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is False
        assert "lacks no-year declaration" in result.details


# ---------------------------------------------------------------------------
# Scenario 5 — BBC fixture contains 2015 → evaluator must not judge unsupported
# ---------------------------------------------------------------------------


class TestBBCFixtureWith2015Allowed:
    """Scenario (5): when a BBC fixture's article body contains ``2015``,
    the case MUST list ``2015`` in ``allowed_temporal_claims`` so the
    evaluator does not flag answers that mention 2015 as unsupported.

    This is the core repair for the audit finding: previously, BBC unknown
    cases had ``allowed_temporal_claims=[]`` even though the BBC article
    body contains ``2015``. Any model answer that faithfully cited 2015
    from the article would be flagged as unsupported — a false positive
    that punishes the model for correctly grounding in the article.
    """

    def test_design_invariant_allowed_contains_2015(self) -> None:
        case = _make_bbc_case_with_2015_allowed()
        assert "2015" in case.expected.allowed_temporal_claims
        # must_declare_no_year MUST be False when 2015 is in the article
        assert case.expected.must_declare_no_year is False

    def test_2015_in_answer_passes(self) -> None:
        case = _make_bbc_case_with_2015_allowed()
        artifact = _make_artifact(
            "文章提到 2015 年的火灾季节，影响了 Thunder Bay 周边地区。"
        )
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is True, (
            f"2015 is in allowed_temporal_claims; evaluator must not flag "
            f"it as unsupported. details: {result.details}"
        )
        assert result.severity == "none"

    def test_2015_year_only_in_answer_passes(self) -> None:
        case = _make_bbc_case_with_2015_allowed()
        artifact = _make_artifact("2015 年是文章涉及的关键年份。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is True

    def test_2025_in_answer_still_fails(self) -> None:
        """Allowing 2015 must NOT relax the check for unrelated years."""
        case = _make_bbc_case_with_2015_allowed()
        artifact = _make_artifact("文章发布于 2025 年。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is False
        assert "2025" in result.details
        assert "unsupported temporal tokens" in result.details

    def test_2026_in_answer_still_fails(self) -> None:
        case = _make_bbc_case_with_2015_allowed()
        artifact = _make_artifact("2026 年的情况与之类似。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        assert result.passed is False
        assert "2026" in result.details


class TestBBCFixtureEmptyAllowedIsBrokenPattern:
    """Contrast test: ``allowed_temporal_claims=[]`` on a BBC fixture whose
    article contains 2015 is the broken pre-repair pattern.

    This test documents WHY the repair is necessary: with empty allowed,
    any model answer that cites 2015 from the article is flagged as
    unsupported. The repair adds 2015 to allowed so the model is not
    punished for faithful grounding.
    """

    def test_empty_allowed_with_2015_in_answer_fails(self) -> None:
        case = ReaderRecordAskCase(
            id="bbc-broken-pattern",
            source_kind="bbc_record",
            record_id=None,
            article_text=None,
            article_title=None,
            input_mode="no_selection",
            selection=None,
            rag_mode="off",
            source_metadata="unknown",
            baseline_mode="complete",
            external_knowledge_policy="forbidden",
            question="这篇文章主要说了什么？",
            question_category="main_idea",
            expected=ReaderRecordAskExpected(
                allowed_temporal_claims=[],
                must_declare_no_year=False,
            ),
            tags=["bbc", "main_idea"],
            phase_tags=["real_phase1"],
        )
        artifact = _make_artifact("文章提到 2015 年的火灾季节。")
        result = evaluate_unsupported_temporal_claims(case, artifact)
        # This is the BROKEN behavior — 2015 cited from article is flagged.
        assert result.passed is False
        assert "2015" in result.details


# ---------------------------------------------------------------------------
# Combined policy invariants
# ---------------------------------------------------------------------------


class TestYearPolicyInvariants:
    """Cross-cutting invariants for the dataset year-policy repair."""

    def test_synthetic_absent_year_case_has_no_year_in_article(self) -> None:
        """Scenario (4) at the case level: synthetic absent-year case's
        article_text MUST NOT contain any year-like token."""
        case = _make_synthetic_absent_year_case()
        assert case.article_text is not None
        article = case.article_text
        assert YEAR_RE.search(article) is None
        assert ISO_DATE_RE.search(article) is None
        assert CN_DATE_RE.search(article) is None
        for word in RELATIVE_TIME_WORDS:
            assert word not in article

    def test_synthetic_absent_year_case_policy_combination(self) -> None:
        """The synthetic absent-year case combines:
        * no year in article (verified above)
        * ``allowed_temporal_claims=[]`` (no year allowed in answer)
        * ``must_declare_no_year=True`` (answer must declare no year)

        This combination is valid ONLY when the article has no year.
        """
        case = _make_synthetic_absent_year_case()
        assert case.expected.allowed_temporal_claims == []
        assert case.expected.must_declare_no_year is True

    def test_bbc_allow_2015_case_policy_combination(self) -> None:
        """The BBC case with 2015 in article combines:
        * ``allowed_temporal_claims=["2015"]`` (2015 allowed in answer)
        * ``must_declare_no_year=False`` (answer may mention 2015)

        This combination is valid when the BBC article body contains 2015.
        """
        case = _make_bbc_case_with_2015_allowed()
        assert "2015" in case.expected.allowed_temporal_claims
        assert case.expected.must_declare_no_year is False

    def test_no_contradiction_between_allowed_and_must_declare(self) -> None:
        """Invariant: ``must_declare_no_year=True`` requires
        ``allowed_temporal_claims=[]``. Otherwise the evaluator would
        simultaneously allow a year (via allowed) and forbid it (via
        must_declare_no_year) — a contradiction.

        Conversely, if ``allowed_temporal_claims`` is non-empty,
        ``must_declare_no_year`` MUST be False.
        """
        # Synthetic absent-year: allowed=[] + must_declare_no_year=True → OK
        syn_case = _make_synthetic_absent_year_case()
        assert syn_case.expected.allowed_temporal_claims == []
        assert syn_case.expected.must_declare_no_year is True

        # BBC allow-2015: allowed=["2015"] + must_declare_no_year=False → OK
        bbc_case = _make_bbc_case_with_2015_allowed()
        assert bbc_case.expected.allowed_temporal_claims == ["2015"]
        assert bbc_case.expected.must_declare_no_year is False
