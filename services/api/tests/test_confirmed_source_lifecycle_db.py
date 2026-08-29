"""L2 阶段 2 Gate — Confirmed Source 生命周期（真实 PostgreSQL）。

封住的合同（docs/architecture/reader-orchestration.md — Confirmed Source 生命周期）：

1. 文本路径（candidate 创建 + stable-ready）：original_inputs.source_text
   恒 NULL；confirmed_source_documents 是该 generation 唯一完整正文
   （“Identity / Truth ownership” 验证 SQL：oi JOIN cs 中 oi.source_text
   非空计数为 0）；candidate source_refs_json 含三 key（同节）。
2. PUT：stale revision 不覆盖（409 stale_source_revision）；同 hash
   幂等 no-op（revision 不变、不 supersede）；编辑后版本化
   candidate supersede（仅一份 ready candidate）。
3. confirm：编辑后 confirm 使用修改后原文；frozen source 与 stable
   blocks/canonical 同 revision/hash（插入点 A/B）；重复 confirm 幂等
   （recovery 第 6 条）；candidate 引用过期 source revision →
   stale_candidate_revision fail closed；legacy candidate（无 source
   行/引用）走旧逻辑向后兼容。
4. artifact 路径：pipeline-status 增 has_confirmed_source（Q5）。

测试模式复用 ``test_table_code_metadata_reload.py``：隔离
PostgreSQL schema + 生产服务全链路，不做 fake connection。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.artifact_input_status_query_service import (
    ArtifactPipelineStatusQueryService,
)
from app.services.reader_orchestration.base_builder import (
    AUTO_SEGMENTER_POLICY,
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.candidate_document_confirm_application_service import (
    CandidateDocumentConfirmApplicationService,
    StaleCandidateRevisionApplicationError,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    CandidateDocumentCreationService,
    _build_candidate_blocks,
)
from app.services.reader_orchestration.confirmed_source_application_service import (
    ConfirmedSourceApplicationService,
    ConfirmedSourceConflictError,
)
from app.services.reader_orchestration.confirmed_source_repository import (
    confirmed_source_content_sha256,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationService,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL,
    DATABASE_URL,
)

# source_artifacts 已并入单一基线 0001_initial.sql，直接用 BASELINE_SQL。
SCHEMA_SQL = BASELINE_SQL

# candidate 触发器：footnote 是合法 content_check 信号（结构语义需要人工
# 确认）。G2a-A（O-1）后图片走 typed representation 直接 freeze，不再
# 触发 candidate。
_CANDIDATE_MD = """## Quarterly Review Notes

The committee reviewed the regional pilot results and recorded every
measured outcome before drafting the summary for the public review
session scheduled next month in the main hall near the river.[^1]

[^1]: The archival note keeps the additional context attached.

The closing paragraph explains how the committee weighed the evidence
and why the combined record supports the final recommendation for all
readers of the public summary document.
"""

_CANDIDATE_MD_EDITED = """## Quarterly Review Notes ( Revised )

The committee reviewed the regional pilot results and recorded every
measured outcome before drafting the summary for the public review
session scheduled next month in the main hall near the river.[^1]

[^1]: The archival note keeps the additional context attached.

The closing paragraph explains how the committee weighed the edited
evidence and why the combined record supports the revised final
recommendation for all readers of the public summary document.
"""

_STABLE_MD = """## Morning Reading Notes

The committee reviewed the proposal and agreed that the appendix should
remain available to every participant before the vote takes place next
month in the main hall near the river district office building.

A second paragraph keeps the document comfortably above the minimum
word count so the gate sees ordinary English prose with a heading and
no high-impact structure risks anywhere in the body of the note.
"""


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


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
async def db_env() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_conf_src_{uuid4().hex}"
    admin_conn: asyncpg.Connection | None = None
    try:
        admin_conn = await asyncpg.connect(DATABASE_URL)
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(SCHEMA_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        if admin_conn is not None:
            await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable for L2 confirmed-source tests: {exc}")
    assert admin_conn is not None
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


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _fetch_source_row(pool: asyncpg.Pool, record_id: UUID):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, markdown_text, revision, content_sha256, status,
                   edit_source, original_input_id, frozen_at
            FROM confirmed_source_documents
            WHERE reading_record_id = $1 AND record_generation = 1
            """,
            record_id,
        )


async def _fetch_original_input(pool: asyncpg.Pool, record_id: UUID):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, source_text, source_ref_json, content_sha256
            FROM original_inputs
            WHERE reading_record_id = $1
            """,
            record_id,
        )


async def _double_body_count(pool: asyncpg.Pool) -> int:
    """合同 “Identity / Truth ownership” 验证 SQL：每 generation 仅一份完整 Markdown。"""
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT count(*)
            FROM original_inputs oi
            JOIN confirmed_source_documents cs
              ON cs.original_input_id = oi.id
            WHERE oi.source_text IS NOT NULL
            """
        )
    return int(count)


async def _fetch_candidates(pool: asyncpg.Pool, record_id: UUID):
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, status, source_refs_json
            FROM candidate_reading_documents
            WHERE reading_record_id = $1
            ORDER BY created_at ASC
            """,
            record_id,
        )


async def _create_candidate(pool: asyncpg.Pool, user_id: UUID, text: str):
    service = CandidateDocumentCreationService(pool=pool)
    return await service.create_candidate_document_from_input(
        user_id=user_id,
        source_type="pasted_text",
        text=text,
        language="en",
    )


async def _confirm(pool: asyncpg.Pool, *, record_id: UUID, candidate_id: UUID, user_id: UUID):
    service = CandidateDocumentConfirmApplicationService(pool=pool)
    return await service.confirm_candidate_document_and_load_snapshot(
        candidate_document_id=candidate_id,
        reading_record_id=record_id,
        user_id=user_id,
        canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
        builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
        segmenter_version=AUTO_SEGMENTER_POLICY,
        language="en",
    )


# ---------------------------------------------------------------------------
# 1. 文本路径 candidate 创建：source 行 + source_text NULL + 三 key
# ---------------------------------------------------------------------------


async def test_text_candidate_creation_writes_confirmed_source(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    result = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    source = await _fetch_source_row(db_env, result.reading_record_id)
    assert source is not None
    assert source["status"] == "draft"
    assert source["revision"] == 1
    assert source["edit_source"] == "initial"
    assert source["frozen_at"] is None
    assert source["markdown_text"] == _normalized(_CANDIDATE_MD)
    assert source["content_sha256"] == confirmed_source_content_sha256(_normalized(_CANDIDATE_MD))
    assert source["original_input_id"] == result.original_input_id

    # original_inputs 仅留 lineage：source_text 恒 NULL，hash 保留。
    original_input = await _fetch_original_input(db_env, result.reading_record_id)
    assert original_input["source_text"] is None
    assert original_input["content_sha256"] is not None

    # “Identity / Truth ownership” 验证 SQL：每 generation 仅一份完整 Markdown（无双正文）。
    assert await _double_body_count(db_env) == 0

    # candidate source_refs_json 三 key 与 source 行一致。
    candidates = await _fetch_candidates(db_env, result.reading_record_id)
    (candidate,) = candidates
    refs = _json(candidate["source_refs_json"])
    assert refs["confirmed_source_document_id"] == str(source["id"])
    assert refs["source_revision"] == 1
    assert refs["source_content_sha256"] == source["content_sha256"]


# ---------------------------------------------------------------------------
# 2. stable-ready：source 与 Stable Document 同事务冻结
# ---------------------------------------------------------------------------


async def test_stable_ready_freezes_confirmed_source_same_transaction(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    service = StableReadyInputApplicationService(pool=db_env)
    result = await service.freeze_stable_ready_input_and_load_snapshot(
        user_id=user_id,
        source_type="pasted_text",
        text=_STABLE_MD,
        language="en",
    )
    assert result.suitability.outcome == "stable_document_ready"

    source = await _fetch_source_row(db_env, result.reading_record_id)
    assert source is not None
    assert source["status"] == "frozen"
    assert source["frozen_at"] is not None
    assert source["edit_source"] == "initial"
    # stable-ready 的 Confirmed Source 正文与 preparsed/normalizer
    # 输入同式（\r\n→\n，无 strip）。
    expected_body = _STABLE_MD.replace("\r\n", "\n").replace("\r", "\n")
    assert source["content_sha256"] == confirmed_source_content_sha256(expected_body)

    original_input = await _fetch_original_input(db_env, result.reading_record_id)
    assert original_input["source_text"] is None
    assert await _double_body_count(db_env) == 0

    # stable blocks 与 source 同 revision 生命周期（generation 1）。
    async with db_env.acquire() as conn:
        block_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM stable_reading_documents d
            JOIN stable_document_blocks b ON b.stable_document_id = d.id
            WHERE d.reading_record_id = $1
            """,
            result.reading_record_id,
        )
    assert int(block_count) > 0


# ---------------------------------------------------------------------------
# 3. PUT stale revision：不覆盖较新草稿
# ---------------------------------------------------------------------------


async def test_put_stale_revision_does_not_overwrite(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_MD)
    service = ConfirmedSourceApplicationService(pool=db_env)

    first = await service.update_confirmed_source(
        record_id=created.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_CANDIDATE_MD_EDITED,
        edit_source="source_mode",
    )
    assert first.revision == 2

    with pytest.raises(ConfirmedSourceConflictError) as exc_info:
        await service.update_confirmed_source(
            record_id=created.reading_record_id,
            user_id=user_id,
            expected_revision=1,
            markdown_text=_CANDIDATE_MD,
            edit_source="wysiwyg",
        )
    assert exc_info.value.code == "stale_source_revision"
    assert exc_info.value.resolution == "reload"
    assert exc_info.value.current_revision == 2

    # 较新草稿未被覆盖。
    source = await _fetch_source_row(db_env, created.reading_record_id)
    assert source["revision"] == 2
    assert source["markdown_text"] == _normalized(_CANDIDATE_MD_EDITED)


# ---------------------------------------------------------------------------
# 4. PUT 同 hash：幂等 no-op
# ---------------------------------------------------------------------------


async def test_put_same_hash_is_idempotent_noop(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_MD)
    service = ConfirmedSourceApplicationService(pool=db_env)

    result = await service.update_confirmed_source(
        record_id=created.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_CANDIDATE_MD,
        edit_source="wysiwyg",
    )
    assert result.outcome == "idempotent_noop"
    assert result.revision == 1
    assert result.candidate is not None
    assert result.candidate.candidate_document_id == created.candidate_document_id

    # candidate 未被 supersede，仍只有创建时那一份 ready candidate。
    candidates = await _fetch_candidates(db_env, created.reading_record_id)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "ready"


# ---------------------------------------------------------------------------
# 5. PUT 编辑：版本化 candidate supersede
# ---------------------------------------------------------------------------


async def test_put_edit_supersedes_candidate_and_bumps_revision(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_MD)
    service = ConfirmedSourceApplicationService(pool=db_env)

    result = await service.update_confirmed_source(
        record_id=created.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_CANDIDATE_MD_EDITED,
        edit_source="source_mode",
    )
    assert result.outcome == "candidate_document_required"
    assert result.revision == 2
    assert result.candidate is not None
    assert result.candidate.candidate_document_id != created.candidate_document_id

    candidates = await _fetch_candidates(db_env, created.reading_record_id)
    statuses = {row["id"]: row["status"] for row in candidates}
    assert statuses[created.candidate_document_id] == "superseded"
    ready = [row for row in candidates if row["status"] == "ready"]
    assert len(ready) == 1
    refs = _json(ready[0]["source_refs_json"])
    source = await _fetch_source_row(db_env, created.reading_record_id)
    assert refs["source_revision"] == 2
    assert refs["source_content_sha256"] == source["content_sha256"]

    # PUT 的 reparse 结构保真（阶段 1 机制：detected_format 驱动）。
    async with db_env.acquire() as conn:
        new_candidate_row = await conn.fetchrow(
            "SELECT blocks_json FROM candidate_reading_documents WHERE id = $1",
            ready[0]["id"],
        )
    blocks = _json(new_candidate_row["blocks_json"])
    headings = [b for b in blocks if b["block_type"] == "heading"]
    assert headings, f"block types: {[b['block_type'] for b in blocks]}"
    assert headings[0]["text_content"] == "Quarterly Review Notes ( Revised )"


# ---------------------------------------------------------------------------
# 6. 编辑后 confirm：使用修改后原文；frozen source 与 stable 同 revision/hash
# ---------------------------------------------------------------------------


async def test_confirm_after_edit_uses_edited_source(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_MD)
    service = ConfirmedSourceApplicationService(pool=db_env)
    updated = await service.update_confirmed_source(
        record_id=created.reading_record_id,
        user_id=user_id,
        expected_revision=1,
        markdown_text=_CANDIDATE_MD_EDITED,
        edit_source="source_mode",
    )
    assert updated.candidate is not None

    confirmed = await _confirm(
        db_env,
        record_id=created.reading_record_id,
        candidate_id=updated.candidate.candidate_document_id,
        user_id=user_id,
    )
    assert confirmed.candidate_confirmed is True

    # stable blocks / canonical 使用修改后原文。
    async with db_env.acquire() as conn:
        heading_text = await conn.fetchval(
            """
            SELECT b.text_content
            FROM stable_reading_documents d
            JOIN stable_document_blocks b ON b.stable_document_id = d.id
            WHERE d.reading_record_id = $1 AND b.block_type = 'heading'
            ORDER BY b.order_index ASC
            LIMIT 1
            """,
            created.reading_record_id,
        )
        canonical = await conn.fetchval(
            """
            SELECT text FROM reading_bases
            WHERE reading_record_id = $1 AND status = 'active'
            """,
            created.reading_record_id,
        )
    assert heading_text == "Quarterly Review Notes ( Revised )"
    assert "edited" in str(canonical)

    # frozen source 与 candidate 引用同 revision/hash（插入点 A/B）。
    source = await _fetch_source_row(db_env, created.reading_record_id)
    assert source["status"] == "frozen"
    assert source["frozen_at"] is not None
    assert source["revision"] == 2
    candidates = await _fetch_candidates(db_env, created.reading_record_id)
    confirmed_candidate = next(row for row in candidates if row["status"] == "confirmed")
    refs = _json(confirmed_candidate["source_refs_json"])
    assert refs["source_revision"] == source["revision"]
    assert refs["source_content_sha256"] == source["content_sha256"]


# ---------------------------------------------------------------------------
# 7. 重复 confirm：幂等（recovery 路径）
# ---------------------------------------------------------------------------


async def test_repeated_confirm_is_idempotent(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    first = await _confirm(
        db_env,
        record_id=created.reading_record_id,
        candidate_id=created.candidate_document_id,
        user_id=user_id,
    )
    second = await _confirm(
        db_env,
        record_id=created.reading_record_id,
        candidate_id=created.candidate_document_id,
        user_id=user_id,
    )
    assert second.stable_document_id == first.stable_document_id
    assert second.base_id == first.base_id
    assert second.freeze_idempotent_noop is True

    # recovery 第 6 条：source 保持 frozen 且 revision/hash 未漂移。
    source = await _fetch_source_row(db_env, created.reading_record_id)
    assert source["status"] == "frozen"
    assert source["revision"] == 1


# ---------------------------------------------------------------------------
# 8. confirm 引用过期 source revision：fail closed（stale_candidate_revision）
# ---------------------------------------------------------------------------


async def test_confirm_with_stale_candidate_revision_fails_closed(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    created = await _create_candidate(db_env, user_id, _CANDIDATE_MD)

    # 模拟 source 在 candidate 生成后被编辑（绕过 PUT 的 supersede，
    # 直接推进 source revision/hash —— candidate 仍引用 revision 1）。
    edited = _normalized(_CANDIDATE_MD_EDITED)
    async with db_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE confirmed_source_documents
            SET markdown_text = $2,
                revision = 2,
                content_sha256 = $3,
                updated_at = NOW()
            WHERE reading_record_id = $1
            """,
            created.reading_record_id,
            edited,
            confirmed_source_content_sha256(edited),
        )

    with pytest.raises(StaleCandidateRevisionApplicationError) as exc_info:
        await _confirm(
            db_env,
            record_id=created.reading_record_id,
            candidate_id=created.candidate_document_id,
            user_id=user_id,
        )
    assert exc_info.value.current_revision == 2

    # fail closed：无 stable document，source 保持 draft（未冻结）。
    source = await _fetch_source_row(db_env, created.reading_record_id)
    assert source["status"] == "draft"
    assert source["revision"] == 2
    async with db_env.acquire() as conn:
        stable_count = await conn.fetchval(
            "SELECT COUNT(*) FROM stable_reading_documents WHERE reading_record_id = $1",
            created.reading_record_id,
        )
    assert int(stable_count) == 0


# ---------------------------------------------------------------------------
# 9. legacy candidate（无 source 行/引用）：confirm 走旧逻辑向后兼容
# ---------------------------------------------------------------------------


async def test_legacy_candidate_confirms_via_legacy_branch(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    record_id = uuid4()
    original_input_id = uuid4()
    candidate_id = uuid4()
    legacy_text = _STABLE_MD
    blocks, title = _build_candidate_blocks(
        source_type="pasted_text",
        text=legacy_text,
        filename=None,
        source_metadata={},
        original_input_id=original_input_id,
    )
    async with db_env.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, reading_goal, reading_variant
            )
            VALUES ($1, $2, 'text', $3, 'en',
                    'active', 'needs_confirmation', 'candidate_base_ready',
                    1, 'daily_reading', 'intermediate_reading')
            """,
            record_id,
            user_id,
            title,
        )
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'plain_text', $4, $5::jsonb, '{}'::jsonb, $6)
            """,
            original_input_id,
            record_id,
            user_id,
            legacy_text,
            jsonb_param({"adapter_source_type": "pasted_text"}),
            confirmed_source_content_sha256(legacy_text),
        )
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status
            )
            VALUES ($1, $2, $3, 1, $4, $5::jsonb, 'legacy preview',
                    $6::jsonb, '{}'::jsonb, 'ready')
            """,
            candidate_id,
            record_id,
            user_id,
            title,
            jsonb_param([block.model_dump(mode="json") for block in blocks]),
            jsonb_param(
                {
                    "source_type": "pasted_text",
                    "original_input_id": str(original_input_id),
                }
            ),
        )

    confirmed = await _confirm(
        db_env,
        record_id=record_id,
        candidate_id=candidate_id,
        user_id=user_id,
    )
    assert confirmed.candidate_confirmed is True

    # legacy 分支不创建 source 行。
    source = await _fetch_source_row(db_env, record_id)
    assert source is None


# ---------------------------------------------------------------------------
# 11. GET confirmed-source：三级分类信息（Content Check 首载/刷新恢复）
# ---------------------------------------------------------------------------


async def test_get_confirmed_source_returns_quality_and_classification_split(
    db_env: asyncpg.Pool,
) -> None:
    """有 ready candidate 时：quality 超集 + adaptations 按 classification
    拆分（footnote → content_check；安全 aside → adaptation_notice）。"""
    user_id = await _insert_user(db_env)
    text_with_aside = (
        _CANDIDATE_MD + '\n<aside class="note">A safe Notion callout carried along as an '
        "adaptation notice for every reader of this document today.</aside>\n"
    )
    created = await _create_candidate(db_env, user_id, text_with_aside)
    assert created.suitability.outcome == "candidate_document_required"

    service = ConfirmedSourceApplicationService(pool=db_env)
    result = await service.get_confirmed_source(
        record_id=created.reading_record_id,
        user_id=user_id,
    )

    assert result.candidate is not None
    assert result.candidate.candidate_document_id == created.candidate_document_id
    # quality = _candidate_quality_json 超集。
    assert result.quality["candidate_creation_version"] == "candidate_creation_v1"
    assert result.quality["suitability"]["outcome"] == "candidate_document_required"
    # 三级分类拆分。
    notice_codes = {item["code"] for item in result.adaptation_notice}
    check_codes = {item["code"] for item in result.content_check}
    assert "raw_html_block" in notice_codes
    assert "footnote_reference" in check_codes
    assert all(item["classification"] == "adaptation_notice" for item in result.adaptation_notice)
    assert all(item["classification"] == "content_check" for item in result.content_check)


async def test_get_confirmed_source_without_candidate_returns_empty_classification(
    db_env: asyncpg.Pool,
) -> None:
    """无 ready candidate（如 artifact rejected 后的 draft source）时：
    candidate=None，quality={}，两个分类列表为空。"""
    user_id = await _insert_user(db_env)
    record_id = uuid4()
    source_sha = confirmed_source_content_sha256(_STABLE_MD)
    async with db_env.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, reading_goal, reading_variant
            )
            VALUES ($1, $2, 'file', 'Rejected Artifact', 'en',
                    'active', 'action_required', 'submitted',
                    1, 'daily_reading', 'intermediate_reading')
            """,
            record_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO confirmed_source_documents (
                id, reading_record_id, user_id, record_generation,
                original_input_id, markdown_text, revision,
                content_sha256, status, edit_source
            )
            VALUES ($1, $2, $3, 1, NULL, $4, 1, $5, 'draft', 'extraction')
            """,
            uuid4(),
            record_id,
            user_id,
            _STABLE_MD,
            source_sha,
        )

    service = ConfirmedSourceApplicationService(pool=db_env)
    result = await service.get_confirmed_source(
        record_id=record_id,
        user_id=user_id,
    )
    assert result.candidate is None
    assert result.quality == {}
    assert result.adaptation_notice == []
    assert result.content_check == []


# ---------------------------------------------------------------------------
# 10. artifact 路径：pipeline-status 增 has_confirmed_source（Q5）
# ---------------------------------------------------------------------------


async def test_pipeline_status_reports_has_confirmed_source(
    db_env: asyncpg.Pool,
) -> None:
    user_id = await _insert_user(db_env)
    record_id = uuid4()
    original_input_id = uuid4()
    artifact_id = uuid4()
    source_sha = confirmed_source_content_sha256(_STABLE_MD)
    async with db_env.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state,
                generation, reading_goal, reading_variant
            )
            VALUES ($1, $2, 'file', 'Artifact Test', 'en',
                    'active', 'needs_confirmation', 'candidate_base_ready',
                    1, 'daily_reading', 'intermediate_reading')
            """,
            record_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref', NULL, $4::jsonb,
                    '{"extraction_status": "succeeded"}'::jsonb, $5)
            """,
            original_input_id,
            record_id,
            user_id,
            jsonb_param({"artifact_id": str(artifact_id)}),
            source_sha,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename,
                status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', 'claread-dev',
                    'dev/test/notes.txt',
                    'https://oss-cn-shenzhen.aliyuncs.com',
                    'text/plain', 100, $5, 'notes.txt', 'available')
            """,
            artifact_id,
            record_id,
            original_input_id,
            user_id,
            source_sha,
        )
        await conn.execute(
            """
            INSERT INTO confirmed_source_documents (
                id, reading_record_id, user_id, record_generation,
                original_input_id, markdown_text, revision,
                content_sha256, status, edit_source
            )
            VALUES ($1, $2, $3, 1, $4, $5, 1, $6, 'draft', 'extraction')
            """,
            uuid4(),
            record_id,
            user_id,
            original_input_id,
            _STABLE_MD,
            source_sha,
        )
        # status 查询要求 bound artifact 存在 extraction job。
        run_id = uuid4()
        await conn.execute(
            """
            INSERT INTO reader_runs (
                id, reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, $3, 'input_artifact_extraction', 'completed',
                    1, '{}'::jsonb, 'reader_input_artifact_extraction_v1',
                    'system')
            """,
            run_id,
            record_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                expected_generation, operation_fingerprint, idempotency_key
            )
            VALUES ($1, NULL, $2, $3,
                    'input_artifact_extraction', 'record', $4, 'succeeded',
                    1, 'input_artifact_extraction_v1', $5)
            """,
            record_id,
            run_id,
            user_id,
            str(artifact_id),
            f"idem-{uuid4().hex}",
        )
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                expected_generation, operation_fingerprint, idempotency_key
            )
            VALUES ($1, NULL, $2, $3,
                    'extracted_artifact_materialization', 'record', $4,
                    'succeeded',
                    1, 'extracted_artifact_materialization_v1', $5)
            """,
            record_id,
            run_id,
            user_id,
            str(artifact_id),
            f"idem-{uuid4().hex}",
        )
        # needs_confirmation 状态要求存在 ready candidate。
        await conn.execute(
            """
            INSERT INTO candidate_reading_documents (
                id, reading_record_id, user_id, record_generation,
                title, blocks_json, canonical_text_preview,
                source_refs_json, quality_json, status
            )
            VALUES ($1, $2, $3, 1, 'Artifact Test',
                    '[]'::jsonb, 'preview', '{}'::jsonb, '{}'::jsonb,
                    'ready')
            """,
            uuid4(),
            record_id,
            user_id,
        )

    service = ArtifactPipelineStatusQueryService(pool=db_env)
    result = await service.load_pipeline_status(
        artifact_id=artifact_id,
        user_id=user_id,
    )
    assert result.original_input is not None
    # Q5：has_source_text 对 legacy 保留原义（新输入恒 false）；
    # has_confirmed_source 反映 source 行存在性。
    assert result.original_input.has_source_text is False
    assert result.original_input.has_confirmed_source is True
