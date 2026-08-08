"""Bootstrap topology + freeze/reload tests for automatic layer policy.

No real LLM. Uses fixtures and (when available) real PostgreSQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.database.json_compat import ensure_json_object, jsonb_param
from app.schemas.reader_documents import StableDocumentBlock
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    AutomaticLayerPolicy,
    SemanticFenceConstructionError,
    filter_units_for_automatic_layer,
)
from app.services.reader_orchestration.base_builder import (
    build_reading_base_from_canonical_text,
)
from app.services.reader_orchestration.input_document_normalizer import (
    normalize_input_document,
)
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityRequest,
)
from app.services.reader_orchestration.job_bootstrap import (
    TranslationJobBootstrapService,
)
from app.services.reader_orchestration.semantic_classifier import (
    SEMANTIC_CONTRACT_V1,
    annotate_blocks_with_semantic,
)
from app.services.reader_orchestration.stable_annotation_analysis import (
    StableBlockAnnotation,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

REFERENCES_MD = """# Intro

This paragraph explains the main finding of the study in ordinary prose with
enough English words to pass the minimum learning-length suitability gate for
stable document freeze during automated fixture tests in continuous integration.

The second prose paragraph continues the background narrative so that ordinary
body units remain eligible for translation, vocabulary, grammar note and sentence
analysis automatic layers exactly as they were before the semantic policy change.

## References

[1] Smith, J. Example paper on pilots. Journal of Trials, 2020.

[2] Jones, A. Another related work. Conf Proc, 2021.

```python
def skip_me():
    return 1
```

| Name | Score |
| :--- | :---: |
| North | 42 |

See [only this link](https://example.com/only)
"""

PROSE_GOLDEN_MD = """# Field Notes

The research group compared three regional pilots and recorded every
measured outcome before drafting the summary for the public review session
that will be held next month with community stakeholders and external reviewers.

- First list item covers the northern pilot results carefully with enough detail.
- Second list item explains southern measurements in plain English for readers.

The closing paragraph explains how the committee weighed the combined evidence
and why the final recommendation remains stable for ordinary prose learning units.
"""


def test_normalizer_writes_contract_on_all_blocks() -> None:
    doc = normalize_input_document(
        InputSuitabilityRequest(
            text=REFERENCES_MD,
            source_type="pasted_text",
        )
    )
    assert doc.blocks
    for block in doc.blocks:
        semantic = (block.payload_json or {}).get("semantic")
        assert isinstance(semantic, dict)
        assert semantic.get("contract_version") == SEMANTIC_CONTRACT_V1


def test_base_builder_materializes_policy_for_matched_annotation() -> None:
    raw_blocks = [
        StableDocumentBlock(
            block_id="b1",
            order_index=0,
            block_type="paragraph",
            text_content="Intro prose here.",
            payload_json={},
        ),
        StableDocumentBlock(
            block_id="b2",
            order_index=1,
            block_type="heading",
            text_content="References",
            payload_json={"level": 2},
        ),
        StableDocumentBlock(
            block_id="b3",
            order_index=2,
            block_type="paragraph",
            text_content="[1] Cite me.",
            payload_json={},
        ),
        StableDocumentBlock(
            block_id="b4",
            order_index=3,
            block_type="code_block",
            text_content="code line",
            payload_json={"language": "python"},
        ),
    ]
    annotated = annotate_blocks_with_semantic(raw_blocks)
    assert annotated[2].payload_json["semantic"]["content_role"] == "citation_reference"
    assert annotated[3].payload_json["semantic"]["content_role"] is None

    result = build_reading_base_from_canonical_text(
        reading_record_id=str(uuid4()),
        base_id=str(uuid4()),
        canonical_text="Intro prose here.\n\n[1] Cite me.\n\ncode line",
        stable_block_annotations=(
            StableBlockAnnotation(
                block_id="b1",
                block_type="paragraph",
                parent_block_id=None,
                payload_json=dict(annotated[0].payload_json),
                start_utf16=0,
                end_utf16=len("Intro prose here."),
            ),
        ),
    )
    prose_units = [u for u in result.units if u.text.startswith("Intro")]
    assert prose_units
    unit = prose_units[0]
    assert unit.semantic_contract_version == SEMANTIC_CONTRACT_V1
    assert unit.automatic_layer_policy is not None
    assert unit.automatic_layer_policy["translation"] is True


def test_compact_grouped_grammar_window_filter_semantics() -> None:
    """Simulate compact / grouped / grammar-window pre-planning filter on mixed units."""
    units = [
        {
            "unit_id": "prose",
            "order_index": 1,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": "prose",
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                }
            },
        },
        {
            "unit_id": "code",
            "order_index": 2,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": None,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            },
        },
        {
            "unit_id": "table",
            "order_index": 3,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": None,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            },
        },
        {
            "unit_id": "cite",
            "order_index": 4,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": "citation_reference",
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": {
                        "translation": True,
                        "vocabulary": False,
                        "grammar_note": False,
                        "sentence_analysis": False,
                    },
                }
            },
        },
        {
            "unit_id": "heading",
            "order_index": 5,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": None,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": {
                        "translation": True,
                        "vocabulary": False,
                        "grammar_note": False,
                        "sentence_analysis": False,
                    },
                }
            },
        },
    ]
    t = filter_units_for_automatic_layer(units, "translation", mode="enforce")
    assert {u["unit_id"] for u in t} == {"prose", "cite", "heading"}
    v = filter_units_for_automatic_layer(units, "vocabulary", mode="enforce")
    assert {u["unit_id"] for u in v} == {"prose"}  # heading vocabulary off
    g = filter_units_for_automatic_layer(units, "grammar_note", mode="enforce")
    assert {u["unit_id"] for u in g} == {"prose"}


def test_prose_golden_no_false_citation_roles() -> None:
    doc = normalize_input_document(
        InputSuitabilityRequest(text=PROSE_GOLDEN_MD, source_type="pasted_text")
    )
    for block in doc.blocks:
        semantic = block.payload_json["semantic"]
        role = semantic.get("content_role")
        assert role not in {"citation_reference", "link_only"}


def test_version_mismatch_fence_unit() -> None:
    from app.services.reader_orchestration.automatic_layer_policy import (
        SemanticPolicyVersionMismatch,
        validate_job_unit_semantic_fence,
    )

    with pytest.raises(SemanticPolicyVersionMismatch):
        validate_job_unit_semantic_fence(
            job_input={
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            },
            unit_metadata_list=[
                {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "resolver_version": "automatic_layer_policy_v99",
                        "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                    }
                }
            ],
        )


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


@pytest.fixture
async def semantic_db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_sem_policy_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    pool = await _make_pool(schema_name)
    try:
        yield pool
    finally:
        await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


@pytest.mark.asyncio
async def test_freeze_unit_metadata_reload_equivalence(
    semantic_db_env: asyncpg.Pool,
) -> None:
    """Real Postgres: freeze → unit metadata → snapshot reload policy fields."""
    pool = semantic_db_env
    user_id = await _insert_user(pool)
    service = StableReadyInputApplicationService(pool=pool)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="markdown_file",
        filename="references-sample.md",
        text=REFERENCES_MD,
        language="en",
    )
    record_id = result.reading_record_id

    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, unit_type, metadata_json
            FROM reading_units
            WHERE reading_record_id = $1
            ORDER BY order_index
            """,
            record_id,
        )
        block_rows = await conn.fetch(
            """
            SELECT b.block_type, b.payload_json, b.text_content
            FROM stable_reading_documents d
            JOIN stable_document_blocks b ON b.stable_document_id = d.id
            WHERE d.reading_record_id = $1 AND d.status = 'active'
            ORDER BY b.order_index
            """,
            record_id,
        )

    assert unit_rows, "expected reading_units"
    assert block_rows, "expected stable blocks"

    for brow in block_rows:
        payload = ensure_json_object(brow["payload_json"])
        semantic = payload.get("semantic")
        assert isinstance(semantic, dict), f"missing semantic on {brow['block_type']}"
        assert semantic.get("contract_version") == SEMANTIC_CONTRACT_V1

    policies: list[dict[str, Any]] = []
    for urow in unit_rows:
        meta = ensure_json_object(urow["metadata_json"])
        semantic = meta.get("semantic")
        if not isinstance(semantic, dict):
            continue
        assert semantic.get("contract_version") == SEMANTIC_CONTRACT_V1
        assert semantic.get("resolver_version") == AUTOMATIC_LAYER_POLICY_RESOLVER_V1
        policy = semantic.get("automatic_layer_policy")
        assert isinstance(policy, dict)
        policies.append(policy)

    assert policies, "expected at least one unit with semantic policy"

    all_off = [
        p
        for p in policies
        if p.get("translation") is False
        and p.get("vocabulary") is False
        and p.get("grammar_note") is False
        and p.get("sentence_analysis") is False
    ]
    assert all_off, "expected code/table/link units with all-off automatic policy"

    cite_policies = [
        p
        for p in policies
        if p.get("translation") is True
        and p.get("vocabulary") is False
        and p.get("grammar_note") is False
    ]
    assert cite_policies, "expected citation_reference T-only policies"

    unit_maps = [
        {
            "unit_id": str(r["unit_id"]),
            "order_index": i,
            "metadata_json": ensure_json_object(r["metadata_json"]),
        }
        for i, r in enumerate(unit_rows)
    ]
    vocab_targets = filter_units_for_automatic_layer(unit_maps, "vocabulary")
    for t in vocab_targets:
        pol = (t["metadata_json"].get("semantic") or {}).get(
            "automatic_layer_policy"
        ) or {}
        assert pol.get("vocabulary") is not False

    # Reload path must project the same automatic policy DTO fields.
    ready = ArticleReadyPersistenceService(pool=pool)
    reloaded = await ready.load_snapshot(record_id=record_id, user_id=user_id)

    source_blocks: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "reader_source_block":
                source_blocks.append(node)
            for child in node.get("children") or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(reloaded.value)
    assert source_blocks
    with_policy = [b for b in source_blocks if "automaticLayerPolicy" in b]
    assert with_policy
    for b in with_policy:
        pol = b["automaticLayerPolicy"]
        if pol is not None:
            assert set(pol.keys()) == {
                "translation",
                "vocabulary",
                "grammarNote",
                "sentenceAnalysis",
            }


@pytest.mark.asyncio
async def test_automatic_bootstrap_mixed_contract_fails_closed_no_job(
    semantic_db_env: asyncpg.Pool,
) -> None:
    """Real automatic bootstrap: mixed contract versions across untranslated
    units → shared fence builder raises ``SemanticFenceConstructionError``
    before any ``reader_jobs`` / ``reader_runs`` row is persisted.

    The automatic bootstrap loads all untranslated translation-allowed units
    and builds a single semantic fence from them via the shared builder. When
    the units carry mixed contract versions, the builder raises and the
    surrounding transaction rolls back — no half-legitimate job survives to
    be worker-superseded.
    """
    pool = semantic_db_env
    user_id = await _insert_user(pool)
    service = StableReadyInputApplicationService(pool=pool)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="markdown_file",
        filename="references-sample.md",
        text=REFERENCES_MD,
        language="en",
    )
    record_id = result.reading_record_id

    # Pick two translation-allowed units and tamper one to a foreign contract.
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, metadata_json
            FROM reading_units
            WHERE reading_record_id = $1
            ORDER BY order_index
            """,
            record_id,
        )
    translation_units: list[dict[str, Any]] = []
    for r in unit_rows:
        meta = ensure_json_object(r["metadata_json"])
        semantic = meta.get("semantic") or {}
        policy = semantic.get("automatic_layer_policy") or {}
        if policy.get("translation") is True:
            translation_units.append({"unit_id": str(r["unit_id"]), "meta": meta})
    assert len(translation_units) >= 2, (
        "test requires >=2 translation-allowed units to produce a mix"
    )

    target_unit_id = translation_units[0]["unit_id"]
    tampered_meta = dict(translation_units[0]["meta"])
    tampered_semantic = dict(tampered_meta.get("semantic") or {})
    tampered_semantic["contract_version"] = "semantic_contract_v999_bogus"
    tampered_meta["semantic"] = tampered_semantic

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_units SET metadata_json = $1::jsonb "
            "WHERE reading_record_id = $2 AND unit_id = $3",
            jsonb_param(tampered_meta),
            record_id,
            target_unit_id,
        )

    # Snapshot baseline counts before bootstrap; freeze may have created
    # unrelated jobs (e.g. semantic outline). The fail-closed contract is
    # that bootstrap must NOT add any new translation job/run.
    async with pool.acquire() as conn:
        pre_jobs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
        pre_runs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_runs WHERE reading_record_id = $1",
            record_id,
        )

    bootstrap = TranslationJobBootstrapService(pool=pool)
    with pytest.raises(SemanticFenceConstructionError):
        await bootstrap.bootstrap_translation_run(
            record_id=record_id,
            user_id=user_id,
        )

    # Fail-closed: no new reader_jobs / reader_runs row survives the rollback.
    async with pool.acquire() as conn:
        post_jobs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
        post_runs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_runs WHERE reading_record_id = $1",
            record_id,
        )
    assert post_jobs == pre_jobs, (
        f"expected zero new reader_jobs after mixed-contract fail-closed, "
        f"delta={post_jobs - pre_jobs}"
    )
    assert post_runs == pre_runs, (
        f"expected zero new reader_runs after mixed-contract fail-closed, "
        f"delta={post_runs - pre_runs}"
    )


@pytest.mark.asyncio
async def test_automatic_bootstrap_mixed_resolver_fails_closed_no_job(
    semantic_db_env: asyncpg.Pool,
) -> None:
    """Real automatic bootstrap: mixed resolver versions across untranslated
    units → shared fence builder raises ``SemanticFenceConstructionError``
    before any ``reader_jobs`` / ``reader_runs`` row is persisted.
    """
    pool = semantic_db_env
    user_id = await _insert_user(pool)
    service = StableReadyInputApplicationService(pool=pool)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="markdown_file",
        filename="references-sample.md",
        text=REFERENCES_MD,
        language="en",
    )
    record_id = result.reading_record_id

    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, metadata_json
            FROM reading_units
            WHERE reading_record_id = $1
            ORDER BY order_index
            """,
            record_id,
        )
    translation_units: list[dict[str, Any]] = []
    for r in unit_rows:
        meta = ensure_json_object(r["metadata_json"])
        semantic = meta.get("semantic") or {}
        policy = semantic.get("automatic_layer_policy") or {}
        if policy.get("translation") is True:
            translation_units.append({"unit_id": str(r["unit_id"]), "meta": meta})
    assert len(translation_units) >= 2

    target_unit_id = translation_units[0]["unit_id"]
    tampered_meta = dict(translation_units[0]["meta"])
    tampered_semantic = dict(tampered_meta.get("semantic") or {})
    tampered_semantic["resolver_version"] = "automatic_layer_policy_v999_bogus"
    tampered_meta["semantic"] = tampered_semantic

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_units SET metadata_json = $1::jsonb "
            "WHERE reading_record_id = $2 AND unit_id = $3",
            jsonb_param(tampered_meta),
            record_id,
            target_unit_id,
        )

    async with pool.acquire() as conn:
        pre_jobs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
        pre_runs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_runs WHERE reading_record_id = $1",
            record_id,
        )

    bootstrap = TranslationJobBootstrapService(pool=pool)
    with pytest.raises(SemanticFenceConstructionError):
        await bootstrap.bootstrap_translation_run(
            record_id=record_id,
            user_id=user_id,
        )

    async with pool.acquire() as conn:
        post_jobs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_jobs WHERE reading_record_id = $1",
            record_id,
        )
        post_runs = await conn.fetchval(
            "SELECT count(*)::int FROM reader_runs WHERE reading_record_id = $1",
            record_id,
        )
    assert post_jobs == pre_jobs
    assert post_runs == pre_runs
