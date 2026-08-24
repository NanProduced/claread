"""P-4E canonical teaching-prototype real-run harness tests (offline).

Every Agent call in this module runs through a FunctionModel double. No
real provider, judge, DB, Redis, FastAPI or network access happens here;
credentials are dummy values injected via monkeypatched environment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

RUNNER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_daily_reader_teaching_prototype.py"
)

OK_HOST = "https://dashscope.aliyuncs.com/compatible-mode/v1"
FORBIDDEN_HOST = "https://api.deepseek.com/v1"
FLASH_MODEL = "deepseek-v4-flash-0731"
PRO_MODEL = "deepseek-v4-pro-0813"
FLASH_PROFILE = "workflow-p4e-flash"
PRO_PROFILE = "workflow-p4e-pro"
TIER_PROFILES = {"flash": FLASH_PROFILE, "pro": PRO_PROFILE}
DUMMY_KEY_ENV = "P4E_TEST_DASHSCOPE_KEY"
DUMMY_KEY = "dummy-key-p4e-offline"

UNITS = [
    {"id": "u01", "text": "City officials opened the season with a short statement."},
    {"id": "u02", "text": "Analysts noted that the dispute had simply run its course."},
    {"id": "u03", "text": "Neither side wanted to make the first move before talks began."},
    {
        "id": "u04",
        "text": "By contrast, early negotiations collapsed although demand stayed strong.",
    },
]
SENTENCE = "early negotiations collapsed although demand stayed strong"
SENTENCE_ZH = "尽管需求强劲，早期谈判仍告破裂"
BOILER_FRAGMENT = "INTERNAL REVIEW NOTE REMOVE BEFORE PUBLISH"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_daily_reader_teaching_prototype_under_test", RUNNER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner():
    return _load_runner()


@pytest.fixture
def eval_settings(runner, monkeypatch, tmp_path):
    profiles = {
        "providers": {
            "dashscope_compat": {
                "adapter": "openai_compatible",
                "base_url": OK_HOST,
                "api_key": "",
                "api_key_env": DUMMY_KEY_ENV,
                "openai_profile": {
                    "openai_chat_thinking_field": "reasoning_content",
                    "openai_chat_send_back_thinking_parts": "field",
                    "openai_supports_tool_choice_required": False,
                },
            }
        },
        "models": {
            "p4e-flash": {
                "provider": "dashscope_compat",
                "model_name": FLASH_MODEL,
                "model_settings": {"temperature": 0.2},
                "openai_profile": {
                    "default_structured_output_mode": "prompted",
                    "supports_json_object_output": True,
                },
            },
            "p4e-pro": {
                "provider": "dashscope_compat",
                "model_name": PRO_MODEL,
                "model_settings": {"temperature": 0.2},
                "openai_profile": {
                    "default_structured_output_mode": "prompted",
                    "supports_json_object_output": True,
                },
            },
        },
        "profiles": {
            FLASH_PROFILE: {
                "model": "p4e-flash",
                "model_settings": {"extra_body": {"enable_thinking": False}},
            },
            PRO_PROFILE: {
                "model": "p4e-pro",
                "model_settings": {"extra_body": {"enable_thinking": False}},
            },
        },
    }
    presets = {
        "daily_reader": {
            "routes": {
                "daily_annotation": {"profile": FLASH_PROFILE},
                "daily_translation": {"profile": FLASH_PROFILE},
                "daily_analysis": {"profile": FLASH_PROFILE},
                "daily_review": {"profile": PRO_PROFILE},
            }
        }
    }
    profiles_path = tmp_path / "model-profiles.json"
    presets_path = tmp_path / "model-presets.json"
    profiles_path.write_text(json.dumps(profiles), encoding="utf-8")
    presets_path.write_text(json.dumps(presets), encoding="utf-8")
    monkeypatch.setenv(DUMMY_KEY_ENV, DUMMY_KEY)
    monkeypatch.delenv("MODEL_PROFILES_JSON", raising=False)
    monkeypatch.delenv("MODEL_PRESETS_JSON", raising=False)
    settings = runner.Settings(
        model_profiles_json=str(profiles_path),
        model_presets_json=str(presets_path),
    )
    selection = runner.build_eval_selection(settings, tier_profile_names=TIER_PROFILES)
    return settings, selection


# ---------------------------------------------------------------------------
# offline FunctionModel transport
# ---------------------------------------------------------------------------


class _Probe:
    def __init__(self) -> None:
        self.requests = 0
        self.constructions = 0
        self.captures: list[dict[str, Any]] = []

    def capture(self, info: Any) -> None:
        params = info.model_request_parameters
        instructions = getattr(params, "prompted_output_instructions", None)
        self.captures.append(
            {
                "output_mode": params.output_mode,
                "output_tools": len(params.output_tools),
                "function_tools": len(params.function_tools),
                "has_output_object": params.output_object is not None,
                "has_prompted_instructions": bool(instructions),
                "model_settings": dict(info.model_settings or {}),
                "requests_so_far": self.requests + 1,
            }
        )


class _Queue:
    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.last: Any = None

    def next_item(self) -> Any:
        if self.items:
            self.last = self.items.pop(0)
        return self.last


def _offline_transport_factory(queue: Any, probe: _Probe, *, select_queue=None):
    from pydantic_ai import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RunUsage

    def transport(config):
        probe.constructions += 1
        profile = _prompted_profile(config)

        def handler(messages: list[Any], info: Any) -> Any:
            active = select_queue(messages) if select_queue is not None else queue
            item = active.next_item()
            probe.requests += 1
            probe.capture(info)
            if isinstance(item, dict) and "__cancel__" in item:
                import asyncio

                raise asyncio.CancelledError()
            if isinstance(item, dict) and "__raise__" in item:
                import time

                time.sleep(0.02)
                raise RuntimeError(item["__raise__"])
            if isinstance(item, dict) and "__raw__" in item:
                payload = item["__raw__"]
                usage_in, usage_out = 11, 7
            else:
                usage = item.pop("_usage", None) or {}
                payload = json.dumps(item, ensure_ascii=False)
                usage_in = int(usage.get("input_tokens", 21))
                usage_out = int(usage.get("output_tokens", 13))
            return ModelResponse(
                parts=[TextPart(payload)],
                # requests is counted by the pydantic-ai graph itself
                usage=RunUsage(input_tokens=usage_in, output_tokens=usage_out),
            )

        return FunctionModel(handler, model_name=config.model_name, profile=profile)

    return transport


def _prompted_profile(config):
    """Harvest the production-chain profile for the resolved config."""
    from pydantic_ai.models import Model

    model = _load_runner().build_model_instance(config)
    assert isinstance(model, Model)
    return model.profile


def mini_case(case_id: str = "syn-p4e-001") -> dict[str, Any]:
    substantive = [unit["id"] for unit in UNITS]
    return {
        "case_id": case_id,
        "schema_version": 2,
        "dataset_id": "daily-reader-teaching-v2",
        "origin": {"frozen_real_article": True},
        "input": {
            "title": f"Synthetic P-4E harness case {case_id}",
            "source": "bbc",
            "source_url": "https://example.test/synthetic",
            "original_text": "\n".join(unit["text"] for unit in UNITS),
            "source_caption": "",
            "reading_units": UNITS,
        },
        "gold": {
            "expected_outcome": "cleaned_publish",
            "expected_difficulty": "B1",
            "article_type": "news_report",
            "dirty_fragments": [],
            "rejection_reasons": [],
            "expected_translation_coverage": {
                "policy": "all_units",
                "required_paragraph_ids": substantive,
                "allowed_paragraph_ids": substantive,
            },
        },
    }


def _checkpoint(skill: str, unit: str) -> dict[str, str | list[str]]:
    return {
        "skill": skill,
        "prompt": f"What does unit {unit} report?",
        "prompt_subject": "the officials",
        "reference_answer": f"Unit {unit} reports the season opening.",
        "reference_answer_subject": "the officials",
        "evidence_paragraph_ids": [unit],
        "answer_evidence_paragraph_ids": [unit],
    }


def blueprint_payload(article_type: str = "news_report") -> dict[str, Any]:
    task_kind = {
        "news_report": "retell",
        "opinion_commentary": "counter",
        "explainer": "explain",
        "narrative_profile": "rewrite",
    }[article_type]
    content = "fact_chain" if article_type == "news_report" else "original_stance"
    return {
        "_usage": {"input_tokens": 30, "output_tokens": 20},
        "article_type": article_type,
        "effective_difficulty": "B1",
        "reading_mission": "Read closely and retell the reported facts.",
        "reading_mission_stance": "neutral",
        "learning_objectives": ["Retell the fact chain."],
        "structure_map": [
            {"label": "Lead", "function": "introduce", "paragraph_ids": ["u01"]},
            {
                "label": "Body",
                "function": "develop",
                "paragraph_ids": ["u02", "u03", "u04"],
            },
        ],
        "selected_paragraph_ids": ["u01", "u02", "u03", "u04"],
        "comprehension_checkpoints": [
            _checkpoint("fact_location", "u02"),
            _checkpoint("main_idea", "u03"),
        ],
        "transfer_task": {
            "task_kind": task_kind,
            "content_requirement": content,
            "required_language_target_expressions": ["run its course"],
            "prompt": "Retell the reported sequence in your own words.",
            "scaffold": "Use run its course when you describe how it ended.",
            "reference_points": ["The dispute ended without a winner."],
        },
        "high_difficulty_unit_ids": ["u04"],
    }


def language_support_payload() -> dict[str, Any]:
    return {
        "_usage": {"input_tokens": 25, "output_tokens": 35},
        "language_targets": [
            {
                "expression": "run its course",
                "paragraph_id": "u02",
                "target_kind": "idiom",
                "teaching_purpose": "idiom in context",
                "meaning_zh": "自然走完全过程",
                "usage_note": "描述不可逆进程已经结束",
                "reusable_pattern": "sth has run its course",
            },
            {
                "expression": "make the first move",
                "paragraph_id": "u03",
                "target_kind": "phrase",
                "teaching_purpose": "negotiation collocation",
                "meaning_zh": "先采取行动",
                "usage_note": "常用于僵持局面",
                "reusable_pattern": "be reluctant to make the first move",
            },
            {
                "expression": "By contrast",
                "paragraph_id": "u04",
                "target_kind": "discourse_link",
                "teaching_purpose": "contrast cohesion",
                "meaning_zh": "相比之下",
                "usage_note": "句首对比衔接",
                "reusable_pattern": "By contrast, ...",
            },
        ],
        "sentence_maps": [
            {
                "sentence": SENTENCE,
                "paragraph_id": "u04",
                "translation": SENTENCE_ZH,
                "complexity_kind": "complex_syntax",
                "teaching_purpose": "concession clause relations",
            }
        ],
        "high_difficulty_unit_ids": ["u04"],
    }


def translation_payload() -> dict[str, Any]:
    translations = [
        {"paragraph_id": "u01", "translation": "官员们以简短声明开启了新赛季。"},
        {"paragraph_id": "u02", "translation": "分析人士指出，争端已自然终结。"},
        {"paragraph_id": "u03", "translation": "双方都不愿在谈判前先采取行动。"},
        {"paragraph_id": "u04", "translation": SENTENCE_ZH + "。"},
    ]
    return {
        "_usage": {"input_tokens": 40, "output_tokens": 50},
        "translations": translations,
    }


def review_results(failing: str | None = None) -> list[dict[str, Any]]:
    contracts = _load_runner().SEMANTIC_REVIEW_CONTRACTS
    return [
        {
            "contract": contract,
            "passed": contract != failing,
            "rationale": f"Substantive check for {contract}.",
        }
        for contract in contracts
    ]


def review_payload(verdict: str = "PASS", failing: str | None = None) -> dict[str, Any]:
    return {
        "_usage": {"input_tokens": 60, "output_tokens": 70},
        "verdict": verdict,
        "issues": []
        if verdict == "PASS"
        else [
            {
                "contract": failing,
                "field": "transfer_task.task_kind",
                "problem": "direction does not fit the article type",
            }
        ],
        "remaining_issues": [] if verdict == "PASS" else ["transfer direction"],
        "contract_results": review_results(failing),
        "reviewed_at_stage": "before_refinement",
        "refinement_requested": verdict == "FAIL",
    }


def refinement_payload(*, passed: bool = True) -> dict[str, Any]:
    failing = review_payload("FAIL", "transfer_mapping")["issues"][0]["contract"]
    return {
        "_usage": {"input_tokens": 20, "output_tokens": 25},
        "refinement_patch": {"transfer_task": {}},
        "rechecked_contract_results": [
            {
                "contract": failing,
                "passed": passed,
                "rationale": "Directed recheck after patch.",
            }
        ],
        "remaining_issues": [],
    }


def pass_queue() -> list[dict[str, Any]]:
    return [
        blueprint_payload(),
        language_support_payload(),
        translation_payload(),
        review_payload("PASS"),
    ]


def fail_queue() -> list[dict[str, Any]]:
    return [
        blueprint_payload(),
        language_support_payload(),
        translation_payload(),
        review_payload("FAIL", "transfer_mapping"),
        refinement_payload(passed=True),
    ]


def tree_hashes(root: Path) -> dict[str, str]:
    import hashlib

    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# 1-4: topology and call counts under FunctionModel
# ---------------------------------------------------------------------------


def test_normal_function_model_path_has_exactly_four_calls(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue = _Queue(pass_queue())
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    assert case["outcome"] == "completed"
    assert probe.requests == 4
    assert probe.constructions == 4
    stages = [entry["stage"] for entry in case["stage_ledger"]]
    assert stages == ["blueprint", "language_support", "translation", "semantic_review"]


def test_failed_review_triggers_refinement_with_exactly_five_calls(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue = _Queue(fail_queue())
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    assert case["outcome"] == "completed"
    assert case["artifact"]["run_meta"]["refinement_count"] == 1
    assert probe.requests == 5
    assert case["stage_ledger"][-1]["stage"] == "refinement"


def test_sixth_call_is_unreachable(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue = _Queue(fail_queue() + [{"forbidden": "sixth call"}])
    probe = _Probe()
    runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    assert probe.requests == 5
    assert queue.items == [{"forbidden": "sixth call"}]


def test_forbidden_route_is_blocked_by_topology_validation(runner):
    with pytest.raises(ValueError, match="forbidden route"):
        runner.validate_topology(
            (
                runner.StageSpec("blueprint", "daily_takeaways", "pro", 4096),
                runner.StageSpec("language_support", "daily_annotation", "flash", 4096),
                runner.StageSpec("translation", "daily_translation", "flash", 8192),
                runner.StageSpec("semantic_review", "daily_review", "pro", 4096),
                runner.StageSpec("refinement", "daily_review", "pro", 4096),
            )
        )


# ---------------------------------------------------------------------------
# 5: structural fail-closed (empty object / missing field / illegal enum)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"article_type": "news_report"},
        {**blueprint_payload(), "article_type": "poem"},
    ],
)
def test_structural_generation_failures_stop_the_case(runner, eval_settings, tmp_path, broken):
    settings, selection = eval_settings
    broken["_usage"] = {"input_tokens": 5, "output_tokens": 5}
    queue = _Queue([broken])
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    assert case["outcome"].startswith("stopped")
    assert case["stop_reason"]
    assert probe.requests == 4  # 1 initial + 3 output retries, then exhausted
    assert case["stage_ledger"][-1]["outcome"].startswith("error:")


# ---------------------------------------------------------------------------
# 6-7: Gold injection blocked with zero agent calls
# ---------------------------------------------------------------------------


def test_gold_object_injection_is_blocked_with_zero_agent_calls(runner):
    payload_article = {
        "title": "Synthetic P-4E harness case",
        "source": "bbc",
        "reading_units": UNITS,
        "gold": {"expected_difficulty": "C1"},
    }
    with pytest.raises(ValueError, match="forbidden generation key"):
        runner.build_blueprint_prompt(payload_article)


def test_gold_string_injection_is_blocked_before_agent_call(runner):
    smuggled = dict(mini_case())
    smuggled["input"]["title"] = "Leak expected_difficulty=B2 inside title"
    view = runner.generation_view(smuggled)
    article = {
        "title": view["title"],
        "source": view["source"],
        "reading_units": view["reading_units"],
    }
    prompt = runner.build_blueprint_prompt(article)
    with pytest.raises(runner.GenerationLeakError):
        runner.assert_prompt_clean(prompt)


# ---------------------------------------------------------------------------
# 9-10: route/profile/settings drift and structured-output channel
# ---------------------------------------------------------------------------


def test_route_drift_blocks_before_agent_construction(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    fallback_selection = runner.ModelSelection(
        preset=selection.preset,
        routes={
            **selection.routes,
            "daily_translation": runner.RouteModelSelection(
                profile=TIER_PROFILES["flash"],
                fallback_profiles=[TIER_PROFILES["pro"]],
            ),
        },
    )
    spec = runner.STAGE_TOPOLOGY[2]
    with pytest.raises(runner.StructuralCaseError, match="fallback"):
        runner.resolve_stage_runtime(settings, fallback_selection, spec)


def test_official_deepseek_host_blocks_before_agent_construction(runner, monkeypatch, tmp_path):
    profiles = {
        "providers": {
            "official_deepseek": {
                "adapter": "openai_compatible",
                "base_url": FORBIDDEN_HOST,
                "api_key_env": DUMMY_KEY_ENV,
                "openai_profile": {
                    "default_structured_output_mode": "prompted",
                    "supports_json_object_output": True,
                },
            }
        },
        "models": {
            "p4e-flash": {
                "provider": "official_deepseek",
                "model_name": FLASH_MODEL,
            }
        },
        "profiles": {FLASH_PROFILE: {"model": "p4e-flash"}},
    }
    profiles_path = tmp_path / "model-profiles.json"
    presets_path = tmp_path / "model-presets.json"
    profiles_path.write_text(json.dumps(profiles), encoding="utf-8")
    presets_path.write_text(json.dumps({"daily_reader": {"routes": {}}}), encoding="utf-8")
    monkeypatch.setenv(DUMMY_KEY_ENV, DUMMY_KEY)
    settings = runner.Settings(
        model_profiles_json=str(profiles_path),
        model_presets_json=str(presets_path),
    )
    selection = runner.ModelSelection(
        preset="daily_reader",
        routes={
            "daily_analysis": runner.RouteModelSelection(profile=FLASH_PROFILE),
        },
    )
    spec = runner.STAGE_TOPOLOGY[0]
    with pytest.raises(runner.StructuralCaseError, match="host"):
        runner.resolve_stage_runtime(settings, selection, spec)


def test_prompted_channel_contract_is_captured_from_agent_calls(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue = _Queue(pass_queue())
    probe = _Probe()
    runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    assert probe.captures, "FunctionModel must observe at least one request"
    for capture in probe.captures:
        assert capture["output_mode"] == "prompted"
        assert capture["output_tools"] == 0
        assert capture["function_tools"] == 0
        assert capture["has_output_object"] is True
        assert capture["has_prompted_instructions"] is True
        ms = capture["model_settings"]
        assert ms["max_tokens"] in (4096, 8192)
        assert ms["temperature"] == 0.2
        assert ms["timeout"] == 120.0
        assert ms["extra_body"]["enable_thinking"] is False


# ---------------------------------------------------------------------------
# 11-14: usage conservation and failure attribution
# ---------------------------------------------------------------------------


def test_success_usage_conserves_stage_to_case_to_batch(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(_Queue(pass_queue()), probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    stage_total = {
        key: sum(entry["usage"][key] for entry in case["stage_ledger"])
        for key in ("input_tokens", "output_tokens", "total_tokens", "model_requests", "tool_calls")
    }
    assert case["usage"]["aggregate"] == stage_total
    assert report["aggregate"] == stage_total
    expected_inputs = 30 + 25 + 40 + 60
    expected_outputs = 20 + 35 + 50 + 70
    assert stage_total["input_tokens"] == expected_inputs
    assert stage_total["output_tokens"] == expected_outputs
    assert stage_total["model_requests"] == 4


def test_retry_exhaustion_keeps_usage_and_attributes_failure(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue = _Queue(
        [
            blueprint_payload(),
            language_support_payload(),
            translation_payload(),
            {"__raw__": "{not json"},
        ]
    )
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    review_entry = case["stage_ledger"][-1]
    assert review_entry["stage"] == "semantic_review"
    assert review_entry["usage"]["model_requests"] == 4
    assert review_entry["usage"]["input_tokens"] >= 44
    assert case["outcome"].startswith("stopped")


def test_refinement_usage_conserves_into_aggregate(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(_Queue(fail_queue()), probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    total = {
        "input_tokens": 30 + 25 + 40 + 60 + 20,
        "output_tokens": 20 + 35 + 50 + 70 + 25,
    }
    assert case["usage"]["aggregate"]["input_tokens"] == total["input_tokens"]
    assert case["usage"]["aggregate"]["output_tokens"] == total["output_tokens"]
    assert report["aggregate"]["model_requests"] == 5


def test_provider_exception_records_no_forged_usage(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue = _Queue([{"__raise__": "connection dead"}])
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    entry = report["cases"][0]["stage_ledger"][0]
    assert entry["stage"] == "blueprint"
    assert entry["outcome"].startswith("error:")
    assert entry["usage"]["model_requests"] == 0
    assert entry["usage"]["input_tokens"] == 0
    assert entry["usage"]["output_tokens"] == 0


# ---------------------------------------------------------------------------
# 15-16: batch stop semantics
# ---------------------------------------------------------------------------


def test_quality_mismatch_continues_to_next_case(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    mismatched = [blueprint_payload("opinion_commentary")]
    mismatched[0]["transfer_task"]["task_kind"] = "counter"
    mismatched[0]["transfer_task"]["content_requirement"] = "original_stance"
    queue_a = _Queue(
        mismatched
        + [
            language_support_payload(),
            translation_payload(),
            review_payload("PASS"),
        ]
    )
    queue_b = _Queue(pass_queue())
    active_queue = {"index": 0}
    probe = _Probe()
    inner_a = _offline_transport_factory(queue_a, probe)
    inner_b = _offline_transport_factory(queue_b, probe)

    def transport(config):
        # constructions are strictly sequential: case A uses 4 stages,
        # then case B uses its own 4
        inner = inner_a if active_queue["index"] < 4 else inner_b
        active_queue["index"] += 1
        return inner(config)

    report = runner.run_batch(
        [mini_case(), mini_case("syn-p4e-002")],
        settings,
        selection,
        transport,
        tmp_path / "out",
    )
    outcomes = [case["outcome"] for case in report["cases"]]
    assert outcomes[0] == "quality_fail_continue"
    assert outcomes[1] == "completed"
    assert probe.requests == 8


def test_structural_failure_stops_remaining_cases(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    queue_a = _Queue([{}])
    queue_b = _Queue(pass_queue())
    probe = _Probe()

    def select_queue(messages: list[Any]) -> _Queue:
        rendered = str(messages)
        return queue_b if "syn-p4e-002" in rendered else queue_a

    report = runner.run_batch(
        [mini_case(), mini_case("syn-p4e-002")],
        settings,
        selection,
        _offline_transport_factory(None, probe, select_queue=select_queue),
        tmp_path / "out",
    )
    assert report["cases"][0]["outcome"].startswith("stopped")
    assert report["cases"][1]["outcome"] == "skipped_by_stop"
    assert probe.constructions == 1
    assert probe.requests == 4


def test_budget_breach_stops_batch_after_usage_posting(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    budget = runner.derive_budget(case_count=1)
    budget["output_tokens_max"] = 10
    probe = _Probe()
    with pytest.raises(Exception, match="budget"):
        runner.run_batch(
            [mini_case()],
            settings,
            selection,
            _offline_transport_factory(_Queue(pass_queue()), probe),
            tmp_path / "out",
            budget=budget,
        )


# ---------------------------------------------------------------------------
# 17-18: attempt idempotency
# ---------------------------------------------------------------------------


def test_attempt_markers_are_first_side_effects(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    out_dir = tmp_path / "out"
    probe = _Probe()
    base_transport = _offline_transport_factory(_Queue(pass_queue()), probe)

    def factory(_queue, _probe):
        def transport(config):
            assert (out_dir / "batch-attempt.marker.json").exists()
            assert (out_dir / "syn-p4e-001" / "attempt.marker.json").exists()
            return base_transport(config)

        return transport

    runner.run_batch(
        [mini_case()],
        settings,
        selection,
        factory(None, probe),
        out_dir,
    )
    assert probe.requests == 4


def test_second_run_in_same_directory_is_refused_without_calls(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    out_dir = tmp_path / "out"
    probe = _Probe()
    runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(_Queue(pass_queue()), probe),
        out_dir,
    )
    before = tree_hashes(out_dir)
    requests_before = probe.requests
    with pytest.raises(FileExistsError):
        runner.run_batch(
            [mini_case()],
            settings,
            selection,
            _offline_transport_factory(_Queue([]), probe),
            out_dir,
        )
    assert probe.requests == requests_before
    assert tree_hashes(out_dir) == before


# ---------------------------------------------------------------------------
# 19-20: P-2 schema compatibility and evaluation-lane-only gates
# ---------------------------------------------------------------------------


def test_final_artifact_is_directly_consumable_by_p2_schema(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    case = mini_case()
    probe = _Probe()
    report = runner.run_batch(
        [case],
        settings,
        selection,
        _offline_transport_factory(_Queue(pass_queue()), probe),
        tmp_path / "out",
    )
    artifact = report["cases"][0]["artifact"]
    for key in (
        "case_id",
        "lesson_blueprint",
        "learning_package",
        "source_assets",
        "run_meta",
        "usage",
    ):
        assert key in artifact
    assert runner.validate_artifact(case, artifact) == []
    gates = runner.run_hard_gates(case, artifact)
    assert gates["all_passed"] is True


def test_hard_gates_run_only_in_evaluation_lane(runner, eval_settings, tmp_path, monkeypatch):
    settings, selection = eval_settings
    probe = _Probe()
    events: list[str] = []
    real_gates = runner.run_hard_gates

    def gates_spy(case, artifact):
        events.append(f"gates:{probe.requests}")
        return real_gates(case, artifact)

    monkeypatch.setattr(runner, "run_hard_gates", gates_spy)
    base_transport = _offline_transport_factory(_Queue(pass_queue()), probe)

    def factory(_queue, _probe):
        def transport(config):
            events.append(f"call:{probe.requests}")
            return base_transport(config)

        return transport

    runner.run_batch(
        [mini_case()],
        settings,
        selection,
        factory(None, probe),
        tmp_path / "out",
    )
    gate_events = [event for event in events if event.startswith("gates")]
    assert len(gate_events) == 1
    assert events[-1].startswith("gates:")
    assert gate_events[0] == "gates:4"


def test_budget_derivation_matches_frozen_caps(runner):
    budget = runner.derive_budget(case_count=4)
    assert budget["workflow_runs_max"] == 4
    assert budget["logical_calls_max"] == 20
    assert budget["model_requests_max"] == 80
    assert budget["output_tokens_max"] == 393216
    assert budget["outer_retries"] == 0
    assert budget["sdk_retries"] == 0
    assert budget["judge_calls"] == 0
    assert budget["db_calls"] == 0
    assert budget["redis_calls"] == 0
    assert budget["fastapi_calls"] == 0


# ---------------------------------------------------------------------------
# authorization gate (dual flags)
# ---------------------------------------------------------------------------


def test_missing_dual_flags_refuse_real_run(runner, tmp_path, capsys):
    out_dir = tmp_path / "refused"
    assert runner.main(["--out-dir", str(out_dir)]) != 0
    assert not out_dir.exists()
    assert "REFUSED" in capsys.readouterr().err
    assert runner.main(["--out-dir", str(out_dir), "--real-run"]) != 0
    assert runner.main(["--out-dir", str(out_dir), "--enable-real-provider-calls"]) != 0
    assert not out_dir.exists()


def test_authorized_cli_requires_new_output_directory(runner, tmp_path):
    out_dir = tmp_path / "auth"
    out_dir.mkdir()
    code = runner.main(
        [
            "--out-dir",
            str(out_dir),
            "--real-run",
            "--enable-real-provider-calls",
        ]
    )
    assert code != 0


def test_dummy_credentials_only_reach_the_model_chain(runner, eval_settings):
    settings, selection = eval_settings
    config = runner.resolve_model_config(settings, "daily_annotation", selection)
    assert config is not None
    assert config.api_key == DUMMY_KEY
    assert config.base_url.startswith("https://")
    assert FORBIDDEN_HOST not in config.base_url


# ---------------------------------------------------------------------------
# P-4E-R: budget, usage, and evaluation-lane closure (RED first on b0335f0c)
# ---------------------------------------------------------------------------


def test_production_client_default_sdk_retries_are_two(runner, eval_settings):
    settings, selection = eval_settings
    config = runner.resolve_model_config(settings, "daily_annotation", selection)
    model = runner.build_model_instance(config)
    client = getattr(model, "client", None)
    assert client is not None and hasattr(client, "max_retries")
    assert client.max_retries == 2


def test_production_transport_forces_zero_sdk_retries(runner, eval_settings):
    settings, selection = eval_settings
    runtime = runner.resolve_stage_runtime(settings, selection, runner.STAGE_TOPOLOGY[1])
    model = runner.production_transport(runtime.config)
    assert model.client.max_retries == 0


def test_derived_budget_binds_http_attempts_to_requests(runner):
    budget = runner.derive_budget(case_count=4)
    assert budget["sdk_retries"] == 0
    assert budget["http_attempts_max"] == 80
    assert budget["http_attempts_max"] == budget["model_requests_max"]


def test_failed_case_usage_enters_batch_aggregate(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    probe = _Probe()
    queue = _Queue(
        [
            blueprint_payload(),
            language_support_payload(),
            translation_payload(),
            {"__raw__": "{not json"},
        ]
    )
    report = runner.run_batch(
        [mini_case(), mini_case("syn-p4e-002")],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    first = report["cases"][0]
    second = report["cases"][1]
    # 1+1+1 completed stages plus the semantic_review entry that exhausted
    # its 4 requests (1 initial + 3 output retries)
    case_requests = sum(e["usage"]["model_requests"] for e in first["stage_ledger"])
    assert case_requests == 7
    assert first["usage"]["aggregate"]["model_requests"] == 7
    assert report["aggregate"]["model_requests"] == 7
    assert second["outcome"] == "skipped_by_stop"
    assert second["usage"]["aggregate"] == {}
    assert probe.requests == 7  # no double counting


def _boilerplate_case() -> dict[str, Any]:
    case = mini_case()
    case["gold"]["dirty_fragments"] = [BOILER_FRAGMENT]
    return case


def _blueprint_with_boilerplate() -> dict[str, Any]:
    payload = blueprint_payload()
    payload["transfer_task"]["prompt"] = f"Retell the sequence. {BOILER_FRAGMENT}"
    return payload


def test_gold_gate_fail_after_pass_review_is_quality_fail_continue(
    runner, eval_settings, tmp_path, monkeypatch
):
    settings, selection = eval_settings
    replays: list[dict[str, Any]] = []
    real_merge = runner.build_refinement_evidence

    def merge_spy(**kwargs):
        replays.append(kwargs["hard_gate_replay"])
        return real_merge(**kwargs)

    monkeypatch.setattr(runner, "build_refinement_evidence", merge_spy)
    queue = _Queue(
        [
            _blueprint_with_boilerplate(),
            language_support_payload(),
            translation_payload(),
            review_payload("FAIL", "transfer_mapping"),
            refinement_payload(passed=True),
        ]
    )
    qb = _Queue(pass_queue())
    probe = _Probe()
    inner_a = _offline_transport_factory(queue, probe)
    inner_b = _offline_transport_factory(qb, probe)
    state = {"n": 0}

    def transport(config):
        inner = inner_a if state["n"] < 5 else inner_b
        state["n"] += 1
        return inner(config)

    report = runner.run_batch(
        [_boilerplate_case(), mini_case("syn-p4e-002")],
        settings,
        selection,
        transport,
        tmp_path / "out",
    )
    outcomes = [case["outcome"] for case in report["cases"]]
    assert outcomes[0] == "quality_fail_continue"
    assert outcomes[1] == "completed"
    gold_gates = runner.run_hard_gates(_boilerplate_case(), report["cases"][0]["artifact"])
    assert gold_gates["all_passed"] is False
    assert replays and replays[0]["all_passed"] is True


@pytest.mark.parametrize(
    ("mutation", "reason_part"),
    [
        ("missing", "missing"),
        ("extra", "extra"),
        ("duplicate", "duplicate"),
    ],
)
def test_translation_target_set_mismatch_is_structural(
    runner, eval_settings, tmp_path, mutation, reason_part
):
    settings, selection = eval_settings
    translation = translation_payload()
    if mutation == "missing":
        translation["translations"] = translation["translations"][:-1]
    elif mutation == "extra":
        translation["translations"].append({"paragraph_id": "u09", "translation": "多余译文。"})
    else:
        translation["translations"].append(dict(translation["translations"][1]))
    queue = _Queue(
        [
            blueprint_payload(),
            language_support_payload(),
            translation,
            review_payload("PASS"),
        ]
    )
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    stages = [entry["stage"] for entry in case["stage_ledger"]]
    assert case["outcome"].startswith("stopped")
    assert reason_part in case["stop_reason"]
    assert stages[-1] == "translation"
    assert "semantic_review" not in stages
    assert probe.requests == 3


def test_budget_stop_checks_each_stage_and_persists_report(runner, eval_settings, tmp_path):
    import json as jsonlib

    settings, selection = eval_settings
    budget = runner.derive_budget(case_count=2)
    budget["model_requests_max"] = 2
    out_dir = tmp_path / "out"
    probe = _Probe()
    with pytest.raises(runner.BudgetBreached) as excinfo:
        runner.run_batch(
            [mini_case(), mini_case("syn-p4e-002")],
            settings,
            selection,
            _offline_transport_factory(_Queue(pass_queue()), probe),
            out_dir,
            budget=budget,
        )
    assert "budget_exceeded" in str(excinfo.value)
    report_path = out_dir / "batch-report.json"
    assert report_path.is_file()
    persisted = jsonlib.loads(report_path.read_text(encoding="utf-8"))
    first = persisted["cases"][0]
    second = persisted["cases"][1]
    assert first["outcome"] == "stopped:budget_exceeded"
    # posting happens before the check, so the breaching stage is retained
    assert [entry["stage"] for entry in first["stage_ledger"]] == [
        "blueprint",
        "language_support",
        "translation",
    ]
    assert first["stage_ledger"][-1]["usage"]["model_requests"] == 1
    assert first["usage"]["aggregate"]["model_requests"] == 3
    assert persisted["aggregate"]["model_requests"] == 3
    assert persisted["aggregate"]["model_requests"] > budget["model_requests_max"]
    assert second["outcome"] == "skipped_by_stop"
    assert persisted["budget"]["model_requests_max"] == 2
    assert "budget_exceeded" in persisted["stop_reason"]
    assert probe.constructions == 3
    with pytest.raises(FileExistsError):
        runner.run_batch(
            [mini_case()],
            settings,
            selection,
            _offline_transport_factory(_Queue([]), _Probe()),
            out_dir,
            budget=budget,
        )


def test_cancelled_error_propagates_unwrapped(runner, eval_settings, tmp_path):
    import asyncio

    settings, selection = eval_settings
    probe = _Probe()
    with pytest.raises(asyncio.CancelledError):
        runner.run_batch(
            [mini_case()],
            settings,
            selection,
            _offline_transport_factory(_Queue([{"__cancel__": True}]), probe),
            tmp_path / "out",
        )
    assert probe.requests == 1


def test_provider_failure_latency_is_measured(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    probe = _Probe()
    report = runner.run_batch(
        [mini_case()],
        settings,
        selection,
        _offline_transport_factory(_Queue([{"__raise__": "connection dead"}]), probe),
        tmp_path / "out",
    )
    entry = report["cases"][0]["stage_ledger"][0]
    assert entry["outcome"].startswith("error:")
    assert isinstance(entry["latency_ms"], int)
    assert entry["latency_ms"] >= 10


def test_gold_identity_mismatch_is_a_direct_comparison(runner):
    case = mini_case()
    artifact = {
        "lesson_blueprint": {
            "article_type": "opinion_commentary",
            "effective_difficulty": "B1",
        }
    }
    assert runner.gold_identity_mismatch(case, artifact) is True
    artifact["lesson_blueprint"]["effective_difficulty"] = "C1"
    assert runner.gold_identity_mismatch(case, artifact) is True
    artifact["lesson_blueprint"].update(article_type="news_report", effective_difficulty="B1")
    assert runner.gold_identity_mismatch(case, artifact) is False


def test_structural_errors_ignore_gold_identity_fields(runner, eval_settings, tmp_path):
    settings, selection = eval_settings
    source_case = mini_case()
    mismatched = [blueprint_payload("opinion_commentary")]
    mismatched[0]["transfer_task"]["task_kind"] = "counter"
    mismatched[0]["transfer_task"]["content_requirement"] = "original_stance"
    queue = _Queue(
        mismatched
        + [
            language_support_payload(),
            translation_payload(),
            review_payload("PASS"),
        ]
    )
    probe = _Probe()
    report = runner.run_batch(
        [source_case],
        settings,
        selection,
        _offline_transport_factory(queue, probe),
        tmp_path / "out",
    )
    case = report["cases"][0]
    artifact = case["artifact"]
    assert runner.gold_identity_mismatch(source_case, artifact) is True
    assert runner.artifact_structural_errors(source_case, artifact) == []
    broken = json.loads(json.dumps(artifact))
    broken["learning_package"]["comprehension_checkpoints"][0]["skill"] = "vibes"
    assert runner.artifact_structural_errors(source_case, broken) != []
