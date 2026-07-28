from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from app.services.reader_orchestration.base_builder import (
    AUTO_SEGMENTER_POLICY,
    DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    EXACT_CANONICAL_TEXT_VERSION,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    StableDocumentFreezePersistenceResult,
)
from app.services.reader_orchestration.event_runtime import ReaderEventEnvelope
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
)
from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    PROFILE,
)
from app.services.reader_orchestration.stable_ready_input_application_service import (
    StableReadyInputApplicationError,
    StableReadyInputApplicationResult,
    StableReadyInputApplicationService,
)


class _RecordedCall:
    __slots__ = ("kind", "query", "args")

    def __init__(self, kind: str, query: str, args: tuple[Any, ...]) -> None:
        self.kind = kind
        self.query = query
        self.args = args


class _FakeTransaction:
    def __init__(self, conn: _FakeConn, log: list[str] | None = None) -> None:
        self._conn = conn
        self._log = log
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> None:
        self._conn._in_transaction = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        self._conn._in_transaction = False
        if exc_type is None:
            self.committed = True
            if self._log is not None:
                self._log.append("transaction_committed")
        else:
            self.rolled_back = True
            if self._log is not None:
                self._log.append("transaction_rolled_back")
        return False


class _FakeConn:
    def __init__(self, *, log: list[str] | None = None) -> None:
        self.calls: list[_RecordedCall] = []
        self._in_transaction = False
        self._log = log
        self._last_transaction: _FakeTransaction | None = None

    def transaction(
        self,
        *,
        isolation: str | None = None,
        readonly: bool = False,
    ) -> _FakeTransaction:
        self._last_transaction = _FakeTransaction(self, log=self._log)
        return self._last_transaction

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(_RecordedCall("execute", query, args))
        if self._log is not None:
            if "INSERT INTO reading_records" in query:
                self._log.append("insert_reading_record")
            elif "INSERT INTO original_inputs" in query:
                self._log.append("insert_original_input")
            elif "UPDATE confirmed_source_documents" in query:
                self._log.append("freeze_confirmed_source")
        if "UPDATE confirmed_source_documents" in query:
            # freeze_confirmed_source expects exactly "UPDATE 1".
            return "UPDATE 1"
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append(_RecordedCall("fetchrow", query, args))
        if self._log is not None and "INSERT INTO confirmed_source_documents" in query:
            self._log.append("insert_confirmed_source")
        # L2: insert_confirmed_source uses INSERT ... RETURNING via
        # fetchrow; synthesize the inserted row from the query args
        # (id, record_id, user_id, generation, original_input_id,
        # markdown_text, content_sha256, edit_source, now).
        if "INSERT INTO confirmed_source_documents" in query:
            return {
                "id": args[0],
                "reading_record_id": args[1],
                "user_id": args[2],
                "record_generation": args[3],
                "original_input_id": args[4],
                "markdown_text": args[5],
                "revision": 1,
                "content_sha256": args[6],
                "status": "draft",
                "edit_source": args[7],
            }
        return None

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    @property
    def execute_calls(self) -> list[_RecordedCall]:
        return [call for call in self.calls if call.kind == "execute"]


class _FakePoolAcquireContext:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        return None


class FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePoolAcquireContext:
        return _FakePoolAcquireContext(self._conn)


class FakeRepository:
    def __init__(
        self,
        *,
        log: list[str] | None = None,
        raise_on_set_active_base: Exception | None = None,
    ) -> None:
        self.set_active_base_calls: list[dict[str, Any]] = []
        self._log = log
        self._raise = raise_on_set_active_base

    async def set_active_base_and_mark_article_ready(
        self,
        conn: Any,
        *,
        record_id: UUID,
        base_id: UUID,
        expected_generation: int,
        updated_at: datetime,
    ) -> None:
        assert conn.is_in_transaction()
        self.set_active_base_calls.append(
            {
                "record_id": record_id,
                "base_id": base_id,
                "expected_generation": expected_generation,
                "updated_at": updated_at,
            }
        )
        if self._log is not None:
            self._log.append("set_active_base")
        if self._raise is not None:
            raise self._raise

    def get_pool(self) -> Any:
        raise RuntimeError("FakeRepository.get_pool should not be called when pool is injected")


class FakeEventRuntime:
    def __init__(
        self,
        *,
        event_id: UUID,
        sequence: int,
        log: list[str] | None = None,
        raise_on_publish: Exception | None = None,
    ) -> None:
        self.publish_calls: list[dict[str, Any]] = []
        self._event_id = event_id
        self._sequence = sequence
        self._log = log
        self._raise = raise_on_publish

    async def publish_event_in_transaction(
        self,
        conn: Any,
        *,
        record_id: UUID,
        event_type: str,
        payload_json: Any,
        source_run_id: UUID | None = None,
        source_job_id: UUID | None = None,
        source_layer_id: UUID | None = None,
        event_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReaderEventEnvelope:
        assert conn.is_in_transaction()
        self.publish_calls.append(
            {
                "record_id": record_id,
                "event_type": event_type,
                "payload_json": dict(payload_json),
                "created_at": created_at,
            }
        )
        if self._log is not None:
            self._log.append("publish_event")
        if self._raise is not None:
            raise self._raise
        return ReaderEventEnvelope(
            event_id=event_id or self._event_id,
            reading_record_id=record_id,
            sequence=self._sequence,
            event_type=event_type,
            payload_json=dict(payload_json),
            source_run_id=source_run_id,
            source_job_id=source_job_id,
            source_layer_id=source_layer_id,
            created_at=created_at or _FROZEN_AT,
        )


class _FakeSnapshot:
    pass


class FakeSnapshotService:
    def __init__(
        self,
        *,
        snapshot: Any,
        log: list[str] | None = None,
        raise_on_load: Exception | None = None,
    ) -> None:
        self.load_calls: list[dict[str, Any]] = []
        self._snapshot = snapshot
        self._log = log
        self._raise = raise_on_load

    async def load_snapshot(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> Any:
        self.load_calls.append(
            {
                "record_id": record_id,
                "user_id": user_id,
                "expected_base_id": expected_base_id,
                "expected_generation": expected_generation,
            }
        )
        if self._log is not None:
            self._log.append("snapshot_loaded")
        if self._raise is not None:
            raise self._raise
        return self._snapshot


_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
_STABLE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000002")
_BASE_ID = UUID("00000000-0000-0000-0000-000000000003")
_EVENT_ID = UUID("00000000-0000-0000-0000-000000000004")
_FROZEN_AT = datetime(2026, 6, 26, 9, 0, 0, tzinfo=UTC)
_FAKE_SNAPSHOT = _FakeSnapshot()


def _english_paragraph(multiplier: int = 1) -> str:
    sentence = (
        "This article explains how communities compare evidence, revise plans, "
        "and discuss tradeoffs before making a decision about public projects. "
        "Each paragraph stays focused on natural language reading, includes "
        "complete sentences, and keeps enough context for vocabulary, grammar, "
        "and sentence analysis to be genuinely useful for an English learner."
    )
    return "\n\n".join(sentence for _ in range(multiplier))


def _build_service(
    conn: _FakeConn,
    *,
    log: list[str] | None = None,
    repository: FakeRepository | None = None,
    event_runtime: FakeEventRuntime | None = None,
    snapshot_service: FakeSnapshotService | None = None,
) -> StableReadyInputApplicationService:
    return StableReadyInputApplicationService(
        pool=FakePool(conn),
        repository=repository or FakeRepository(log=log),
        event_runtime=event_runtime
        or FakeEventRuntime(event_id=_EVENT_ID, sequence=7, log=log),
        snapshot_service=snapshot_service
        or FakeSnapshotService(snapshot=_FAKE_SNAPSHOT, log=log),
    )


def _freeze(
    service: StableReadyInputApplicationService,
    *,
    source_type: str = "pasted_text",
    text: str,
    filename: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    client_record_id: str | None = None,
    language: str | None = "en",
    now: datetime | None = _FROZEN_AT,
) -> StableReadyInputApplicationResult:
    return asyncio.run(
        service.freeze_stable_ready_input_and_load_snapshot(
            user_id=_USER_ID,
            source_type=source_type,
            text=text,
            filename=filename,
            source_metadata=source_metadata or {},
            client_record_id=client_record_id,
            language=language,
            now=now,
        )
    )


def _freeze_result_from_plan(
    plan: Any,
    *,
    base_id: UUID | None = _BASE_ID,
) -> StableDocumentFreezePersistenceResult:
    canonical_text_sha256 = hashlib.sha256(
        plan.canonical_text.encode("utf-8")
    ).hexdigest()
    return StableDocumentFreezePersistenceResult(
        stable_document_id=_STABLE_DOCUMENT_ID,
        base_id=base_id,
        reading_record_id=UUID(plan.stable_document.reading_record_id),
        record_generation=plan.stable_document.record_generation,
        document_version=plan.stable_document.document_version,
        content_sha256=plan.content_sha256,
        canonical_text_sha256=canonical_text_sha256,
        block_count=len(plan.blocks),
        candidate_confirmed=False,
        idempotent_noop=False,
    )


def test_happy_path_pasted_text_persists_marks_event_and_loads_snapshot() -> None:
    conn = _FakeConn(log=[])
    log: list[str] = []
    conn._log = log
    repo = FakeRepository(log=log)
    event_runtime = FakeEventRuntime(event_id=_EVENT_ID, sequence=7, log=log)
    snapshot_service = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT, log=log)
    service = _build_service(
        conn,
        log=log,
        repository=repo,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )
    captured: dict[str, Any] = {}

    async def fake_persist(conn_arg: Any, **kwargs: Any) -> StableDocumentFreezePersistenceResult:
        assert conn_arg.is_in_transaction()
        log.append("persist_freeze")
        captured.update(kwargs)
        return _freeze_result_from_plan(kwargs["plan"])

    result: StableReadyInputApplicationResult
    with patch(
        "app.services.reader_orchestration.stable_ready_input_application_service.persist_stable_document_freeze_plan",
        new=fake_persist,
    ):
        result = _freeze(
            service,
            text=_english_paragraph(multiplier=2),
            source_metadata={"source_kind": "manual_submit"},
            client_record_id="stable-ready-1",
        )

    assert log == [
        "insert_reading_record",
        "insert_original_input",
        "insert_confirmed_source",
        "persist_freeze",
        "freeze_confirmed_source",
        "set_active_base",
        "publish_event",
        "transaction_committed",
        "snapshot_loaded",
    ]
    assert result.reading_record_id == repo.set_active_base_calls[0]["record_id"]
    assert result.stable_document_id == _STABLE_DOCUMENT_ID
    assert result.base_id == _BASE_ID
    assert result.record_generation == 1
    assert result.document_version == 1
    assert result.title is None
    assert result.block_count == 2
    assert result.article_ready_event_id == _EVENT_ID
    assert result.article_ready_sequence == 7
    assert result.suitability.outcome == "stable_document_ready"
    assert result.snapshot is _FAKE_SNAPSHOT

    payload = event_runtime.publish_calls[0]["payload_json"]
    assert payload["source"] == "stable_ready_input"
    assert payload["stable_document_id"] == str(_STABLE_DOCUMENT_ID)
    assert payload["base_id"] == str(_BASE_ID)
    assert payload["generation"] == 1
    assert payload["document_version"] == 1
    assert payload["block_count"] == 2
    assert payload["suitability"]["outcome"] == "stable_document_ready"
    assert payload["suitability"]["flags"] == []

    assert captured["canonicalizer_version"] == EXACT_CANONICAL_TEXT_VERSION
    assert (
        captured["builder_version"]
        == DETERMINISTIC_READING_BASE_BUILDER_VERSION
    )
    assert captured["segmenter_version"] == AUTO_SEGMENTER_POLICY
    assert captured["language"] == "en"
    assert captured["now"] == _FROZEN_AT
    assert captured["plan"].stable_document.source_profile_json == {
        "source_type": "pasted_text",
        "filename": None,
        "source_metadata": {"source_kind": "manual_submit"},
        "suitability": {
            "outcome": "stable_document_ready",
            "flags": [],
            "reasons": captured["plan"].stable_document.source_profile_json["suitability"]["reasons"],
            # L1: three-level adaptation records (none for this plain input).
            "adaptations": [],
        },
        # T1/T5 — plain text path has no structured-source parser, so
        # parser_identity must be explicitly None (not omitted) to
        # avoid false provenance attribution.
        "parser_identity": None,
    }

    snapshot_call = snapshot_service.load_calls[0]
    assert snapshot_call["record_id"] == result.reading_record_id
    assert snapshot_call["user_id"] == _USER_ID
    assert snapshot_call["expected_base_id"] == _BASE_ID
    assert snapshot_call["expected_generation"] == 1

    queries = [call.query for call in conn.execute_calls]
    assert all("candidate_reading_documents" not in query for query in queries)
    assert "candidate_document_id" not in captured


def test_happy_path_simple_markdown_uses_heading_title_and_includes_code_in_canonical() -> None:
    conn = _FakeConn()
    service = _build_service(conn)
    captured: dict[str, Any] = {}

    async def fake_persist(conn_arg: Any, **kwargs: Any) -> StableDocumentFreezePersistenceResult:
        captured.update(kwargs)
        return _freeze_result_from_plan(kwargs["plan"])

    text = f"""
# Weekly Review

{_english_paragraph()}

- Readers compare background evidence before revising a public plan in writing.
- Editors highlight tradeoffs so the article still teaches grammar and logic clearly.

```python
def add(a, b):
    return a + b
```

---

The closing paragraph explains how the revised decision was communicated to local readers.
""".strip()

    with patch(
        "app.services.reader_orchestration.stable_ready_input_application_service.persist_stable_document_freeze_plan",
        new=fake_persist,
    ):
        result = _freeze(
            service,
            source_type="markdown_file",
            filename="weekly-review.md",
            text=text,
        )

    plan = captured["plan"]
    block_types = [block.block_type for block in plan.blocks]

    assert result.title == "Weekly Review"
    assert result.block_count == len(plan.blocks)
    # Parser emits a ``list`` wrapper block before list_items, and
    # ``thematic_break`` for ``---`` (structural, metadata_only) per
    # the Structured Source Contract.
    assert block_types == [
        "heading",
        "paragraph",
        "list",
        "list_item",
        "list_item",
        "code_block",
        "thematic_break",
        "paragraph",
    ]
    assert "Weekly Review" in plan.canonical_text
    assert "Readers compare background evidence before revising a public plan in writing." in plan.canonical_text
    assert "The closing paragraph explains how the revised decision was communicated to local readers." in plan.canonical_text
    # D2 (e9678eba): code_block defaults to main_reading — code is
    # first-class reading content and enters canonical text; only the
    # fence markers are stripped.
    assert "return a + b" in plan.canonical_text
    assert "```python" not in plan.canonical_text
    assert "---" not in plan.canonical_text

    # T5 / G1 验收条款 4 — parser version identity MUST appear in
    # frozen document metadata (source_profile_json) for the
    # markdown_file path. Document-level metadata must not rely on
    # block-level quality_json inference.
    source_profile = plan.stable_document.source_profile_json
    parser_identity = source_profile.get("parser_identity")
    assert parser_identity is not None, (
        "markdown_file frozen document must carry parser_identity in "
        "source_profile_json"
    )
    assert parser_identity["parser_name"] == PARSER_NAME
    assert parser_identity["parser_version"] == PARSER_VERSION
    assert parser_identity["profile"] == PROFILE


@pytest.mark.parametrize(
    ("source_type", "filename", "text"),
    [
        (
            "pasted_text",
            None,
            "This brief note is far too short for useful reading analysis.",
        ),
        (
            "markdown_file",
            "report.md",
            # L1: deterministic tables are stable-ready; an extra raw
            # cell (column mismatch) keeps this a non-stable outcome.
            f"{_english_paragraph()}\n\n| City | Cost |\n| --- | --- |\n| A | 10 | 99 |",
        ),
    ],
)
def test_non_stable_gate_outcome_wraps_and_does_not_write_or_publish(
    source_type: str,
    filename: str | None,
    text: str,
) -> None:
    conn = _FakeConn()
    repo = FakeRepository()
    event_runtime = FakeEventRuntime(event_id=_EVENT_ID, sequence=7)
    snapshot_service = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
    service = _build_service(
        conn,
        repository=repo,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )

    with pytest.raises(StableReadyInputApplicationError) as excinfo:
        _freeze(
            service,
            source_type=source_type,
            filename=filename,
            text=text,
        )

    assert isinstance(excinfo.value.__cause__, InputDocumentNormalizationError)
    assert conn.execute_calls == []
    assert repo.set_active_base_calls == []
    assert event_runtime.publish_calls == []
    assert snapshot_service.load_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_persist_returning_base_id_none_fails_closed_and_rolls_back() -> None:
    conn = _FakeConn()
    repo = FakeRepository()
    event_runtime = FakeEventRuntime(event_id=_EVENT_ID, sequence=7)
    snapshot_service = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
    service = _build_service(
        conn,
        repository=repo,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )

    async def fake_persist(conn_arg: Any, **kwargs: Any) -> StableDocumentFreezePersistenceResult:
        return _freeze_result_from_plan(kwargs["plan"], base_id=None)

    with patch(
        "app.services.reader_orchestration.stable_ready_input_application_service.persist_stable_document_freeze_plan",
        new=fake_persist,
    ):
        with pytest.raises(StableReadyInputApplicationError, match="base_id=None"):
            _freeze(service, text=_english_paragraph(multiplier=2))

    assert repo.set_active_base_calls == []
    assert event_runtime.publish_calls == []
    assert snapshot_service.load_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_repository_set_active_error_is_wrapped_and_snapshot_is_not_reloaded() -> None:
    conn = _FakeConn()
    repo = FakeRepository(raise_on_set_active_base=ValueError("bad-state"))
    event_runtime = FakeEventRuntime(event_id=_EVENT_ID, sequence=7)
    snapshot_service = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
    service = _build_service(
        conn,
        repository=repo,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )

    async def fake_persist(conn_arg: Any, **kwargs: Any) -> StableDocumentFreezePersistenceResult:
        return _freeze_result_from_plan(kwargs["plan"])

    with patch(
        "app.services.reader_orchestration.stable_ready_input_application_service.persist_stable_document_freeze_plan",
        new=fake_persist,
    ):
        with pytest.raises(StableReadyInputApplicationError) as excinfo:
            _freeze(service, text=_english_paragraph(multiplier=2))

    assert isinstance(excinfo.value.__cause__, ValueError)
    assert event_runtime.publish_calls == []
    assert snapshot_service.load_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_event_publish_error_is_wrapped_and_snapshot_is_not_reloaded() -> None:
    conn = _FakeConn()
    repo = FakeRepository()
    event_runtime = FakeEventRuntime(
        event_id=_EVENT_ID,
        sequence=7,
        raise_on_publish=RuntimeError("event-boom"),
    )
    snapshot_service = FakeSnapshotService(snapshot=_FAKE_SNAPSHOT)
    service = _build_service(
        conn,
        repository=repo,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )

    async def fake_persist(conn_arg: Any, **kwargs: Any) -> StableDocumentFreezePersistenceResult:
        return _freeze_result_from_plan(kwargs["plan"])

    with patch(
        "app.services.reader_orchestration.stable_ready_input_application_service.persist_stable_document_freeze_plan",
        new=fake_persist,
    ):
        with pytest.raises(StableReadyInputApplicationError) as excinfo:
            _freeze(service, text=_english_paragraph(multiplier=2))

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert snapshot_service.load_calls == []
    assert conn._last_transaction is not None
    assert conn._last_transaction.rolled_back is True


def test_snapshot_reload_error_after_commit_is_wrapped_with_cause() -> None:
    conn = _FakeConn()
    repo = FakeRepository()
    event_runtime = FakeEventRuntime(event_id=_EVENT_ID, sequence=7)
    snapshot_service = FakeSnapshotService(
        snapshot=_FAKE_SNAPSHOT,
        raise_on_load=LookupError("snapshot-missing"),
    )
    service = _build_service(
        conn,
        repository=repo,
        event_runtime=event_runtime,
        snapshot_service=snapshot_service,
    )

    async def fake_persist(conn_arg: Any, **kwargs: Any) -> StableDocumentFreezePersistenceResult:
        return _freeze_result_from_plan(kwargs["plan"])

    with patch(
        "app.services.reader_orchestration.stable_ready_input_application_service.persist_stable_document_freeze_plan",
        new=fake_persist,
    ):
        with pytest.raises(StableReadyInputApplicationError) as excinfo:
            _freeze(service, text=_english_paragraph(multiplier=2))

    assert isinstance(excinfo.value.__cause__, LookupError)
    assert conn._last_transaction is not None
    assert conn._last_transaction.committed is True
    assert len(event_runtime.publish_calls) == 1
    assert len(snapshot_service.load_calls) == 1
