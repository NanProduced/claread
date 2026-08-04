# task-history: D6-I2E (renamed from test_d6_i2e_stable_document_query_service.py)
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.stable_document_query_service import (
    StableDocumentProjectionResult,
    StableDocumentQueryError,
    StableDocumentQueryService,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_reader_parse, pytest.mark.seam_pure_unit, pytest.mark.life_permanent_regression]

RECORD_ID = UUID("00000000-0000-0000-0000-000000000201")
USER_ID = UUID("00000000-0000-0000-0000-000000000202")
BASE_ID = UUID("00000000-0000-0000-0000-000000000203")
STABLE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000204")


class _RecordedCall:
    __slots__ = ("kind", "query", "args")

    def __init__(self, kind: str, query: str, args: tuple[Any, ...]) -> None:
        self.kind = kind
        self.query = query
        self.args = args


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[_RecordedCall] = []
        self._fetchrow_queue: list[dict[str, Any] | None] = []
        self._fetch_queue: list[list[dict[str, Any]]] = []

    def queue_fetchrow(self, value: dict[str, Any] | None) -> None:
        self._fetchrow_queue.append(value)

    def queue_fetch(self, rows: list[dict[str, Any]]) -> None:
        self._fetch_queue.append(rows)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append(_RecordedCall("fetchrow", query, args))
        if self._fetchrow_queue:
            return self._fetchrow_queue.pop(0)
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(_RecordedCall("fetch", query, args))
        if self._fetch_queue:
            return self._fetch_queue.pop(0)
        return []

    def transaction(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("StableDocumentQueryService should not open a transaction")


class _FakeAcquireContext:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self._conn)


def _build_service(conn: _FakeConn) -> StableDocumentQueryService:
    return StableDocumentQueryService(pool=_FakePool(conn))  # type: ignore[arg-type]


def _queue_happy_path(conn: _FakeConn) -> None:
    conn.queue_fetchrow(
        {
            "generation": 4,
            "active_base_id": BASE_ID,
        }
    )
    conn.queue_fetchrow(
        {
            "id": STABLE_DOCUMENT_ID,
            "document_version": 2,
            "title": "Stable Title",
            "source_profile_json": json.dumps(
                {
                    "source_refs": {"url": "https://example.com/article"},
                    "quality": {"flags": ["ocr_corrected"]},
                }
            ),
            "content_sha256": "a" * 64,
            "status": "active",
        }
    )
    conn.queue_fetchrow(
        {
            "id": BASE_ID,
            "content_sha256": "b" * 64,
            "content_utf16_length": 123,
            "canonicalizer_version": "canon-v1",
            "builder_version": "builder-v1",
            "segmenter_version": "segmenter-v1",
            "language": "en",
            "title_snapshot": "Base Title",
            "navigation_json": {
                "units": [
                    {
                        "unit_id": "u1",
                        "order_index": 1,
                    }
                ]
            },
            "text": "\nSection A\nHello stable document.\n",
        }
    )
    conn.queue_fetch(
        [
            {
                "block_id": "heading-1",
                "parent_block_id": None,
                "order_index": 0,
                "block_type": "heading",
                "text_content": "Section A",
                "payload_json": json.dumps({"level": 2}),
                "source_refs_json": {"page": 1},
                "quality_json": json.dumps({"warnings": []}),
                "canonical_text_start_utf16": 0,
                "canonical_text_end_utf16": 9,
                "interpretation_policy_json": {
                    "allowed_source_scope": ["heading"],
                    "default_route": "main_reading",
                    "rag_eligible": True,
                },
            },
            {
                "block_id": "paragraph-1",
                "parent_block_id": None,
                "order_index": 1,
                "block_type": "paragraph",
                "text_content": "Hello stable document.",
                "payload_json": {"kind": "body"},
                "source_refs_json": json.dumps({"page": 1, "line": 3}),
                "quality_json": {"score": 0.99},
                "canonical_text_start_utf16": 11,
                "canonical_text_end_utf16": 33,
                "interpretation_policy_json": json.dumps(
                    {
                        "allowed_source_scope": ["main_reading_text"],
                        "default_route": "main_reading",
                        "rag_eligible": True,
                    }
                ),
            },
        ]
    )


async def test_load_active_stable_document_happy_path_maps_fields_and_accepts_dict_or_json_string() -> None:
    conn = _FakeConn()
    _queue_happy_path(conn)
    service = _build_service(conn)

    result = await service.load_active_stable_document(
        record_id=RECORD_ID,
        user_id=USER_ID,
    )

    assert isinstance(result, StableDocumentProjectionResult)
    assert result.reading_record_id == RECORD_ID
    assert result.record_generation == 4
    assert result.active_base_id == BASE_ID
    assert result.base.base_id == BASE_ID
    assert result.base.content_sha256 == "b" * 64
    assert result.base.navigation == {"units": [{"unit_id": "u1", "order_index": 1}]}
    assert result.stable_document.stable_document_id == STABLE_DOCUMENT_ID
    assert result.stable_document.document_version == 2
    assert result.stable_document.language == "en"
    assert result.stable_document.source_profile == {
        "source_refs": {"url": "https://example.com/article"},
        "quality": {"flags": ["ocr_corrected"]},
    }
    assert [block.block_id for block in result.blocks] == ["heading-1", "paragraph-1"]
    assert result.blocks[0].payload == {"level": 2}
    assert result.blocks[0].source_refs == {"page": 1}
    assert result.blocks[0].quality == {"warnings": []}
    assert result.blocks[1].payload == {"kind": "body"}
    assert result.blocks[1].source_refs == {"page": 1, "line": 3}
    assert result.blocks[1].quality == {"score": 0.99}
    assert any(
        call.kind == "fetch" and "ORDER BY order_index ASC" in call.query
        for call in conn.calls
    )


async def test_load_active_stable_document_record_missing_raises_lookup_error() -> None:
    conn = _FakeConn()
    conn.queue_fetchrow(None)
    service = _build_service(conn)

    with pytest.raises(LookupError, match="not found"):
        await service.load_active_stable_document(
            record_id=RECORD_ID,
            user_id=USER_ID,
        )


async def test_load_active_stable_document_active_base_id_null_fails_closed() -> None:
    conn = _FakeConn()
    conn.queue_fetchrow({"generation": 4, "active_base_id": None})
    service = _build_service(conn)

    with pytest.raises(StableDocumentQueryError, match="no active base"):
        await service.load_active_stable_document(
            record_id=RECORD_ID,
            user_id=USER_ID,
        )


async def test_load_active_stable_document_active_stable_document_missing_fails_closed() -> None:
    conn = _FakeConn()
    conn.queue_fetchrow({"generation": 4, "active_base_id": BASE_ID})
    conn.queue_fetchrow(None)
    service = _build_service(conn)

    with pytest.raises(StableDocumentQueryError, match="no active stable document"):
        await service.load_active_stable_document(
            record_id=RECORD_ID,
            user_id=USER_ID,
        )


async def test_load_active_stable_document_active_base_missing_fails_closed() -> None:
    conn = _FakeConn()
    conn.queue_fetchrow({"generation": 4, "active_base_id": BASE_ID})
    conn.queue_fetchrow(
        {
            "id": STABLE_DOCUMENT_ID,
            "document_version": 2,
            "title": "Stable Title",
            "source_profile_json": {},
            "content_sha256": "a" * 64,
            "status": "active",
        }
    )
    conn.queue_fetchrow(None)
    service = _build_service(conn)

    with pytest.raises(StableDocumentQueryError, match="no active reading base"):
        await service.load_active_stable_document(
            record_id=RECORD_ID,
            user_id=USER_ID,
        )


async def test_load_active_stable_document_zero_blocks_fails_closed() -> None:
    conn = _FakeConn()
    _queue_happy_path(conn)
    conn._fetch_queue[-1] = []
    service = _build_service(conn)

    with pytest.raises(StableDocumentQueryError, match="no ordered blocks"):
        await service.load_active_stable_document(
            record_id=RECORD_ID,
            user_id=USER_ID,
        )


async def test_load_active_stable_document_returns_canonical_text_and_anchors() -> None:
    conn = _FakeConn()
    _queue_happy_path(conn)
    # Augment base_row with the canonical text column.  ``_queue_happy_path``
    # already queues three fetchrow results; we splice text into index 2
    # (the base row) and add an anchor_segments fetch after the blocks.
    base_index = 2
    conn._fetchrow_queue[base_index]["text"] = (
        "\nSection A\nHello stable document.\n"
    )
    conn.queue_fetch(
        [
            {
                "anchor_segment_id": "as-2",
                "unit_id": "u1",
                "order_index": 1,
                "segment_type": "sentence",
                "base_start_utf16": 11,
                "base_end_utf16": 33,
                "text_hash": "12345678",
            },
            {
                "anchor_segment_id": "as-1",
                "unit_id": "u1",
                "order_index": 0,
                "segment_type": "sentence",
                "base_start_utf16": 0,
                "base_end_utf16": 9,
                "text_hash": "abcdef00",
            },
        ]
    )
    service = _build_service(conn)
    result = await service.load_active_stable_document(
        record_id=RECORD_ID, user_id=USER_ID,
    )

    assert result.base.text.startswith("\nSection A")
    # Anchor segments sorted by order_index ascending.
    assert [a.anchor_segment_id for a in result.anchor_segments] == ["as-1", "as-2"]
    # All blocks present.
    assert result.blocks[0].text_content == "Section A"
    # Block slices match base.text by offset.
    assert result.base.text[1:10] == "Section A"


@pytest.mark.parametrize(
    ("raw_value", "match"),
    [
        ("{not valid json", r"reading_bases\.navigation_json is not valid JSON"),
        ("[1, 2, 3]", r"reading_bases\.navigation_json parses to a non-object JSON value"),
        (None, r"reading_bases\.navigation_json must be a JSON object"),
    ],
)
async def test_load_active_stable_document_invalid_json_object_field_fails_closed(
    raw_value: Any,
    match: str,
) -> None:
    conn = _FakeConn()
    _queue_happy_path(conn)
    conn._fetchrow_queue[2]["navigation_json"] = raw_value
    service = _build_service(conn)

    with pytest.raises(StableDocumentQueryError, match=match):
        await service.load_active_stable_document(
            record_id=RECORD_ID,
            user_id=USER_ID,
        )
