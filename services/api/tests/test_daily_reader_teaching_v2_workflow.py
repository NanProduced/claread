"""P-5B: teaching-v2 five-stage workflow full-chain regressions.

Offline coverage for the five defense lines wired to the shared teaching
package:

1. DTO hard boundary (counts / UnitId / required fields / title contract)
   with in-call retries — pydantic stage DTOs.
2. Deterministic contract checks into the semantic-review input and the
   fail-closed replay after refinement.
3. Post-patch DTO re-check + patch rejection + pre-image restore + FAIL +
   batch continue (P-4I semantics).
4. Stop/rejection diagnostics (abort_reason / abort_diagnostics).
5. Usage conservation + the frozen per-article budget caps.

Plus: the prompt-registry fidelity contract (registry instructions are a
verbatim prefix of the evals-canonical stage prompts) and the
fail-closed abort semantics (aborted articles carry no lesson payload).
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
from daily_reader_teaching_v2_fixtures import (
    READING_UNITS,
    _refinement_span,
    graph_input_state,
    make_blueprint,
    make_language_support,
    make_refinement,
    make_review_fail,
    make_review_pass,
    make_translation,
    make_usage,
    v2_happy_path,
)
from pydantic import ValidationError

from app.schemas.internal.daily_lesson_v2 import (
    BlueprintDraft,
    LanguageSupportDraft,
    SemanticReviewDraft,
)
from app.services.daily_reader.teaching.prototype import (
    SEMANTIC_REVIEW_CONTRACTS,
    build_blueprint_prompt,
    build_language_support_prompt,
    build_refinement_prompt,
    build_semantic_review_prompt,
    build_translation_prompt,
)
from app.services.daily_reader.workflow import (
    TEACHING_V2_MODEL_REQUESTS_MAX,
    TEACHING_V2_OUTPUT_TOKENS_MAX,
    build_daily_reader_graph,
    daily_projection_node,
    refinement_node,
    semantic_review_node,
    translation_node,
)
from app.services.prompting.prompt_loader import load_agent_instructions

WORKFLOW_MODULE = "app.services.daily_reader.workflow"


def _package_state() -> dict:
    blueprint = make_blueprint().model_dump()
    language_support = make_language_support().model_dump()
    package = {
        "comprehension_checkpoints": blueprint["comprehension_checkpoints"],
        "high_difficulty_unit_ids": language_support["high_difficulty_unit_ids"],
        "language_targets": language_support["language_targets"],
        "sentence_maps": language_support["sentence_maps"],
        "transfer_task": blueprint["transfer_task"],
        "translations_by_paragraph_id": {
            item["paragraph_id"]: item["translation"]
            for item in make_translation().model_dump()["translations"]
        },
    }
    return {
        "original_text": "\n\n".join(unit["text"] for unit in READING_UNITS),
        "reading_units": READING_UNITS,
        "lesson_blueprint": blueprint,
        "language_support": language_support,
        "learning_package": package,
        "derived_translation_unit_ids": ["u01", "u02", "u03"],
        "teaching_contract_issues": [],
        "semantic_review_result": make_review_pass().model_dump(),
        "source_url": "https://example.test/policy-analysis",
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Full chain (five stages, happy path + refinement path)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_chain_passes_gates_and_lands_lesson_v2():
    with v2_happy_path():
        final_state = await build_daily_reader_graph().ainvoke(graph_input_state())

    assert not final_state.get("abort")
    lesson_v2 = final_state["lesson_v2"]
    assert lesson_v2["lesson_blueprint"]["article_type"] == "news_report"
    assert lesson_v2["learning_package"]["translations_by_paragraph_id"].keys() == {
        "u01",
        "u02",
        "u03",
    }
    assert lesson_v2["run_meta"]["outcome"] == "cleaned_publish"
    assert lesson_v2["run_meta"]["refinement_count"] == 0
    assert lesson_v2["run_meta"]["hard_gates"]["all_passed"] is True
    assert lesson_v2["run_meta"]["review"]["verdict"] == "PASS"
    # body projection: plain paragraphs with the teaching unit ids
    assert [p["id"] for p in final_state["body_json"]["paragraphs"]] == ["u01", "u02", "u03"]
    # defense line 5: usage conservation — aggregate equals the stage sum
    summary = final_state["usage_summary"]
    assert summary["available"] is True
    assert set(summary["per_agent"]) == {
        "blueprint",
        "language_support",
        "translation",
        "semantic_review",
    }
    assert summary["aggregate"]["model_requests"] == 4


@pytest.mark.anyio
async def test_full_chain_fail_review_runs_sole_refinement_then_lands():
    async def failing_review(**_kwargs):
        return {"output": make_review_fail(), "usage_metadata": make_usage("semantic_review")}

    with v2_happy_path(review=failing_review, refinement=_refinement_span):
        final_state = await build_daily_reader_graph().ainvoke(graph_input_state())

    assert not final_state.get("abort")
    refinement = final_state["refinement_result"]
    assert refinement["refinement_count"] == 1
    assert refinement["review_after_refinement"]["verdict"] == "PASS"
    assert refinement["hard_gate_replay"]["all_passed"] is True
    # the patched package keeps the anchor contract
    lesson_v2 = final_state["lesson_v2"]
    assert lesson_v2["run_meta"]["refinement_count"] == 1
    assert lesson_v2["run_meta"]["hard_gates"]["all_passed"] is True
    assert final_state["usage_summary"]["aggregate"]["model_requests"] == 5


@pytest.mark.anyio
async def test_transcript_rejection_aborts_before_any_stage():
    final_state = await build_daily_reader_graph().ainvoke(
        {
            "original_text": "JUANA SUMMERS, HOST: words.\nKATIA RIDDLE, BYLINE: more words.",
            "title": "transcript",
            "pipeline_meta": {},
        }
    )
    assert final_state.get("abort") is True
    assert final_state["abort_reason"] == "transcript_rejected"
    # fail-closed: no lesson payload on aborted runs
    assert "lesson_v2" not in final_state
    assert "body_json" not in final_state


# ---------------------------------------------------------------------------
# Defense line 1: DTO hard boundary
# ---------------------------------------------------------------------------


def test_blueprint_dto_rejects_bad_unit_id_and_counts():
    base = make_blueprint().model_dump()
    bad_anchor = {**base, "selected_paragraph_ids": ["14"]}
    with pytest.raises(ValidationError):
        BlueprintDraft.model_validate(bad_anchor)

    too_many_targets = make_language_support().model_dump()
    too_many_targets["language_targets"] = too_many_targets["language_targets"] * 2
    with pytest.raises(ValidationError):
        LanguageSupportDraft.model_validate(too_many_targets)


def test_blueprint_dto_enforces_title_contract():
    base = make_blueprint().model_dump()
    # DTO floor: presence/shape (blank title, tag count, frozen stance).
    # The 8-18 char length bounds stay with the shared counts_in_bounds
    # gate (asserted below) — the DTO never re-implements them.
    with pytest.raises(ValidationError):
        BlueprintDraft.model_validate({**base, "title_zh": ""})
    with pytest.raises(ValidationError):
        BlueprintDraft.model_validate({**base, "tags_zh": ["只有一个"]})
    with pytest.raises(ValidationError):
        BlueprintDraft.model_validate({**base, "reading_mission_stance": "persuasive"})
    # length bounds live in the shared gate
    from app.services.daily_reader.teaching.gates import run_hard_gates

    artifact = {
        "lesson_blueprint": {**base, "title_zh": "过短"},
        "learning_package": {},
        "source_assets": {"source_caption": ""},
        "run_meta": {"outcome": "cleaned_publish", "refinement_count": 0},
    }
    gates = run_hard_gates({"input": {"reading_units": READING_UNITS}}, artifact)
    assert gates["gates"]["counts_in_bounds"]["passed"] is False


def test_semantic_review_dto_delegates_to_canonical_contract():
    dump = make_review_pass().model_dump()
    # PASS with a non-empty issue list violates the canonical contract and
    # must burn an output retry at validation time.
    with pytest.raises(ValidationError):
        SemanticReviewDraft.model_validate(
            {
                **dump,
                "issues": [
                    {
                        "contract": "evidence_anchors",
                        "field": "learning_package.language_targets",
                        "problem": "锚点不一致。",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        SemanticReviewDraft.model_validate({**dump, "refinement_requested": True})


# ---------------------------------------------------------------------------
# Defense line 2: deterministic contract into review input + fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deterministic_contract_issues_feed_review_input(monkeypatch):
    state = _package_state()
    # break the verbatim anchor contract on one language target
    state["learning_package"]["language_targets"][0]["expression"] = "substantive analyses"
    state["teaching_contract_issues"] = [
        {
            "code": "teaching_anchor_not_verbatim",
            "field": "language_targets[0]",
            "detail": "target expression is not a verbatim quote of its anchored unit",
        }
    ]

    captured: dict = {}

    async def review_span(*, deps, prompt, metadata, run_usage=None):
        captured["prompt"] = prompt
        captured["deps"] = deps
        return {"output": make_review_pass(), "usage_metadata": make_usage("semantic_review")}

    monkeypatch.setattr(f"{WORKFLOW_MODULE}._run_semantic_review_llm_span", review_span)
    await semantic_review_node(state)

    # the deterministic issue is visible to the reviewer, never swallowed
    assert "teaching_anchor_not_verbatim" in captured["prompt"]
    assert captured["deps"].deterministic_checks["teaching_contract_issues"]


@pytest.mark.anyio
async def test_hard_gate_failure_aborts_projection():
    state = _package_state()
    # anchors_resolve gate must fail: the expression no longer quotes the unit
    state["learning_package"]["language_targets"][0]["expression"] = "substantive analyses"

    result = daily_projection_node(state)

    assert result["abort"] is True
    assert result["abort_reason"] == "teaching_v2_hard_gates_failed"
    assert result["abort_diagnostics"]["failed_gates"] == ["anchors_resolve"]
    # usage stays conserved on the fail-closed stop
    assert "usage_summary" in result


# ---------------------------------------------------------------------------
# Defense line 3: post-patch DTO re-check / rejection / pre-image restore
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refinement_patch_outside_allowlist_is_rejected_and_restores():
    state = _package_state()
    state["semantic_review_result"] = make_review_fail().model_dump()
    package_before = {
        key: (list(value) if isinstance(value, list) else dict(value))
        for key, value in state["learning_package"].items()
    }

    async def rogue_refinement(**_kwargs):
        patch_output = {
            "refinement_patch": {
                # outside fields_to_fix (only language_targets is directed)
                "translations_by_paragraph_id": {
                    "u01": "被篡改的译文。",
                }
            },
            "rechecked_contract_results": [
                {
                    "contract": "language_target_value",
                    "passed": True,
                    "rationale": "声称已修复。",
                }
            ],
            "remaining_issues": [],
        }
        return {
            "output": make_refinement().__class__.model_validate(patch_output),
            "usage_metadata": make_usage("refinement"),
        }

    with patch(f"{WORKFLOW_MODULE}._run_teaching_refinement_llm_span", new=rogue_refinement):
        result = await refinement_node(state)

    # fail-closed: the rogue patch is rejected, the article aborts with FAIL
    assert result["abort"] is True
    assert result["abort_reason"] == "teaching_v2_after_review_fail"
    diagnostics = result["abort_diagnostics"]
    assert diagnostics["patch_rejected"] is True
    assert diagnostics["violations"] == [
        {
            "container": "learning_package",
            "error_type": "outside_allowlist",
            "loc": ["translations_by_paragraph_id"],
        }
    ]
    assert diagnostics["restored_fields"] == ["translations_by_paragraph_id"]
    # pre-image restore: the rogue patch never touched the containers
    assert (
        state["learning_package"]["translations_by_paragraph_id"]["u01"]
        == package_before["translations_by_paragraph_id"]["u01"]
    )
    # the directed field itself also stays at the pre-patch image
    assert state["learning_package"]["language_targets"] == package_before["language_targets"]


@pytest.mark.anyio
async def test_refinement_patch_broken_dto_is_restored_to_pre_image():
    state = _package_state()
    state["semantic_review_result"] = make_review_fail().model_dump()

    async def broken_refinement(**_kwargs):
        output = make_refinement().model_dump()
        # the patch replaces language_targets with an invalid shape
        output["refinement_patch"] = {"language_targets": [{"expression": "x"}]}
        return {
            "output": make_refinement().__class__.model_validate(output),
            "usage_metadata": make_usage("refinement"),
        }

    with patch(f"{WORKFLOW_MODULE}._run_teaching_refinement_llm_span", new=broken_refinement):
        result = await refinement_node(state)

    evidence = result["refinement_result"]
    assert evidence["rejection"]["reason"] == "patch_violation"
    # pre-image restore: the full original target list is back
    assert len(result["learning_package"]["language_targets"]) == 3
    assert result["learning_package"]["language_targets"][0]["expression"] == "substantive analysis"


@pytest.mark.anyio
async def test_refinement_field_unknown_fails_closed():
    state = _package_state()
    review = make_review_fail().model_dump()
    review["issues"] = [
        {
            "contract": "evidence_anchors",
            "field": "learning_package.nope_missing_field",
            "problem": "字段不存在。",
        }
    ]
    state["semantic_review_result"] = review

    result = await refinement_node(state)

    assert result["abort"] is True
    assert result["abort_reason"] == "refinement_field_unknown"
    assert result["abort_diagnostics"]["field"] == "learning_package.nope_missing_field"


@pytest.mark.anyio
async def test_frozen_derivation_field_aborts_before_refinement_llm():
    state = _package_state()
    review = make_review_fail("difficulty_fit").model_dump()
    review["issues"] = [
        {
            "contract": "difficulty_fit",
            "field": "blueprint.effective_difficulty",
            "problem": "declared level misfits the syntax.",
        }
    ]
    state["semantic_review_result"] = review

    async def should_not_run(**_kwargs):
        raise AssertionError("refinement LLM must not run for frozen derivation fields")

    with patch(f"{WORKFLOW_MODULE}._run_teaching_refinement_llm_span", new=should_not_run):
        result = await refinement_node(state)

    assert result["abort"] is True
    assert result["abort_reason"] == "frozen_derivation_field"
    assert result["abort_diagnostics"]["field"] == "blueprint.effective_difficulty"
    assert "lesson_v2" not in result


@pytest.mark.anyio
async def test_patch_changing_derivation_nested_field_is_rejected_and_restored():
    state = _package_state()
    state["semantic_review_result"] = make_review_fail().model_dump()
    original_ids = [
        target["paragraph_id"] for target in state["learning_package"]["language_targets"]
    ]

    async def rogue_paragraph_id(**_kwargs):
        output = make_refinement().model_dump()
        patched = output["refinement_patch"]["language_targets"]
        patched[0]["paragraph_id"] = "u03"
        output["refinement_patch"] = {"language_targets": patched}
        return {
            "output": make_refinement().__class__.model_validate(output),
            "usage_metadata": make_usage("refinement"),
        }

    with patch(f"{WORKFLOW_MODULE}._run_teaching_refinement_llm_span", new=rogue_paragraph_id):
        result = await refinement_node(state)

    assert result["abort"] is True
    assert result["abort_reason"] == "teaching_v2_after_review_fail"
    assert result["abort_diagnostics"]["patch_rejected"] is True
    rejection = result["refinement_result"]["rejection"]
    assert rejection["violations"][0]["error_type"] == "frozen_derivation_field"
    restored_ids = [
        target["paragraph_id"] for target in result["learning_package"]["language_targets"]
    ]
    assert restored_ids == original_ids


# ---------------------------------------------------------------------------
# Defense line 4: stop diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_translation_target_mismatch_aborts_with_diagnostics(monkeypatch):
    from app.schemas.internal.daily_lesson_v2 import (
        TranslationDraft,
        TranslationItemDraft,
    )

    async def rogue_translation(**_kwargs):
        output = TranslationDraft(
            translations=[
                # u01 and u03 supplied, required u02 missing, u99 invented
                TranslationItemDraft(paragraph_id="u01", translation="第一段译文。"),
                TranslationItemDraft(paragraph_id="u99", translation="多余的译文。"),
            ]
        )
        return {"output": output, "usage_metadata": make_usage("translation")}

    monkeypatch.setattr(f"{WORKFLOW_MODULE}._run_translation_llm_span", rogue_translation)
    state = _package_state()

    result = await translation_node(state)

    assert result["abort"] is True
    assert result["abort_reason"] == "translation_target_set_mismatch"
    assert "u02" in result["abort_diagnostics"]["missing_unit_ids"]
    assert "u99" in result["abort_diagnostics"]["extra_unit_ids"]


# ---------------------------------------------------------------------------
# Defense line 5: budget gate
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_budget_gate_stops_after_cap(monkeypatch):
    import app.services.daily_reader.workflow as workflow_module

    monkeypatch.setattr(workflow_module, "TEACHING_V2_MODEL_REQUESTS_MAX", 1)
    with v2_happy_path():
        final_state = await build_daily_reader_graph().ainvoke(graph_input_state())

    assert final_state.get("abort") is True
    assert final_state["abort_reason"] == "teaching_v2_budget_exceeded"
    diagnostics = final_state["abort_diagnostics"]
    assert diagnostics["caps"]["model_requests"] == 1
    # usage is still conserved on the budget stop
    assert diagnostics["aggregate"]["model_requests"] == 2


def test_budget_caps_derive_from_frozen_evals_batch():
    # evals P-4E frozen batch caps: 80 requests / 393216 output tokens / 20
    # logical calls for the 4-case batch -> per article 20 / 98304 / 5.
    assert TEACHING_V2_MODEL_REQUESTS_MAX == 80 // 4
    assert TEACHING_V2_OUTPUT_TOKENS_MAX == 393216 // 4


# ---------------------------------------------------------------------------
# Prompt registry fidelity (verbatim canonical contract sentences)
# ---------------------------------------------------------------------------


def test_registry_instructions_are_verbatim_prefix_of_canonical_prompts():
    article = {"title": "t", "source": "s", "reading_units": READING_UNITS}
    selected = READING_UNITS[:2]
    review_input = _package_state()

    mapping = {
        "daily_blueprint": build_blueprint_prompt(article),
        "daily_language_support": build_language_support_prompt(selected, "B1"),
        "daily_translation": build_translation_prompt(
            selected,
            [{"paragraph_id": "u01", "sentence": "x"}],
            "B1",
        ),
        "daily_semantic_review": build_semantic_review_prompt(
            review_input["original_text"],
            review_input["lesson_blueprint"],
            review_input["learning_package"],
            {"derived_translation_unit_ids": ["u01"], "teaching_contract_issues": []},
        ),
        "daily_teaching_refinement": build_refinement_prompt(
            make_review_fail().model_dump(),
            {"language_targets": review_input["learning_package"]["language_targets"]},
            {"failed_contracts": ["language_target_value"]},
        ),
    }
    for agent_name, canonical in mapping.items():
        registry_text = load_agent_instructions(agent_name)
        assert canonical.startswith(registry_text), (
            f"{agent_name}: registry instructions drifted from the evals "
            f"canonical prompt (expected verbatim prefix)"
        )


def test_registry_version_bumped_for_teaching_v2_prompts():
    from app.services.prompting.prompt_loader import get_prompt_version

    assert get_prompt_version() == "0.0.11"


def test_semantic_review_contracts_frozen():
    assert len(SEMANTIC_REVIEW_CONTRACTS) == 10
    assert SEMANTIC_REVIEW_CONTRACTS[0] == "source_fidelity"
    assert SEMANTIC_REVIEW_CONTRACTS[-1] == "reading_mission_neutrality"


def test_graph_topology_locks_out_v1_nodes():
    """Proof that v1/v2 gates never coexist on any commit of this graph:
    the compiled node set is exactly light_normalize + the five teaching
    stages + daily_projection; every v1 node name is absent."""
    from app.services.daily_reader import workflow as workflow_module

    graph = build_daily_reader_graph()
    nodes = set(graph.get_graph().nodes.keys()) - {"__start__", "__end__"}
    assert nodes == {
        "light_normalize",
        "blueprint",
        "language_support",
        "translation",
        "semantic_review",
        "refinement",
        "daily_projection",
    }
    source = inspect.getsource(workflow_module)
    for v1_node in (
        "highlight_by_paragraph_batches",
        "paragraph_guides_and_translations",
        "close_reading_takeaways",
        "quality_review_node",
        "_run_daily_review_llm_span",
        "_run_daily_refinement_llm_span",
    ):
        assert v1_node not in source, f"v1 node leaked into the v2 workflow: {v1_node}"
