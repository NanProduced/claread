"""Shared offline fixtures for the teaching-v2 workflow tests.

Builds one gate-passing five-stage chain (blueprint → language_support →
translation → semantic_review [→ refinement]) over three reading units so
both the workflow regression file and the performance/usage file drive the
same canonical happy path. All values are hand-crafted to satisfy the
shared hard gates (teaching/gates.py) and the deterministic teaching
contract (teaching/prototype.validate_teaching_contract).
"""

from __future__ import annotations

from unittest.mock import patch

from app.schemas.internal.daily_lesson_v2 import (
    BlueprintDraft,
    CheckpointDraft,
    ContractResultDraft,
    LanguageSupportDraft,
    LanguageTargetDraft,
    RefinementDraft,
    ReviewIssueDraft,
    SemanticReviewDraft,
    SentenceMapDraft,
    StructureNodeDraft,
    TransferTaskDraft,
    TranslationDraft,
    TranslationItemDraft,
)
from app.services.daily_reader.teaching.prototype import SEMANTIC_REVIEW_CONTRACTS

READING_UNITS = [
    {
        "id": "u01",
        "text": (
            "Substantive analysis explains a complex policy choice with "
            "evidence and context for readers who want to understand its "
            "wider consequences. The ministry published the review after "
            "months of deliberation, and several independent groups have "
            "now examined the underlying dataset in detail, offering a "
            "rare window into how the decision was actually made."
        ),
    },
    {
        "id": "u02",
        "text": (
            "Officials published the figures after a long review, and "
            "independent researchers confirmed the main trends this week. "
            "The data covers a period of sustained volatility, but the "
            "headline numbers remain broadly stable once seasonal "
            "adjustments are taken into account, according to the "
            "methodology note attached to the release."
        ),
    },
    {
        "id": "u03",
        "text": (
            "Analysts expect the decision to reshape the market, although "
            "the immediate effect remains limited for most consumers. "
            "Some commentators argue that the change will take years to "
            "filter through supply chains, while others point to faster "
            "adjustments in sectors that depend heavily on imported "
            "components."
        ),
    },
]

U01_TRANSLATION = "实质性分析为想理解其广泛后果的读者解释了一项复杂的政策选择，并提供了证据与背景。"
U02_TRANSLATION = "官员们在长期审查之后公布了这些数据，独立研究者本周确认了主要趋势。"
U03_TRANSLATION = "分析师预计这一决定将重塑市场，尽管对多数消费者而言直接影响仍然有限。"

ARTICLE_TEXT = "\n\n".join(unit["text"] for unit in READING_UNITS)

WORKFLOW_MODULE = "app.services.daily_reader.workflow"


def make_blueprint() -> BlueprintDraft:
    return BlueprintDraft(
        article_type="news_report",
        effective_difficulty="B1",
        title_zh="复杂政策如何重塑市场",
        subtitle_zh="一篇以证据解释后果的分析",
        tags_zh=["公共政策", "市场观察"],
        reading_mission="带着“政策变化与证据”的问题阅读这篇报道。",
        reading_mission_stance="neutral",
        learning_objectives=["抓住报道中的变化内容与证据", "学会转述官方说法"],
        structure_map=[
            StructureNodeDraft(
                label="政策与证据", function="opening", paragraph_ids=["u01", "u02"]
            ),
            StructureNodeDraft(label="影响与展望", function="closing", paragraph_ids=["u03"]),
        ],
        selected_paragraph_ids=["u01", "u02"],
        comprehension_checkpoints=[
            CheckpointDraft(
                skill="fact_location",
                prompt="官方在什么之后公布了数据？",
                prompt_subject="官方公布数据的时机",
                reference_answer="在长期审查之后。",
                reference_answer_subject="公布时机",
                evidence_paragraph_ids=["u02"],
                answer_evidence_paragraph_ids=["u02"],
            ),
            CheckpointDraft(
                skill="main_idea",
                prompt="这篇报道主要解释了什么？",
                prompt_subject="报道主旨",
                reference_answer="一项复杂政策选择及其证据与影响。",
                reference_answer_subject="报道主旨",
                evidence_paragraph_ids=["u01"],
                answer_evidence_paragraph_ids=["u01"],
            ),
        ],
        transfer_task=TransferTaskDraft(
            task_kind="retell",
            content_requirement="fact_chain",
            required_language_target_expressions=["substantive analysis"],
            prompt="用 substantive analysis 复述这篇报道的核心结论。",
            scaffold="The report offers a substantive analysis of ...",
            reference_points=["政策变化", "证据确认", "市场影响"],
        ),
    )


def make_language_support() -> LanguageSupportDraft:
    return LanguageSupportDraft(
        language_targets=[
            LanguageTargetDraft(
                expression="substantive analysis",
                paragraph_id="u01",
                target_kind="phrase",
                teaching_purpose="学术表达迁移",
                meaning_zh="实质性分析",
                usage_note="用于正式语境描述深入分析。",
                reusable_pattern="offer a substantive analysis of",
            ),
            LanguageTargetDraft(
                expression="independent researchers",
                paragraph_id="u02",
                target_kind="phrase",
                teaching_purpose="信源表达",
                meaning_zh="独立研究者",
                usage_note="强调非官方立场的研究者。",
                reusable_pattern="independent researchers confirmed",
            ),
            LanguageTargetDraft(
                expression="immediate effect",
                paragraph_id="u03",
                target_kind="phrase",
                teaching_purpose="影响表达",
                meaning_zh="直接影响",
                usage_note="与长期影响对照使用。",
                reusable_pattern="the immediate effect remains",
            ),
        ],
        sentence_maps=[
            SentenceMapDraft(
                sentence=(
                    "Substantive analysis explains a complex policy choice with "
                    "evidence and context for readers who want to understand its "
                    "wider consequences."
                ),
                paragraph_id="u01",
                translation=U01_TRANSLATION,
                complexity_kind=None,
                teaching_purpose="主干结构演示",
            )
        ],
        high_difficulty_unit_ids=["u02"],
    )


def make_translation() -> TranslationDraft:
    return TranslationDraft(
        translations=[
            TranslationItemDraft(paragraph_id="u01", translation=U01_TRANSLATION),
            TranslationItemDraft(paragraph_id="u02", translation=U02_TRANSLATION),
            TranslationItemDraft(paragraph_id="u03", translation=U03_TRANSLATION),
        ]
    )


def make_review_pass() -> SemanticReviewDraft:
    return SemanticReviewDraft(
        verdict="PASS",
        issues=[],
        remaining_issues=[],
        contract_results=[
            ContractResultDraft(contract=contract, passed=True, rationale="证据充分，通过审核。")
            for contract in SEMANTIC_REVIEW_CONTRACTS
        ],
        reviewed_at_stage="before_refinement",
        refinement_requested=False,
    )


def make_review_fail(failed_contract: str = "language_target_value") -> SemanticReviewDraft:
    return SemanticReviewDraft(
        verdict="FAIL",
        issues=[
            ReviewIssueDraft(
                contract=failed_contract,
                field="learning_package.language_targets",
                problem="释义不够具体，学习价值不足。",
            )
        ],
        remaining_issues=["需要更具体的语言目标释义。"],
        contract_results=[
            ContractResultDraft(
                contract=contract,
                passed=contract != failed_contract,
                rationale="通过：证据充分。" if contract != failed_contract else "释义过于笼统。",
            )
            for contract in SEMANTIC_REVIEW_CONTRACTS
        ],
        reviewed_at_stage="before_refinement",
        refinement_requested=True,
    )


def make_refinement() -> RefinementDraft:
    improved = make_language_support()
    for target in improved.language_targets:
        target.usage_note = target.usage_note + "（含搭配与语域提示。）"
    return RefinementDraft(
        refinement_patch={"language_targets": improved.model_dump()["language_targets"]},
        rechecked_contract_results=[
            ContractResultDraft(
                contract="language_target_value",
                passed=True,
                rationale="已补充具体释义与用法说明。",
            )
        ],
        remaining_issues=[],
    )


def make_usage(stage: str, model_requests: int = 1) -> dict:
    return {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
        "model_requests": model_requests,
        "tool_calls": 0,
        "stage": stage,
    }


def _span_response(output, usage: dict | None):
    return {"output": output, "usage_metadata": usage}


async def _blueprint_span(*, deps, prompt, metadata, run_usage=None):
    return _span_response(make_blueprint(), make_usage("blueprint"))


async def _language_support_span(*, deps, prompt, metadata, run_usage=None):
    return _span_response(make_language_support(), make_usage("language_support"))


async def _translation_span(*, deps, prompt, metadata, run_usage=None):
    return _span_response(make_translation(), make_usage("translation"))


async def _semantic_review_span(*, deps, prompt, metadata, run_usage=None):
    return _span_response(make_review_pass(), make_usage("semantic_review"))


async def _refinement_span(*, deps, prompt, metadata, run_usage=None):
    return _span_response(make_refinement(), make_usage("refinement"))


class v2_happy_path:  # noqa: N801  (context manager, test-local naming)
    """Patch the five stage spans to the canonical happy-path chain.

    ``review``/``refinement`` may be overridden for the FAIL→refine path.
    """

    def __init__(self, *, review=None, refinement=None):
        self.review = review
        self.refinement = refinement

    def __enter__(self):
        self.review_span = self.review if self.review is not None else _semantic_review_span
        self.patches = [
            patch(f"{WORKFLOW_MODULE}._run_blueprint_llm_span", new=_blueprint_span),
            patch(f"{WORKFLOW_MODULE}._run_language_support_llm_span", new=_language_support_span),
            patch(f"{WORKFLOW_MODULE}._run_translation_llm_span", new=_translation_span),
            patch(f"{WORKFLOW_MODULE}._run_semantic_review_llm_span", new=self.review_span),
        ]
        if self.refinement is not None:
            self.patches.append(
                patch(f"{WORKFLOW_MODULE}._run_teaching_refinement_llm_span", new=self.refinement)
            )
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()
        return False


def graph_input_state() -> dict:
    return {
        "original_text": ARTICLE_TEXT,
        "title": "Policy analysis",
        "source": "bbc",
        "source_url": "https://example.test/policy-analysis",
        "difficulty": "B2",
        "pipeline_source": "test",
        "pipeline_meta": {},
    }
