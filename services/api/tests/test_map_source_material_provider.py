"""Tests for MapSourceMaterialProvider (M3 stage C, C1 + B3 heading enrichment).

Contract: docs/initiatives/reader-agentic-orchestration/modules/
ask-claread-agentic-product-runtime-contract.md (accepted, 2026-07-25).

Covers:
  * §3.5.1.1 — 唯一规范签名:
      async def load(
          self,
          *,
          envelope: ReadingRecordAskContextEnvelope,
          turn_id: str,
          include_rag_ask_only: bool = False,
      ) -> MapSourceMaterial
    The signature MUST NOT accept ``EnvelopeIdentity`` or a standalone
    ``user_id`` parameter (v5 §3.5.1.1).
  * §5.1 3 — default OFF semantics (B3 heading-enabled baseline):
    ``include_rag_ask_only=False`` does NOT parse descriptors
    (``descriptor_sources=()``) but DOES call ``plan_service.
    build_index_plan`` to extract B3 heading enrichments
    (§3.5.2 / §4.2 / §5.2 — heading belongs to ``main_reading``).
  * §5.1 26 — provider authorization subject uniqueness: both
    ``record_id`` and ``user_id`` for ``build_index_plan`` come from
    the same ``envelope``; no alternative ``EnvelopeIdentity`` /
    standalone ``user_id`` path exists.
  * §3.4 / §5.1 6(b) / §5.1 28 — material fence failure fallback:
      - envelope.stable_document_id is None
        → material_failure_reason="envelope_stable_document_id_missing"
      - envelope.base_content_sha256 is None
        → material_failure_reason="envelope_base_content_sha256_missing"
      - plan.stable_document_id != envelope.stable_document_id
        → material_failure_reason="stable_document_id_mismatch"
      - plan.content_sha256 != envelope.base_content_sha256
        → material_failure_reason="base_content_sha256_mismatch"
    On any material fence failure: ``material_fence_ok=False`` AND
    ``descriptor_sources=()`` AND ``heading_enrichments=()`` (整份
    fail-closed; 不部分采纳 material 中的合法条目). Ask owner falls
    back to existing unit-window map.
  * Exception handling:
      - LookupError (record missing / ownership mismatch) →
        material_failure_reason="plan_build_failed"
      - ArticleRagIndexPlanError (stale / inactive / mismatched) →
        material_failure_reason="plan_build_failed"
      - Infrastructure exceptions (e.g. RuntimeError, ConnectionError)
        propagate up — NOT caught, NOT converted to fence failure.
  * §5.1 25 — candidate semantics: ``descriptor_sources`` are
    candidates; ``MapSourceMaterial`` carries no visible-retention
    promise field. (Visible retention is left to Ask owner's
    ``assemble_article_map`` cost-fit, not to this provider.)
  * Frozen dataclass shape: ``MapSourceMaterial`` is frozen+slots;
    ``material_failure_reason`` is a fixed safe enum (no raw exception
    text interpolation, no caller-supplied value).
  * Module contract: ``__all__`` exposes ``HeadingEnrichment``,
    ``MapSourceMaterial``, ``MapSourceMaterialProvider``,
    ``MaterialFailureReason``.
  * §5.1 9 — no RAG provenance: ``MapSourceMaterial`` does not carry
    ``index_run_id`` / ``plan_content_sha256`` fields.
  * B3 heading enrichment (§4.2 / §5.2):
      - ``include_rag_ask_only=False`` still populates
        ``heading_enrichments`` (B3 baseline).
      - ``include_rag_ask_only=True`` populates both
        ``heading_enrichments`` and ``descriptor_sources``.
      - material fence failure → ``heading_enrichments=()``.
      - empty heading plan → ``heading_enrichments=()``.
      - heading ↔ unit association by canonical order.
      - ``HeadingEnrichment`` is frozen+slots.

No DB, no asyncpg, no embedding, no Zilliz. A fake plan service
implements the ``_PlanServiceProtocol`` structural protocol.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagCitationRef,
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
    ArticleRagIndexPlanError,
)
from app.services.reader_orchestration.map_source_material_provider import (
    HeadingEnrichment,
    MapSourceMaterial,
    MapSourceMaterialProvider,
    MaterialFailureReason,
)
from app.services.reader_orchestration.source_evidence_descriptor import (
    DESCRIPTOR_HARD_CAP,
)
from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapEntrySource,
)
from app.services.reader_record_ask.context_envelope import (
    EnvelopeCapabilityState,
    ReadingRecordAskContextEnvelope,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RECORD_ID = UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_BASE_ID = UUID("33333333-3333-3333-3333-333333333333")
_STABLE_DOC_ID = UUID("44444444-4444-4444-4444-444444444444")
_OTHER_STABLE_DOC_ID = UUID("55555555-5555-5555-5555-555555555555")
_OTHER_USER_ID = UUID("66666666-6666-6666-6666-666666666666")
_OTHER_BASE_ID = UUID("77777777-7777-7777-7777-777777777777")

_PLAN_CONTENT_SHA = hashlib.sha256(b"plan-content-stable").hexdigest()
_CANON_TEXT_SHA = hashlib.sha256(b"canonical-text").hexdigest()
_OTHER_PLAN_CONTENT_SHA = hashlib.sha256(b"plan-content-other").hexdigest()
_ENVELOPE_FINGERPRINT = hashlib.sha256(b"envelope-fingerprint-v1").hexdigest()

_TURN_ID = "turn-0001"


# ---------------------------------------------------------------------------
# In-memory envelope / plan / chunk helpers (no DB, no asyncpg, no embedding)
# ---------------------------------------------------------------------------


def _make_capabilities(
    *,
    article_rag_ready: bool = False,
) -> EnvelopeCapabilityState:
    return EnvelopeCapabilityState(
        product_state="default",
        readiness_state="ready",
        has_initial_anchor=False,
        has_visible_range=False,
        can_read_range=True,
        can_search_current_article=True,
        article_rag_ready=article_rag_ready,
    )


def _make_envelope(
    *,
    user_id: UUID = _USER_ID,
    reading_record_id: UUID = _RECORD_ID,
    base_id: UUID = _BASE_ID,
    record_generation: int = 1,
    stable_document_id: UUID | None = _STABLE_DOC_ID,
    base_content_sha256: str | None = _PLAN_CONTENT_SHA,
    article_rag_ready: bool = False,
) -> ReadingRecordAskContextEnvelope:
    return ReadingRecordAskContextEnvelope(
        envelope_version="reading_record_ask_context_envelope_v1",
        envelope_fingerprint=_ENVELOPE_FINGERPRINT,
        user_id=user_id,
        reading_record_id=reading_record_id,
        base_id=base_id,
        record_generation=record_generation,
        stable_document_id=stable_document_id,
        base_content_sha256=base_content_sha256,
        initial_anchor=None,
        visible_range=None,
        capabilities=_make_capabilities(article_rag_ready=article_rag_ready),
    )


def _make_citation(
    *,
    stable_document_id: UUID = _STABLE_DOC_ID,
    base_id: UUID = _BASE_ID,
    block_ids: tuple[str, ...] = ("block-1",),
    canonical_start: int | None = None,
    canonical_end: int | None = None,
) -> ArticleRagCitationRef:
    return ArticleRagCitationRef(
        reading_record_id=_RECORD_ID,
        stable_document_id=stable_document_id,
        base_id=base_id,
        record_generation=1,
        block_ids=block_ids,
        unit_ids=(),
        anchor_segment_ids=(),
        canonical_text_start_utf16=canonical_start,
        canonical_text_end_utf16=canonical_end,
    )


def _rag_ask_metadata(
    *,
    block_type: str,
    block_order_index: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    md: dict[str, Any] = {
        "block_type": block_type,
        "block_order_index": block_order_index,
        "source_scope": "main_reading_text",
        "default_route": "rag_ask_only",
        "chunk_index": 0,
        "has_canonical_offsets": False,
    }
    if extra:
        md.update(extra)
    return md


def _make_chunk(
    chunk_id: str,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    block_ids: tuple[str, ...] = ("block-1",),
) -> ArticleRagIndexChunk:
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagIndexChunk(
        chunk_id=chunk_id,
        citation=_make_citation(block_ids=block_ids),
        source_scope="main_reading_text",
        text=text,
        content_sha256=content_sha,
        embedding_text_sha256=content_sha,
        metadata_json=(
            metadata
            if metadata is not None
            else _rag_ask_metadata(block_type="table_cell")
        ),
    )


def _make_plan(
    *,
    chunks: tuple[ArticleRagIndexChunk, ...] | None = None,
    stable_document_id: UUID = _STABLE_DOC_ID,
    base_id: UUID = _BASE_ID,
    content_sha256: str = _PLAN_CONTENT_SHA,
    record_generation: int = 1,
) -> ArticleRagIndexPlan:
    cs = chunks or (
        _make_chunk(
            "chunk-tc-1",
            "cell-text-1",
            metadata=_rag_ask_metadata(
                block_type="table_cell", block_order_index=0
            ),
            block_ids=("block-tc-1",),
        ),
    )
    return ArticleRagIndexPlan(
        reading_record_id=_RECORD_ID,
        stable_document_id=stable_document_id,
        base_id=base_id,
        record_generation=record_generation,
        content_sha256=content_sha256,
        canonical_text_sha256=_CANON_TEXT_SHA,
        chunks=cs,
    )


def _heading_metadata(
    *,
    block_order_index: int = 0,
) -> dict[str, Any]:
    """Metadata for a ``block_type="heading"`` chunk (main_reading route)."""
    return {
        "block_type": "heading",
        "block_order_index": block_order_index,
        "source_scope": "main_reading_text",
        "default_route": "main_reading",
        "chunk_index": 0,
        "has_canonical_offsets": True,
    }


def _main_reading_metadata(
    *,
    block_order_index: int = 0,
) -> dict[str, Any]:
    """Metadata for a ``block_type="paragraph"`` chunk (main_reading route)."""
    return {
        "block_type": "paragraph",
        "block_order_index": block_order_index,
        "source_scope": "main_reading_text",
        "default_route": "main_reading",
        "chunk_index": 0,
        "has_canonical_offsets": True,
    }


def _make_heading_chunk(
    chunk_id: str,
    text: str,
    *,
    canonical_start: int,
    canonical_end: int,
    block_ids: tuple[str, ...] = ("block-heading",),
    block_order_index: int = 0,
) -> ArticleRagIndexChunk:
    """Build a heading chunk with canonical UTF-16 range set."""
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagIndexChunk(
        chunk_id=chunk_id,
        citation=ArticleRagCitationRef(
            reading_record_id=_RECORD_ID,
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            record_generation=1,
            block_ids=block_ids,
            unit_ids=(),
            anchor_segment_ids=(),
            canonical_text_start_utf16=canonical_start,
            canonical_text_end_utf16=canonical_end,
        ),
        source_scope="main_reading_text",
        text=text,
        content_sha256=content_sha,
        embedding_text_sha256=content_sha,
        metadata_json=_heading_metadata(block_order_index=block_order_index),
    )


def _make_unit_chunk(
    chunk_id: str,
    text: str,
    *,
    unit_ids: tuple[str, ...],
    canonical_start: int,
    canonical_end: int | None = None,
    block_ids: tuple[str, ...] = ("block-unit",),
    block_order_index: int = 0,
) -> ArticleRagIndexChunk:
    """Build a main_reading chunk carrying ``unit_ids`` (a reading unit)."""
    if canonical_end is None:
        canonical_end = canonical_start + len(text)
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ArticleRagIndexChunk(
        chunk_id=chunk_id,
        citation=ArticleRagCitationRef(
            reading_record_id=_RECORD_ID,
            stable_document_id=_STABLE_DOC_ID,
            base_id=_BASE_ID,
            record_generation=1,
            block_ids=block_ids,
            unit_ids=unit_ids,
            anchor_segment_ids=(),
            canonical_text_start_utf16=canonical_start,
            canonical_text_end_utf16=canonical_end,
        ),
        source_scope="main_reading_text",
        text=text,
        content_sha256=content_sha,
        embedding_text_sha256=content_sha,
        metadata_json=_main_reading_metadata(block_order_index=block_order_index),
    )


def _make_plan_with_headings(
    *,
    chunks: tuple[ArticleRagIndexChunk, ...] | None = None,
    stable_document_id: UUID = _STABLE_DOC_ID,
    base_id: UUID = _BASE_ID,
    content_sha256: str = _PLAN_CONTENT_SHA,
) -> ArticleRagIndexPlan:
    """Build a plan with a heading chunk followed by a unit chunk.

    Default layout (canonical order):
      - heading "Chapter 1" at [0, 10)
      - unit "unit-1" starting at 20
    """
    if chunks is None:
        chunks = (
            _make_heading_chunk(
                "c-heading-1",
                "Chapter 1",
                canonical_start=0,
                canonical_end=10,
            ),
            _make_unit_chunk(
                "c-unit-1",
                "Unit body text.",
                unit_ids=("unit-1",),
                canonical_start=20,
            ),
        )
    return ArticleRagIndexPlan(
        reading_record_id=_RECORD_ID,
        stable_document_id=stable_document_id,
        base_id=base_id,
        record_generation=1,
        content_sha256=content_sha256,
        canonical_text_sha256=_CANON_TEXT_SHA,
        chunks=chunks,
    )


# ---------------------------------------------------------------------------
# Fake plan service (implements _PlanServiceProtocol structural protocol)
# ---------------------------------------------------------------------------


class _FakePlanService:
    """Fake plan service for testing ``MapSourceMaterialProvider``.

    Implements the structural protocol ``_PlanServiceProtocol``: a single
    ``async def build_index_plan(*, record_id, user_id,
    include_rag_ask_only=False) -> ArticleRagIndexPlan`` coroutine.

    Records all calls so tests can assert forwarding behaviour
    (§5.1 26 authorization subject uniqueness).

    Modes
    -----
    - ``plan=...``: return the supplied plan.
    - ``exc=...``: raise the supplied exception (LookupError /
      ArticleRagIndexPlanError / infrastructure exception).
    """

    def __init__(
        self,
        *,
        plan: ArticleRagIndexPlan | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self._plan = plan
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def build_index_plan(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        include_rag_ask_only: bool = False,
    ) -> ArticleRagIndexPlan:
        self.calls.append(
            {
                "record_id": record_id,
                "user_id": user_id,
                "include_rag_ask_only": include_rag_ask_only,
            }
        )
        if self._exc is not None:
            raise self._exc
        assert self._plan is not None
        return self._plan


# ---------------------------------------------------------------------------
# 1. Signature contract (§3.5.1.1 — 唯一规范签名)
# ---------------------------------------------------------------------------


class TestLoadSignatureContract:
    """§3.5.1.1 — the only canonical signature for ``load``.

    Signature MUST be:
        async def load(
            self,
            *,
            envelope: ReadingRecordAskContextEnvelope,
            turn_id: str,
            include_rag_ask_only: bool = False,
        ) -> MapSourceMaterial
    """

    def test_load_is_async(self) -> None:
        assert inspect.iscoroutinefunction(MapSourceMaterialProvider.load)

    def test_load_signature_parameters(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        params = sig.parameters
        # self is positional-or-keyword (default in unbound methods).
        assert "self" in params
        # envelope / turn_id / include_rag_ask_only are keyword-only.
        for name in ("envelope", "turn_id", "include_rag_ask_only"):
            assert name in params, f"missing param: {name}"
            p = params[name]
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"{name} must be keyword-only"
            )
        # envelope has no default (required).
        assert params["envelope"].default is inspect.Parameter.empty
        # turn_id has no default (required).
        assert params["turn_id"].default is inspect.Parameter.empty
        # include_rag_ask_only defaults to False (§5.1 3).
        assert params["include_rag_ask_only"].default is False
        # No additional parameters (no envelope_identity, no user_id).
        allowed = {"self", "envelope", "turn_id", "include_rag_ask_only"}
        extras = set(params) - allowed
        assert not extras, f"unexpected params: {extras}"

    def test_load_return_annotation_is_map_source_material(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        # ``from __future__ import annotations`` makes annotations strings;
        # accept either the class object or its name.
        ann = sig.return_annotation
        assert ann is MapSourceMaterial or ann == "MapSourceMaterial", (
            f"return annotation must be MapSourceMaterial; got {ann!r}"
        )

    def test_provider_init_accepts_plan_service_only(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.__init__)
        params = sig.parameters
        assert "self" in params
        assert "plan_service" in params
        # Only self + plan_service (no envelope_identity, no user_id).
        allowed = {"self", "plan_service"}
        extras = set(params) - allowed
        assert not extras, f"unexpected __init__ params: {extras}"

    def test_module_all_exports_match_contract(self) -> None:
        from app.services.reader_orchestration import map_source_material_provider as mod

        assert set(mod.__all__) == {
            "HeadingEnrichment",
            "MapSourceMaterial",
            "MapSourceMaterialProvider",
            "MaterialFailureReason",
        }


# ---------------------------------------------------------------------------
# 2. MapSourceMaterial frozen dataclass shape (§3.5.1.1 / §5.1 9 / §5.1 25)
# ---------------------------------------------------------------------------


class TestMapSourceMaterialShape:
    """``MapSourceMaterial`` is a frozen+slots dataclass with four
    server-only fields. No RAG provenance fields (§5.1 9). No
    visible-retention promise field (§5.1 25)."""

    def test_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(MapSourceMaterial)
        # frozen=True raises FrozenInstanceError on setattr.
        m = MapSourceMaterial(material_fence_ok=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.material_fence_ok = False  # type: ignore[misc]

    def test_is_slots_dataclass(self) -> None:
        # slots=True: no __dict__ on instances.
        m = MapSourceMaterial(material_fence_ok=True)
        assert not hasattr(m, "__dict__")

    def test_field_names_match_contract(self) -> None:
        names = {f.name for f in dataclasses.fields(MapSourceMaterial)}
        assert names == {
            "material_fence_ok",
            "descriptor_sources",
            "heading_enrichments",
            "material_failure_reason",
        }

    def test_descriptor_sources_is_tuple_of_article_map_entry_source(self) -> None:
        # Empty default.
        m = MapSourceMaterial(material_fence_ok=True)
        assert m.descriptor_sources == ()
        # With sources — type-check by constructing one with a real
        # ArticleMapEntrySource (created via the provider pipeline below).
        src = ArticleMapEntrySource(heading="表格单元格", window_text="x")
        m2 = MapSourceMaterial(
            material_fence_ok=True,
            descriptor_sources=(src,),
        )
        assert len(m2.descriptor_sources) == 1
        assert isinstance(m2.descriptor_sources[0], ArticleMapEntrySource)

    def test_heading_enrichments_default_empty(self) -> None:
        """B3 — ``heading_enrichments`` defaults to empty tuple."""
        m = MapSourceMaterial(material_fence_ok=True)
        assert m.heading_enrichments == ()

    def test_heading_enrichments_is_tuple_of_heading_enrichment(self) -> None:
        he = HeadingEnrichment(unit_id="unit-1", heading="Chapter 1")
        m = MapSourceMaterial(
            material_fence_ok=True,
            heading_enrichments=(he,),
        )
        assert len(m.heading_enrichments) == 1
        assert isinstance(m.heading_enrichments[0], HeadingEnrichment)
        assert m.heading_enrichments[0].unit_id == "unit-1"
        assert m.heading_enrichments[0].heading == "Chapter 1"

    def test_heading_enrichment_is_frozen_dataclass(self) -> None:
        """B3 — ``HeadingEnrichment`` is frozen+slots (contract interface)."""
        assert dataclasses.is_dataclass(HeadingEnrichment)
        he = HeadingEnrichment(unit_id="unit-1", heading="Chapter 1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            he.unit_id = "unit-2"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            he.heading = "Chapter 2"  # type: ignore[misc]
        # slots=True: no __dict__ on instances.
        assert not hasattr(he, "__dict__")

    def test_default_failure_reason_is_ok(self) -> None:
        m = MapSourceMaterial(material_fence_ok=True)
        assert m.material_failure_reason == "ok"

    def test_no_rag_provenance_fields(self) -> None:
        """§5.1 9 — descriptor sources must NOT carry RAG citation /
        provenance. ``MapSourceMaterial`` itself must not expose
        ``index_run_id`` or ``plan_content_sha256``."""
        names = {f.name for f in dataclasses.fields(MapSourceMaterial)}
        assert "index_run_id" not in names
        assert "plan_content_sha256" not in names
        assert "rag_substrate_id" not in names

    def test_no_visible_retention_promise_field(self) -> None:
        """§5.1 25 — descriptor is candidate; no field on
        ``MapSourceMaterial`` promises model visibility."""
        names = {f.name for f in dataclasses.fields(MapSourceMaterial)}
        assert "visible_count" not in names
        assert "min_visible" not in names
        assert "retention_quota" not in names


# ---------------------------------------------------------------------------
# 3. Default OFF (§5.1 3 — include_rag_ask_only=False)
# ---------------------------------------------------------------------------


class TestDefaultOff:
    """§5.1 3 / §3.5.2 — default OFF path.

    ``include_rag_ask_only=False`` (the default) does NOT parse
    descriptors (``descriptor_sources=()``) but DOES call
    ``plan_service.build_index_plan`` to extract B3 heading
    enrichments (§4.2 / §5.2 — heading belongs to ``main_reading``,
    populated regardless of opt-in).
    """

    @pytest.mark.anyio
    async def test_default_off_populates_heading_only(self) -> None:
        """§5.1 3 / §3.5.2 — default OFF still calls plan service for
        heading extraction; descriptor_sources remains empty."""
        plan = _make_plan_with_headings()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )  # default include_rag_ask_only=False

        assert material.material_fence_ok is True
        assert material.descriptor_sources == ()
        assert material.material_failure_reason == "ok"
        # B3 — heading is populated even when opt-in is off.
        assert len(material.heading_enrichments) == 1
        assert material.heading_enrichments[0].unit_id == "unit-1"
        assert material.heading_enrichments[0].heading == "Chapter 1"
        # Plan IS called for heading extraction.
        assert len(svc.calls) == 1
        assert svc.calls[0]["include_rag_ask_only"] is False

    @pytest.mark.anyio
    async def test_explicit_false_also_calls_plan_for_heading(self) -> None:
        """§5.1 3 — explicit ``include_rag_ask_only=False`` behaves
        identically to the default."""
        plan = _make_plan_with_headings()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=False,
        )

        assert material.material_fence_ok is True
        assert material.descriptor_sources == ()
        assert material.material_failure_reason == "ok"
        assert len(material.heading_enrichments) == 1
        assert len(svc.calls) == 1
        assert svc.calls[0]["include_rag_ask_only"] is False

    @pytest.mark.anyio
    async def test_default_off_with_no_headings_returns_empty_enrichments(
        self,
    ) -> None:
        """§5.1 3 — default OFF with a plan containing no heading
        chunks returns empty heading_enrichments (no error)."""
        # Plan with only a rag_ask_only chunk (no heading, no unit).
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=False,
        )

        assert material.material_fence_ok is True
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert material.material_failure_reason == "ok"
        assert len(svc.calls) == 1


# ---------------------------------------------------------------------------
# 4. §5.1 26 — provider authorization subject uniqueness
# ---------------------------------------------------------------------------


class TestAuthorizationSubjectUniqueness:
    """§5.1 26 / §3.5.1.1 — both ``record_id`` and ``user_id`` for
    ``build_index_plan`` come from the same ``envelope``. No
    alternative path that accepts ``EnvelopeIdentity`` or a standalone
    ``user_id`` parameter.
    """

    @pytest.mark.anyio
    async def test_record_id_and_user_id_both_from_envelope(self) -> None:
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(
            user_id=_USER_ID,
            reading_record_id=_RECORD_ID,
        )

        await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert len(svc.calls) == 1
        call = svc.calls[0]
        assert call["record_id"] == _RECORD_ID
        assert call["user_id"] == _USER_ID
        assert call["include_rag_ask_only"] is True

    @pytest.mark.anyio
    async def test_record_id_and_user_id_propagate_from_different_uuids(
        self,
    ) -> None:
        """When the envelope carries different UUIDs, those exact UUIDs
        are forwarded — no substitution, no implicit default."""
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(
            user_id=_OTHER_USER_ID,
            reading_record_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            base_id=_OTHER_BASE_ID,
            stable_document_id=_OTHER_STABLE_DOC_ID,
            base_content_sha256=_OTHER_PLAN_CONTENT_SHA,
        )
        # Plan must match envelope identity for material fence to pass.
        plan = _make_plan(
            stable_document_id=_OTHER_STABLE_DOC_ID,
            base_id=_OTHER_BASE_ID,
            content_sha256=_OTHER_PLAN_CONTENT_SHA,
        )
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)

        await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert len(svc.calls) == 1
        call = svc.calls[0]
        assert call["record_id"] == UUID(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
        assert call["user_id"] == _OTHER_USER_ID
        assert call["include_rag_ask_only"] is True

    @pytest.mark.anyio
    async def test_no_standalone_user_id_parameter_on_load(self) -> None:
        """§3.5.1.1 — calling load with a ``user_id`` kwarg must fail
        with TypeError (the signature does not accept it)."""
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        with pytest.raises(TypeError):
            await provider.load(  # type: ignore[call-arg]
                envelope=env,
                turn_id=_TURN_ID,
                include_rag_ask_only=True,
                user_id=_USER_ID,
            )

    @pytest.mark.anyio
    async def test_no_envelope_identity_parameter_on_load(self) -> None:
        """§3.5.1.1 — calling load with ``envelope_identity=`` must
        fail with TypeError (the signature does not accept it)."""
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        with pytest.raises(TypeError):
            await provider.load(  # type: ignore[call-arg]
                envelope=env,
                turn_id=_TURN_ID,
                include_rag_ask_only=True,
                envelope_identity=object(),
            )


# ---------------------------------------------------------------------------
# 5. §3.4 / §5.1 6(b) / §5.1 28 — material fence failure fallback
# ---------------------------------------------------------------------------


class TestMaterialFenceFailureFallback:
    """§3.4 / §5.1 6(b) / §5.1 28 — material fence failure paths.

    Each failure path returns:
      - ``material_fence_ok=False``
      - ``descriptor_sources=()`` (整份 fail-closed; 不部分采纳)
      - ``heading_enrichments=()`` (B3 integral fail-closed — heading
        and descriptor travel on the same material)
      - a fixed safe ``material_failure_reason`` enum value
    Ask owner MUST fall back to existing unit-window map.
    """

    @pytest.mark.anyio
    async def test_envelope_stable_document_id_missing(self) -> None:
        plan = _make_plan_with_headings()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(stable_document_id=None)

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert (
            material.material_failure_reason
            == "envelope_stable_document_id_missing"
        )
        # Fence check happens BEFORE plan build, so no plan call.
        assert svc.calls == []

    @pytest.mark.anyio
    async def test_envelope_base_content_sha256_missing(self) -> None:
        plan = _make_plan_with_headings()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(base_content_sha256=None)

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert (
            material.material_failure_reason
            == "envelope_base_content_sha256_missing"
        )
        # Fence check happens BEFORE plan build, so no plan call.
        assert svc.calls == []

    @pytest.mark.anyio
    async def test_stable_document_id_mismatch(self) -> None:
        # Plan has a different stable_document_id than the envelope.
        plan = _make_plan_with_headings(
            stable_document_id=_OTHER_STABLE_DOC_ID,
        )
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(stable_document_id=_STABLE_DOC_ID)

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert (
            material.material_failure_reason == "stable_document_id_mismatch"
        )
        # Plan build happened (one call), but material was rejected.
        assert len(svc.calls) == 1

    @pytest.mark.anyio
    async def test_base_content_sha256_mismatch(self) -> None:
        # Plan has a different content_sha256 than the envelope's
        # base_content_sha256.
        plan = _make_plan_with_headings(
            content_sha256=_OTHER_PLAN_CONTENT_SHA,
        )
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(base_content_sha256=_PLAN_CONTENT_SHA)

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert (
            material.material_failure_reason
            == "base_content_sha256_mismatch"
        )
        assert len(svc.calls) == 1

    @pytest.mark.anyio
    async def test_material_fence_failure_does_not_partially_consume(
        self,
    ) -> None:
        """§5.1 6(b) / §5.1 28 — material fence failure is integral
        fail-closed. Even when the plan contains MANY descriptor
        candidates AND heading chunks, both ``descriptor_sources`` and
        ``heading_enrichments`` are empty on failure."""
        # Plan has many descriptor candidates AND a heading+unit pair,
        # but stable_document_id mismatches → integral fail-closed.
        chunks = (
            _make_heading_chunk(
                "c-heading-1",
                "Chapter 1",
                canonical_start=0,
                canonical_end=10,
            ),
            _make_unit_chunk(
                "c-unit-1",
                "Unit body text.",
                unit_ids=("unit-1",),
                canonical_start=20,
            ),
        ) + tuple(
            _make_chunk(
                f"c-tc-{i}",
                f"text-{i}",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=i
                ),
                block_ids=(f"block-{i:02d}",),
            )
            for i in range(15)
        )
        plan = _make_plan(
            chunks=chunks,
            stable_document_id=_OTHER_STABLE_DOC_ID,
        )
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(stable_document_id=_STABLE_DOC_ID)

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        # Integral fail-closed — no partial consumption of either
        # descriptor_sources or heading_enrichments.
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()


# ---------------------------------------------------------------------------
# 6. Exception handling (§3.4 / §5.1 6(b))
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    """§3.4 / §5.1 6(b) — exception classification.

    - LookupError (record missing / ownership mismatch) →
      material_failure_reason="plan_build_failed"
    - ArticleRagIndexPlanError (stale / inactive / mismatched) →
      material_failure_reason="plan_build_failed"
    - Infrastructure exceptions (RuntimeError, ConnectionError, etc.)
      propagate up — NOT caught, NOT converted to fence failure.
    """

    @pytest.mark.anyio
    async def test_lookup_error_converted_to_plan_build_failed(self) -> None:
        svc = _FakePlanService(exc=LookupError("record not found"))
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert material.material_failure_reason == "plan_build_failed"
        assert len(svc.calls) == 1

    @pytest.mark.anyio
    async def test_article_rag_index_plan_error_converted_to_plan_build_failed(
        self,
    ) -> None:
        svc = _FakePlanService(
            exc=ArticleRagIndexPlanError("stable document stale")
        )
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.descriptor_sources == ()
        assert material.heading_enrichments == ()
        assert material.material_failure_reason == "plan_build_failed"
        assert len(svc.calls) == 1

    @pytest.mark.anyio
    async def test_runtime_error_propagates(self) -> None:
        """Infrastructure exceptions (RuntimeError) are NOT caught."""
        svc = _FakePlanService(exc=RuntimeError("asyncpg pool down"))
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        with pytest.raises(RuntimeError, match="asyncpg pool down"):
            await provider.load(
                envelope=env,
                turn_id=_TURN_ID,
                include_rag_ask_only=True,
            )

        assert len(svc.calls) == 1

    @pytest.mark.anyio
    async def test_connection_error_propagates(self) -> None:
        """Infrastructure exceptions (ConnectionError) are NOT caught."""
        svc = _FakePlanService(exc=ConnectionError("tcp reset"))
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        with pytest.raises(ConnectionError, match="tcp reset"):
            await provider.load(
                envelope=env,
                turn_id=_TURN_ID,
                include_rag_ask_only=True,
            )

    @pytest.mark.anyio
    async def test_value_error_subclass_not_caught_unless_index_plan_error(
        self,
    ) -> None:
        """A ValueError that is NOT ArticleRagIndexPlanError is an
        infrastructure error and MUST propagate (ArticleRagIndexPlanError
        extends ValueError, but a generic ValueError is not a material
        fence failure)."""
        svc = _FakePlanService(exc=ValueError("unexpected value"))
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        with pytest.raises(ValueError, match="unexpected value"):
            await provider.load(
                envelope=env,
                turn_id=_TURN_ID,
                include_rag_ask_only=True,
            )

    @pytest.mark.anyio
    async def test_no_raw_exception_text_in_material(self) -> None:
        """Project convention: ``material_failure_reason`` is a fixed
        safe enum; raw exception text is NOT interpolated."""
        svc = _FakePlanService(
            exc=LookupError("RECORD-ID-LEAK-RAW-PAYLOAD-SECRET")
        )
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        # Safe enum value, no leak.
        assert material.material_failure_reason == "plan_build_failed"
        # The raw exception text must not appear on any field.
        assert "RECORD-ID-LEAK-RAW-PAYLOAD-SECRET" not in material.material_failure_reason
        for src in material.descriptor_sources:
            assert "RECORD-ID-LEAK-RAW-PAYLOAD-SECRET" not in (src.heading or "")
            assert "RECORD-ID-LEAK-RAW-PAYLOAD-SECRET" not in (
                src.window_text or ""
            )
        for he in material.heading_enrichments:
            assert "RECORD-ID-LEAK-RAW-PAYLOAD-SECRET" not in he.unit_id
            assert "RECORD-ID-LEAK-RAW-PAYLOAD-SECRET" not in he.heading


# ---------------------------------------------------------------------------
# 7. Happy path (material fence passes → descriptor candidates produced)
# ---------------------------------------------------------------------------


class TestHappyPath:
    """When material fence passes, ``descriptor_sources`` is populated
    by ``build_descriptor_candidates`` (C3). The provider does NOT call
    ``ledger.issue`` or ``assemble_article_map``."""

    @pytest.mark.anyio
    async def test_fence_pass_returns_candidates(self) -> None:
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is True
        assert material.material_failure_reason == "ok"
        # _make_plan() default has one table_cell chunk → one candidate.
        assert len(material.descriptor_sources) == 1
        src = material.descriptor_sources[0]
        assert isinstance(src, ArticleMapEntrySource)
        # §5.4.4 label: table_cell without column_name → 表格单元格.
        assert src.heading == "表格单元格"
        # §3.3: expansion_text = "表格单元格: cell-text-1".
        assert src.window_text == "表格单元格: cell-text-1"

    @pytest.mark.anyio
    async def test_fence_pass_with_empty_plan_returns_empty_sources(
        self,
    ) -> None:
        """A plan with no rag_ask_only chunks (only main_reading) →
        material_fence_ok=True but descriptor_sources empty."""
        # Build a plan with a main_reading chunk (does not qualify).
        md: dict[str, Any] = {
            "block_type": "paragraph",
            "block_order_index": 0,
            "source_scope": "main_reading_text",
            "default_route": "main_reading",
            "chunk_index": 0,
            "has_canonical_offsets": True,
        }
        chunk = _make_chunk(
            "c-main",
            "main text",
            metadata=md,
            block_ids=("block-main",),
        )
        # main_reading chunks have canonical range set; reset citation.
        chunk = ArticleRagIndexChunk(
            chunk_id=chunk.chunk_id,
            citation=ArticleRagCitationRef(
                reading_record_id=_RECORD_ID,
                stable_document_id=_STABLE_DOC_ID,
                base_id=_BASE_ID,
                record_generation=1,
                block_ids=("block-main",),
                unit_ids=(),
                anchor_segment_ids=(),
                canonical_text_start_utf16=0,
                canonical_text_end_utf16=10,
            ),
            source_scope="main_reading_text",
            text="main text",
            content_sha256=chunk.content_sha256,
            embedding_text_sha256=chunk.embedding_text_sha256,
            metadata_json=md,
        )
        plan = _make_plan(chunks=(chunk,))
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is True
        assert material.material_failure_reason == "ok"
        # No qualifying chunk → no descriptor.
        assert material.descriptor_sources == ()

    @pytest.mark.anyio
    async def test_descriptor_hard_cap_enforced_via_provider(self) -> None:
        """§5.4.2 — provider output respects the descriptor hard cap 8
        (delegated to ``build_descriptor_candidates``)."""
        chunks = tuple(
            _make_chunk(
                f"c-tc-{i}",
                f"text-{i}",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=i
                ),
                block_ids=(f"block-{i:02d}",),
            )
            for i in range(15)  # 15 > 8
        )
        plan = _make_plan(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is True
        assert len(material.descriptor_sources) == DESCRIPTOR_HARD_CAP == 8


# ---------------------------------------------------------------------------
# 8. Candidate semantics (§5.1 25)
# ---------------------------------------------------------------------------


class TestCandidateSemantics:
    """§5.1 25 / §3.5.1.3 — descriptor sources are CANDIDATES. The
    provider does NOT promise model visibility; visibility is decided
    by ``assemble_article_map`` cost-fit (Ask owner's territory).
    """

    @pytest.mark.anyio
    async def test_material_carries_no_visibility_field(self) -> None:
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        # No visibility / quota / retention field on MapSourceMaterial.
        names = {f.name for f in dataclasses.fields(material)}
        for forbidden in (
            "visible_count",
            "min_visible",
            "retention_quota",
            "guaranteed_visible",
            "is_visible",
        ):
            assert forbidden not in names

    @pytest.mark.anyio
    async def test_descriptor_sources_are_pure_article_map_entry_source(
        self,
    ) -> None:
        """§5.1 5 / §5.1 9 — descriptor candidates carry no server-side
        metadata (parent_context / block_id / block_type / footnote_id /
        source_content_sha256 / index_run_id / plan_content_sha256).
        They are pure ``ArticleMapEntrySource`` (heading + window_text)."""
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        for src in material.descriptor_sources:
            # ArticleMapEntrySource only has heading + window_text.
            field_names = {f.name for f in dataclasses.fields(src)}
            assert field_names == {"heading", "window_text"}
            # No server-side metadata fields.
            for forbidden in (
                "parent_context",
                "block_id",
                "block_type",
                "footnote_id",
                "source_content_sha256",
                "index_run_id",
                "plan_content_sha256",
                "reading_record_id",
                "stable_document_id",
            ):
                assert forbidden not in field_names


# ---------------------------------------------------------------------------
# 9. Envelope field validation (Pydantic contract surface)
# ---------------------------------------------------------------------------


class TestEnvelopeFieldValidation:
    """Sanity-check that the envelope contract surface used by the
    provider still enforces its Pydantic constraints. This is a
    regression guard — if ``context_envelope.py`` loosens its field
    validators, the provider's material fence could silently weaken.
    """

    def test_envelope_stable_document_id_accepts_none(self) -> None:
        env = _make_envelope(stable_document_id=None)
        assert env.stable_document_id is None

    def test_envelope_base_content_sha256_accepts_none(self) -> None:
        env = _make_envelope(base_content_sha256=None)
        assert env.base_content_sha256 is None

    def test_envelope_base_content_sha256_rejects_non_hex(self) -> None:
        with pytest.raises(ValidationError):
            _make_envelope(base_content_sha256="not-a-sha256-hex")

    def test_envelope_base_content_sha256_rejects_uppercase(self) -> None:
        # Pattern: ^[0-9a-f]{64}$ — uppercase F is rejected.
        upper = "F" * 64
        with pytest.raises(ValidationError):
            _make_envelope(base_content_sha256=upper)

    def test_envelope_base_content_sha256_accepts_lowercase_hex(self) -> None:
        lower = "a" * 64
        env = _make_envelope(base_content_sha256=lower)
        assert env.base_content_sha256 == lower

    def test_envelope_fingerprint_rejects_non_hex(self) -> None:
        with pytest.raises(ValidationError):
            ReadingRecordAskContextEnvelope(
                envelope_version="reading_record_ask_context_envelope_v1",
                envelope_fingerprint="not-hex",
                user_id=_USER_ID,
                reading_record_id=_RECORD_ID,
                base_id=_BASE_ID,
                record_generation=1,
                stable_document_id=_STABLE_DOC_ID,
                base_content_sha256=_PLAN_CONTENT_SHA,
                capabilities=_make_capabilities(),
            )


# ---------------------------------------------------------------------------
# 10. Module-level smoke tests (import / contract)
# ---------------------------------------------------------------------------


def test_module_does_not_import_ledger_or_assemble() -> None:
    """§3.5.1.1 — the provider module must NOT import ``ledger``,
    ``assemble_article_map``, or any cursor mutation seam."""
    from app.services.reader_orchestration import map_source_material_provider as mod

    src = inspect.getsource(mod)
    # Forbidden import / call surfaces.
    assert "ledger" not in src, "provider must not import or call ledger"
    assert "assemble_article_map" not in src, (
        "provider must not import or call assemble_article_map"
    )
    assert "registry" not in src, (
        "provider must not import or call cursor registry"
    )
    # Sanity: the module DOES define the expected symbols.
    assert hasattr(mod, "MapSourceMaterial")
    assert hasattr(mod, "MapSourceMaterialProvider")
    assert hasattr(mod, "MaterialFailureReason")
    assert hasattr(mod, "HeadingEnrichment")


def test_material_failure_reason_literal_covers_all_safe_values() -> None:
    """``MaterialFailureReason`` is a fixed safe enum Literal covering
    exactly the values used by the provider. No caller-supplied value
    can leak through."""
    from typing import get_args

    values = set(get_args(MaterialFailureReason))
    assert values == {
        "ok",
        "envelope_stable_document_id_missing",
        "envelope_base_content_sha256_missing",
        "stable_document_id_mismatch",
        "base_content_sha256_mismatch",
        "plan_build_failed",
    }


# ---------------------------------------------------------------------------
# 11. B3 heading enrichment (§4.2 / §5.2)
# ---------------------------------------------------------------------------


class TestHeadingEnrichment:
    """B3 — heading enrichment coverage (§4.2 / §5.2).

    Tests the heading extraction and unit-association logic in
    isolation, covering:
      - ``include_rag_ask_only=False`` populates ``heading_enrichments``
      - ``include_rag_ask_only=True`` populates both heading + descriptor
      - material fence failure → ``heading_enrichments=()``
      - empty heading plan → ``heading_enrichments=()``
      - heading ↔ unit association by canonical order (multi-unit)
      - ``HeadingEnrichment`` is frozen+slots
      - edge cases: whitespace heading, None range, heading after last
        unit, multiple headings for same unit
    """

    @pytest.mark.anyio
    async def test_default_off_populates_heading_enrichments(self) -> None:
        """§3.5.2 / §4.2 — ``include_rag_ask_only=False`` still
        populates ``heading_enrichments`` (B3 baseline)."""
        plan = _make_plan_with_headings()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )  # default False

        assert material.material_fence_ok is True
        assert material.descriptor_sources == ()
        assert len(material.heading_enrichments) == 1
        assert material.heading_enrichments[0] == HeadingEnrichment(
            unit_id="unit-1", heading="Chapter 1"
        )

    @pytest.mark.anyio
    async def test_opt_in_populates_both_heading_and_descriptor(self) -> None:
        """§3.5.2 / §5.4 — ``include_rag_ask_only=True`` populates
        BOTH ``heading_enrichments`` AND ``descriptor_sources``."""
        # Plan with a heading+unit pair AND a rag_ask_only chunk.
        chunks = (
            _make_heading_chunk(
                "c-heading-1",
                "Chapter 1",
                canonical_start=0,
                canonical_end=10,
            ),
            _make_unit_chunk(
                "c-unit-1",
                "Unit body text.",
                unit_ids=("unit-1",),
                canonical_start=20,
            ),
            _make_chunk(
                "c-tc-1",
                "cell-text-1",
                metadata=_rag_ask_metadata(
                    block_type="table_cell", block_order_index=0
                ),
                block_ids=("block-tc-1",),
            ),
        )
        plan = _make_plan_with_headings(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is True
        # Heading enrichment populated.
        assert len(material.heading_enrichments) == 1
        assert material.heading_enrichments[0].unit_id == "unit-1"
        assert material.heading_enrichments[0].heading == "Chapter 1"
        # Descriptor also populated.
        assert len(material.descriptor_sources) == 1
        assert isinstance(material.descriptor_sources[0], ArticleMapEntrySource)

    @pytest.mark.anyio
    async def test_fence_failure_heading_enrichments_empty(self) -> None:
        """§5.1 6(b) — material fence failure → heading_enrichments=()
        (integral fail-closed)."""
        plan = _make_plan_with_headings()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope(stable_document_id=None)

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is False
        assert material.heading_enrichments == ()
        assert material.descriptor_sources == ()

    @pytest.mark.anyio
    async def test_empty_heading_plan_returns_empty_enrichments(self) -> None:
        """Plan with no heading chunks → heading_enrichments=() (no
        error, even when include_rag_ask_only=True)."""
        # Plan with only a rag_ask_only chunk (no heading, no unit).
        plan = _make_plan()
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
            include_rag_ask_only=True,
        )

        assert material.material_fence_ok is True
        assert material.heading_enrichments == ()

    @pytest.mark.anyio
    async def test_heading_unit_association_multi_unit(self) -> None:
        """§4.2 / §5.2 — heading is associated with the first unit
        whose canonical_start > heading's canonical_end (multi-unit
        scenario).

        Layout:
          heading "Chapter 1" at [0, 10)
          unit-1 starts at 20  → paired with "Chapter 1"
          heading "Chapter 2" at [50, 60)
          unit-2 starts at 70  → paired with "Chapter 2"
        """
        chunks = (
            _make_heading_chunk(
                "c-h1",
                "Chapter 1",
                canonical_start=0,
                canonical_end=10,
            ),
            _make_unit_chunk(
                "c-u1",
                "Unit 1 body.",
                unit_ids=("unit-1",),
                canonical_start=20,
            ),
            _make_heading_chunk(
                "c-h2",
                "Chapter 2",
                canonical_start=50,
                canonical_end=60,
            ),
            _make_unit_chunk(
                "c-u2",
                "Unit 2 body.",
                unit_ids=("unit-2",),
                canonical_start=70,
            ),
        )
        plan = _make_plan_with_headings(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )

        assert material.material_fence_ok is True
        assert len(material.heading_enrichments) == 2
        assert material.heading_enrichments[0] == HeadingEnrichment(
            unit_id="unit-1", heading="Chapter 1"
        )
        assert material.heading_enrichments[1] == HeadingEnrichment(
            unit_id="unit-2", heading="Chapter 2"
        )

    @pytest.mark.anyio
    async def test_heading_without_following_unit_is_dropped(self) -> None:
        """A heading at the end of the document (no unit after it)
        produces no enrichment for that heading."""
        chunks = (
            _make_unit_chunk(
                "c-u1",
                "Unit 1 body.",
                unit_ids=("unit-1",),
                canonical_start=0,
            ),
            # Heading after the only unit — no unit follows it.
            _make_heading_chunk(
                "c-h-orphan",
                "Orphan Heading",
                canonical_start=50,
                canonical_end=60,
            ),
        )
        plan = _make_plan_with_headings(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )

        assert material.material_fence_ok is True
        assert material.heading_enrichments == ()

    @pytest.mark.anyio
    async def test_multiple_headings_for_same_unit_only_first_wins(
        self,
    ) -> None:
        """§5.2 13 — heading 只补到同一 unit source, 不新增独立
        heading entry. When two headings precede the same unit, only
        the closest (first in canonical order that pairs) wins.

        Layout:
          heading "Chapter A" at [0, 10)
          heading "Chapter B" at [20, 30)
          unit-1 starts at 40  → paired with "Chapter A" (first heading
                                whose end < unit start; "Chapter B" also
                                precedes unit-1 but unit-1 is already
                                paired)
        """
        chunks = (
            _make_heading_chunk(
                "c-h-a",
                "Chapter A",
                canonical_start=0,
                canonical_end=10,
            ),
            _make_heading_chunk(
                "c-h-b",
                "Chapter B",
                canonical_start=20,
                canonical_end=30,
            ),
            _make_unit_chunk(
                "c-u1",
                "Unit 1 body.",
                unit_ids=("unit-1",),
                canonical_start=40,
            ),
        )
        plan = _make_plan_with_headings(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )

        assert material.material_fence_ok is True
        # Only one enrichment — unit-1 paired with "Chapter A" (the
        # first heading in canonical order whose end < unit_start).
        # "Chapter B" is skipped because unit-1 is already paired.
        assert len(material.heading_enrichments) == 1
        assert material.heading_enrichments[0] == HeadingEnrichment(
            unit_id="unit-1", heading="Chapter A"
        )

    @pytest.mark.anyio
    async def test_whitespace_only_heading_text_is_skipped(self) -> None:
        """A heading chunk with whitespace-only text is skipped (no
        enrichment produced for it)."""
        chunks = (
            _make_heading_chunk(
                "c-h-ws",
                "   \n\t  ",
                canonical_start=0,
                canonical_end=10,
            ),
            _make_unit_chunk(
                "c-u1",
                "Unit 1 body.",
                unit_ids=("unit-1",),
                canonical_start=20,
            ),
        )
        plan = _make_plan_with_headings(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )

        assert material.material_fence_ok is True
        assert material.heading_enrichments == ()

    @pytest.mark.anyio
    async def test_heading_with_none_canonical_range_is_skipped(self) -> None:
        """A heading chunk with None canonical range is skipped (no
        enrichment produced for it)."""
        # Build a heading chunk with None range — _make_heading_chunk
        # requires int args, so construct manually.
        content_sha = hashlib.sha256(b"heading text").hexdigest()
        heading_chunk = ArticleRagIndexChunk(
            chunk_id="c-h-none-range",
            citation=ArticleRagCitationRef(
                reading_record_id=_RECORD_ID,
                stable_document_id=_STABLE_DOC_ID,
                base_id=_BASE_ID,
                record_generation=1,
                block_ids=("block-heading",),
                unit_ids=(),
                anchor_segment_ids=(),
                canonical_text_start_utf16=None,
                canonical_text_end_utf16=None,
            ),
            source_scope="main_reading_text",
            text="Heading Text",
            content_sha256=content_sha,
            embedding_text_sha256=content_sha,
            metadata_json=_heading_metadata(),
        )
        unit_chunk = _make_unit_chunk(
            "c-u1",
            "Unit 1 body.",
            unit_ids=("unit-1",),
            canonical_start=20,
        )
        plan = _make_plan_with_headings(chunks=(heading_chunk, unit_chunk))
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )

        assert material.material_fence_ok is True
        assert material.heading_enrichments == ()

    @pytest.mark.anyio
    async def test_no_units_in_plan_returns_empty_enrichments(self) -> None:
        """Plan with heading chunks but no unit chunks → empty
        heading_enrichments (no units to pair with)."""
        chunks = (
            _make_heading_chunk(
                "c-h1",
                "Chapter 1",
                canonical_start=0,
                canonical_end=10,
            ),
        )
        plan = _make_plan_with_headings(chunks=chunks)
        svc = _FakePlanService(plan=plan)
        provider = MapSourceMaterialProvider(plan_service=svc)
        env = _make_envelope()

        material = await provider.load(
            envelope=env,
            turn_id=_TURN_ID,
        )

        assert material.material_fence_ok is True
        assert material.heading_enrichments == ()

    def test_heading_enrichment_is_frozen(self) -> None:
        """B3 — ``HeadingEnrichment`` is a frozen dataclass (cannot
        mutate fields after construction)."""
        he = HeadingEnrichment(unit_id="unit-1", heading="Chapter 1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            he.unit_id = "unit-2"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            he.heading = "Chapter 2"  # type: ignore[misc]

    def test_heading_enrichment_is_slots(self) -> None:
        """B3 — ``HeadingEnrichment`` is a slots dataclass (no
        ``__dict__`` on instances)."""
        he = HeadingEnrichment(unit_id="unit-1", heading="Chapter 1")
        assert not hasattr(he, "__dict__")
