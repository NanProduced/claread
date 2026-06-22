from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings
from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.database import connection as db_connection
from app.llm.registry import build_model_registry
from app.llm.routes import MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE
from app.services.reader_orchestration import grammar_worker as grammar_worker_module
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarAnchorSegmentContext,
    GrammarBundleWorkerService,
    GrammarExecutionError,
    GrammarJobContext,
    PydanticAIGrammarBundleExecutor,
)
from app.services.reader_orchestration.job_bootstrap import GrammarJobBootstrapService
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)


class _StubAgentResult:
    def __init__(self, output: object) -> None:
        self.output = output


class _ExecutorUnderTest(PydanticAIGrammarBundleExecutor):
    def __init__(self, output: object) -> None:
        self._output = output
        super().__init__(
            settings=Settings(
                reader_grammar_bundle_model_profile="reader_grammar_bundle"
            )
        )

    def _build_agent(self, *, model: object):  # type: ignore[override]
        return object()

    async def _run_agent(self, agent: object, prompt: str) -> _StubAgentResult:  # type: ignore[override]
        return _StubAgentResult(self._output)


@pytest.fixture
async def grammar_executor_env() -> asyncpg.Pool:
    schema_name = f"test_reader_grammar_executor_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def _build_context(
    *,
    source_text: str,
    anchor_segment_id: str = "s1",
    segment_type: str = "sentence",
) -> GrammarJobContext:
    return GrammarJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="grammar_bundle_unit_v1",
        source_language="en",
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=(
            GrammarAnchorSegmentContext(
                anchor_segment_id=anchor_segment_id,
                sentence_id=anchor_segment_id,
                segment_type=segment_type,
                unit_start_utf16=0,
                unit_end_utf16=utf16_code_unit_length(source_text),
                text_hash=compute_text_range_hash(source_text),
                text=source_text,
            ),
        ),
    )


def _patch_stub_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        grammar_worker_module,
        "build_model_for_route",
        lambda settings, route: (
            object(),
            SimpleNamespace(
                profile_name="reader-grammar-profile",
                provider="stub-provider",
                model_name="stub-model",
                api_key="",
            ),
        ),
    )
    monkeypatch.setattr(
        grammar_worker_module,
        "extract_run_usage",
        lambda result: {
            "aggregate": {
                "input_tokens": 14,
                "output_tokens": 11,
                "total_tokens": 25,
            }
        },
    )


async def _submit_grammar_article(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
):
    return await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "Not only did the team revise the plan, but they also clarified the "
            "timeline."
        ),
        title="Grammar Executor Slice",
        language="en",
    )


@pytest.mark.anyio
async def test_real_executor_resolves_offsets_hashes_and_typed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    source_text = (
        "Not only did the team revise the plan, but they also clarified the "
        "timeline."
    )
    context = _build_context(source_text=source_text)
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "grammar_notes": [
                {
                    "item_type": "grammar_note",
                    "spans": [
                        {
                            "anchor_segment_id": "s1",
                            "selected_text": "Not only",
                        },
                        {
                            "anchor_segment_id": "s1",
                            "selected_text": "but they also",
                        },
                    ],
                    "grammar_point": "paired focus construction",
                    "pattern": "not only ... but also",
                    "note": "前后两部分共同形成并列强调。",
                }
            ],
            "sentence_analyses": [
                {
                    "item_type": "sentence_analysis",
                    "anchor_segment_id": "s1",
                    "selected_text": source_text,
                    "label": "fronted emphasis with inversion",
                    "analysis": "前置否定结构触发助动词提前，后半句补充并列结果。",
                    "chunks": [
                        {
                            "label": "fronted cue",
                            "text": "Not only",
                        },
                        {
                            "label": "inverted clause",
                            "text": "did the team revise the plan",
                        },
                        {
                            "label": "paired result",
                            "text": "but they also clarified the timeline",
                        },
                    ],
                }
            ],
        }
    )

    result = await executor.generate(context)

    assert len(result.output.grammar_notes) == 1
    assert len(result.output.sentence_analyses) == 1
    grammar_note = result.output.grammar_notes[0]
    assert [span.anchor_segment_id for span in grammar_note.spans] == ["s1", "s1"]
    assert grammar_note.spans[0].start_offset == 0
    assert grammar_note.spans[0].end_offset == utf16_code_unit_length("Not only")
    assert grammar_note.spans[0].text_hash == compute_text_range_hash("Not only")
    assert grammar_note.spans[1].start_offset == utf16_code_unit_length(
        "Not only did the team revise the plan, "
    )
    assert grammar_note.spans[1].text_hash == compute_text_range_hash(
        "but they also"
    )
    sentence_analysis = result.output.sentence_analyses[0]
    assert sentence_analysis.anchor.anchor_segment_id == "s1"
    assert sentence_analysis.anchor.start_offset == 0
    assert sentence_analysis.anchor.end_offset == utf16_code_unit_length(source_text)
    assert [chunk.order for chunk in sentence_analysis.chunks] == [1, 2, 3]
    assert result.model_route == "reader_layer_grammar_bundle"
    assert result.model_profile == "reader-grammar-profile"
    assert result.model_provider == "stub-provider"
    assert result.model_name == "stub-model"
    assert result.diagnostics == {
        "candidate_grammar_note_count": 1,
        "candidate_sentence_analysis_count": 1,
        "resolved_grammar_note_count": 1,
        "resolved_sentence_analysis_count": 1,
        "skipped_item_count": 0,
        "skipped_items": [],
        "skipped_items_truncated_count": 0,
    }


@pytest.mark.anyio
async def test_real_executor_skips_ambiguous_and_missing_selected_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="We had had enough of the delays.")
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "grammar_notes": [
                {
                    "item_type": "grammar_note",
                    "spans": [
                        {
                            "anchor_segment_id": "s1",
                            "selected_text": "had",
                        }
                    ],
                    "grammar_point": "auxiliary repetition",
                    "pattern": None,
                    "note": "这里不应被错误锚定。",
                }
            ],
            "sentence_analyses": [
                {
                    "item_type": "sentence_analysis",
                    "anchor_segment_id": "s1",
                    "selected_text": "ghost clause",
                    "label": "missing clause",
                    "analysis": "不存在的片段不应通过。",
                    "chunks": [{"label": "ghost", "text": "ghost clause"}],
                }
            ],
        }
    )

    result = await executor.generate(context)

    assert result.output.grammar_notes == []
    assert result.output.sentence_analyses == []
    assert result.diagnostics is not None
    assert result.diagnostics["resolved_grammar_note_count"] == 0
    assert result.diagnostics["resolved_sentence_analysis_count"] == 0
    assert result.diagnostics["skipped_item_count"] == 2
    assert {
        item["reason_code"] for item in result.diagnostics["skipped_items"]
    } == {"selected_text_ambiguous", "selected_text_not_found"}


@pytest.mark.anyio
async def test_real_executor_invalid_model_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="The structure matters here.")
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "grammar_notes": [
                {
                    "item_type": "grammar_note",
                    "spans": [{"anchor_segment_id": "s1"}],
                    "grammar_point": "broken",
                    "pattern": None,
                    "note": "缺字段",
                }
            ],
            "sentence_analyses": [],
        }
    )

    with pytest.raises(GrammarExecutionError, match="invalid structured output") as exc_info:
        await executor.generate(context)

    assert exc_info.value.failure_class == "validation"
    assert exc_info.value.failure_code == "model_output_invalid"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_real_executor_skips_fallback_window_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(
        source_text="A fallback window should not become a grammar anchor.",
        segment_type="fallback_window",
    )
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "grammar_notes": [
                {
                    "item_type": "grammar_note",
                    "spans": [
                        {
                            "anchor_segment_id": "s1",
                            "selected_text": "fallback window",
                        }
                    ],
                    "grammar_point": "low quality boundary",
                    "pattern": None,
                    "note": "应被跳过。",
                }
            ],
            "sentence_analyses": [
                {
                    "item_type": "sentence_analysis",
                    "anchor_segment_id": "s1",
                    "selected_text": "A fallback window should not become a grammar anchor.",
                    "label": "fallback sentence",
                    "analysis": "应被跳过。",
                    "chunks": [{"label": "whole", "text": "fallback window"}],
                }
            ],
        }
    )

    result = await executor.generate(context)

    assert result.output.grammar_notes == []
    assert result.output.sentence_analyses == []
    assert result.diagnostics is not None
    assert result.diagnostics["skipped_item_count"] == 2
    assert {
        item["reason_code"] for item in result.diagnostics["skipped_items"]
    } == {"boundary_low_fallback_window"}


@pytest.mark.anyio
async def test_real_executor_accepts_empty_valid_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="This sentence does not need extra grammar help.")
    executor = _ExecutorUnderTest(
        {"schema_version": 1, "grammar_notes": [], "sentence_analyses": []}
    )

    result = await executor.generate(context)

    assert result.output.grammar_notes == []
    assert result.output.sentence_analyses == []
    assert result.diagnostics == {
        "candidate_grammar_note_count": 0,
        "candidate_sentence_analysis_count": 0,
        "resolved_grammar_note_count": 0,
        "resolved_sentence_analysis_count": 0,
        "skipped_item_count": 0,
        "skipped_items": [],
        "skipped_items_truncated_count": 0,
    }


@pytest.mark.anyio
async def test_real_executor_requires_explicit_grammar_profile() -> None:
    context = _build_context(source_text="The executor should fail closed.")
    executor = PydanticAIGrammarBundleExecutor(
        settings=Settings(reader_grammar_bundle_model_profile="")
    )

    with pytest.raises(
        GrammarExecutionError,
        match="grammar bundle executor is not configured",
    ) as exc_info:
        await executor.generate(context)

    assert exc_info.value.failure_class == "configuration"
    assert exc_info.value.failure_code == "grammar_bundle_executor_unconfigured"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_real_executor_candidate_output_publishes_layers_and_snapshot_projection(
    grammar_executor_env: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    user_id = await insert_user(grammar_executor_env)
    article = await _submit_grammar_article(grammar_executor_env, user_id=user_id)
    await GrammarJobBootstrapService(pool=grammar_executor_env).bootstrap_grammar_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    source_text = (
        "Not only did the team revise the plan, but they also clarified the "
        "timeline."
    )
    worker = GrammarBundleWorkerService(
        pool=grammar_executor_env,
        executor=_ExecutorUnderTest(
            {
                "schema_version": 1,
                "grammar_notes": [
                    {
                        "item_type": "grammar_note",
                        "spans": [
                            {
                                "anchor_segment_id": "s1",
                                "selected_text": "Not only",
                            },
                            {
                                "anchor_segment_id": "s1",
                                "selected_text": "but they also",
                            },
                        ],
                        "grammar_point": "paired focus construction",
                        "pattern": "not only ... but also",
                        "note": "前后两部分共同形成并列强调。",
                    }
                ],
                "sentence_analyses": [
                    {
                        "item_type": "sentence_analysis",
                        "anchor_segment_id": "s1",
                        "selected_text": source_text,
                        "label": "fronted emphasis with inversion",
                        "analysis": "前置否定结构触发助动词提前，后半句补充并列结果。",
                        "chunks": [
                            {"label": "fronted cue", "text": "Not only"},
                            {
                                "label": "inverted clause",
                                "text": "did the team revise the plan",
                            },
                            {
                                "label": "paired result",
                                "text": "but they also clarified the timeline",
                            },
                        ],
                    }
                ],
            }
        ),
    )

    result = await worker.process_next_grammar_job(
        lease_owner="grammar-real-executor",
        lease_duration=timedelta(seconds=30),
    )

    assert result is not None
    assert result.status == "succeeded"

    snapshot = await ArticleReadyPersistenceService(pool=grammar_executor_env).load_snapshot(
        record_id=article.record_id,
        user_id=user_id,
    )

    grammar_marked_leaves = [
        leaf
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_source_block"
        for anchor_node in child["children"]  # type: ignore[index]
        if isinstance(anchor_node, dict) and anchor_node.get("type") == "reader_anchor_segment"
        for leaf in anchor_node["children"]  # type: ignore[index]
        if isinstance(leaf, dict) and leaf.get("reader_grammar_note_marks")
    ]
    sentence_analysis_nodes = [
        child
        for unit_node in snapshot.value
        for child in unit_node["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_sentence_analysis"
    ]

    assert grammar_marked_leaves
    assert any(
        mark["item_type"] == "grammar_note"
        for leaf in grammar_marked_leaves
        for mark in leaf["reader_grammar_note_marks"]  # type: ignore[index]
    )
    assert len(sentence_analysis_nodes) == 1
    assert sentence_analysis_nodes[0]["owner"] == "system_ai"


def test_reader_grammar_bundle_route_uses_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        reader_grammar_bundle_model_profile="reader_grammar_bundle",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE] == (
        "reader_grammar_bundle"
    )


def test_reader_grammar_bundle_route_requires_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        reader_grammar_bundle_model_profile="",
    )

    registry = build_model_registry(settings)

    assert MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE not in registry.route_defaults
