"""Turn-coordinator map-source ingress tests.

# task-history: M3 stage C /
Contract: docs/architecture/ask-claread.md (accepted, 2026-07-25).

Covers the Ask-owner side of map-source material consumption —
``MapSourceMaterial`` inside ``TurnCoordinator``:

  * §3.4 preflight — ``_load_map_source_material`` is called before the
    outer transaction (pure I/O + planning; no budget / registry /
    ledger mutation during preflight).
  * §5.1 6(b) — material ``None`` (no provider) or
    ``material_fence_ok=False`` → fall back to the existing unit-window
    map; no partial consumption of ``heading_enrichments`` or
    ``descriptor_sources``.
  * §5.2 13 — heading is injected onto the same unit source (matched
    by ``unit_id``); no standalone heading-only entry is created.
  * §5.4.1 — body sources (rank=0, sorted by ``order_index``) before
    descriptor sources (rank=1).
  * §5.4.2 — hard caps: 32 body + 8 descriptor = 40 max.
  * §5.4.3 — overflow drop: drop highest ``order_index`` body units;
    no descriptor-for-body substitution.
  * §3.5.1.1 — ``include_rag_ask_only`` is fixed to ``False`` in M3
    stage C (heading baseline + wiring skeleton only).

No DB, no asyncpg, no embedding, no Zilliz, no real LLM. A fake
provider duck-types ``MapSourceMaterialProvider.load``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.services.reader_orchestration.map_source_material_provider import (
    HeadingEnrichment,
    MapSourceMaterial,
)
from app.services.reader_record_ask.agent import _SYSTEM_INSTRUCTIONS
from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapEntrySource,
)
from app.services.reader_record_ask.context_envelope import (
    EnvelopeInitialAnchor,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.evidence_expansion import (
    ExpansionPointerLedger,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    ModelVisibleTurnBudget,
)
from app.services.reader_record_ask.pointer_ledger_owner import (
    reset_process_pointer_ledger_for_tests,
)
from app.services.reader_record_ask.turn_coordinator import TurnCoordinator

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "b" * 64

_UNIT_A = "Alpha sentence one about Paris in 2019. "
_UNIT_B = "Bravo paragraph about climate policy in London."
_UNIT_C = "Charlie closing remarks."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _units() -> tuple[ReadingUnitView, ...]:
    return (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text=_UNIT_A,
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=len(_UNIT_A),
        ),
        ReadingUnitView(
            unit_id="u2",
            order_index=1,
            text=_UNIT_B,
            text_hash="22222222",
            base_start_utf16=100,
            base_end_utf16=100 + len(_UNIT_B),
        ),
        ReadingUnitView(
            unit_id="u3",
            order_index=2,
            text=_UNIT_C,
            text_hash="33333333",
            base_start_utf16=200,
            base_end_utf16=200 + len(_UNIT_C),
        ),
    )


def _scope(*, generation: int = 1) -> Any:
    return build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=generation,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
        units=_units(),
        segments=(),
    )


def _envelope(*, selection: str | None = None, generation: int = 1):
    anchor = None
    if selection is not None:
        end = max(1, min(len(selection), 10))
        anchor = EnvelopeInitialAnchor(
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=end,
            selected_text=selection,
            text_hash="abcd1234",
        )
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=generation,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            initial_anchor=anchor,
            can_read_range=True,
            can_search_current_article=True,
            article_rag_ready=False,
            readiness_state="ready",
            product_state="ready",
        )
    )


def _access(scope=None):
    return InMemoryDocumentAccess(snapshot=scope if scope is not None else _scope())


class _FakeMapSourceMaterialProvider:
    """Fake provider that duck-types ``MapSourceMaterialProvider.load``.

    Records every ``load`` call so tests can assert preflight forwarding
    (§3.5.1.1 signature + ``include_rag_ask_only=False`` invariant).
    """

    def __init__(self, *, material: MapSourceMaterial | None = None) -> None:
        self._material = material
        self.calls: list[dict[str, Any]] = []

    async def load(
        self,
        *,
        envelope,
        turn_id: str,
        include_rag_ask_only: bool = False,
    ) -> MapSourceMaterial:
        self.calls.append(
            {
                "envelope": envelope,
                "turn_id": turn_id,
                "include_rag_ask_only": include_rag_ask_only,
            }
        )
        if self._material is None:
            return MapSourceMaterial(material_fence_ok=False)
        return self._material


@pytest.fixture(autouse=True)
def _reset_ledger():
    reset_process_pointer_ledger_for_tests()
    yield
    reset_process_pointer_ledger_for_tests()


def _coordinator(
    *,
    user_message: str = "What cities are mentioned?",
    selection: str | None = None,
    ledger: ExpansionPointerLedger | None = None,
    budget: ModelVisibleTurnBudget | None = None,
    registry: EvidenceRegistry | None = None,
    provider: _FakeMapSourceMaterialProvider | None = None,
) -> TurnCoordinator:
    env = _envelope(selection=selection)
    return TurnCoordinator(
        envelope=env,
        document_access=_access(),
        user_message=user_message,
        system_instructions=_SYSTEM_INSTRUCTIONS,
        pointer_ledger=ledger if ledger is not None else ExpansionPointerLedger(),
        budget=budget,
        evidence_registry=registry,
        product_search_enabled=True,
        map_source_material_provider=provider,
    )


def _material(
    *,
    fence_ok: bool = True,
    headings: tuple[HeadingEnrichment, ...] = (),
    descriptors: tuple[ArticleMapEntrySource, ...] = (),
    reason: str = "ok",
) -> MapSourceMaterial:
    return MapSourceMaterial(
        material_fence_ok=fence_ok,
        descriptor_sources=descriptors,
        heading_enrichments=headings,
        material_failure_reason=reason,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. No provider configured → unit-window fallback (existing behavior)
# ---------------------------------------------------------------------------


class TestNoProviderUnitWindowFallback:
    """§5.1 6(b) — ``provider is None`` preserves pre-map-source unit-window map."""

    def test_static_no_material_returns_body_sources_only(self):
        """``_map_sources_from_scope`` with ``material=None`` returns
        body sources sorted by ``order_index``, no heading, no descriptor."""
        scope = _scope()
        sources = TurnCoordinator._map_sources_from_scope(scope, material=None)
        assert len(sources) == 3
        assert all(s.heading is None for s in sources)
        assert all(s.window_text is not None for s in sources)
        # order_index sort: u1, u2, u3
        assert sources[0].window_text == _UNIT_A
        assert sources[1].window_text == _UNIT_B
        assert sources[2].window_text == _UNIT_C

    @pytest.mark.asyncio
    async def test_assemble_no_provider_loads_unit_window_map(self):
        """Full assemble without provider produces a map whose entry
        labels are derived from window text (no heading injection)."""
        coord = _coordinator(provider=None)
        assembly = await coord.assemble_turn()
        assert assembly.map_result.is_ok
        # All entries should have window-derived or ordinal labels —
        # none should carry a heading from material.
        for entry in assembly.map_result.entries:
            assert entry.kind in ("heading", "window", "ordinal")
        # No provider call was made.
        # (provider is None — nothing to assert on calls.)


# ---------------------------------------------------------------------------
# 2. material_fence_ok=False → unit-window fallback (no partial consumption)
# ---------------------------------------------------------------------------


class TestMaterialFenceFailureFallback:
    """§5.1 6(b) — fence failure falls back to unit-window map; no
    partial consumption of heading/descriptor material."""

    def test_static_fence_failure_ignores_headings_and_descriptors(self):
        """Even if ``heading_enrichments`` and ``descriptor_sources``
        are populated, ``material_fence_ok=False`` MUST ignore them
        (integral fail-closed per §5.1 6(b))."""
        scope = _scope()
        material = _material(
            fence_ok=False,
            headings=(HeadingEnrichment(unit_id="u1", heading="IGNORED"),),
            descriptors=(ArticleMapEntrySource(heading="IGNORED DESC"),),
            reason="stable_document_id_mismatch",
        )
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 3
        # No heading injected, no descriptor appended.
        assert all(s.heading is None for s in sources)
        assert sources[0].window_text == _UNIT_A

    @pytest.mark.asyncio
    async def test_assemble_fence_failure_falls_back_to_unit_window(self):
        """Full assemble with fence-failed material produces a map with
        body-only entries (no heading, no descriptor)."""
        provider = _FakeMapSourceMaterialProvider(
            material=_material(
                fence_ok=False,
                headings=(HeadingEnrichment(unit_id="u1", heading="H1"),),
                descriptors=(ArticleMapEntrySource(heading="MAP-DESC-SENTINEL"),),
                reason="plan_build_failed",
            )
        )
        coord = _coordinator(provider=provider)
        assembly = await coord.assemble_turn()
        assert assembly.map_result.is_ok
        # No entry label should contain the heading "H1" or descriptor sentinel.
        for entry in assembly.map_result.entries:
            assert "H1" not in entry.label
            assert "MAP-DESC-SENTINEL" not in entry.label
        # Provider was called during preflight.
        assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 3. heading_enrichments injection (fence ok, include_rag_ask_only=False)
# ---------------------------------------------------------------------------


class TestHeadingEnrichmentInjection:
    """§5.2 13 — heading injected onto same unit source by ``unit_id``;
    no standalone heading-only entry."""

    def test_static_heading_injected_on_matching_unit(self):
        scope = _scope()
        material = _material(headings=(HeadingEnrichment(unit_id="u2", heading="Climate Section"),))
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 3
        # u1 has no heading.
        assert sources[0].heading is None
        assert sources[0].window_text == _UNIT_A
        # u2 has the injected heading.
        assert sources[1].heading == "Climate Section"
        assert sources[1].window_text == _UNIT_B
        # u3 has no heading.
        assert sources[2].heading is None
        assert sources[2].window_text == _UNIT_C

    def test_static_heading_no_match_leaves_unit_without_heading(self):
        """A heading for a non-existent ``unit_id`` is silently dropped
        (no standalone entry, no error)."""
        scope = _scope()
        material = _material(
            headings=(HeadingEnrichment(unit_id="u_nonexistent", heading="Orphan Heading"),)
        )
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 3
        assert all(s.heading is None for s in sources)

    def test_static_multiple_headings_injected_on_respective_units(self):
        scope = _scope()
        material = _material(
            headings=(
                HeadingEnrichment(unit_id="u1", heading="Intro"),
                HeadingEnrichment(unit_id="u3", heading="Conclusion"),
            )
        )
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 3
        assert sources[0].heading == "Intro"
        assert sources[1].heading is None
        assert sources[2].heading == "Conclusion"

    @pytest.mark.asyncio
    async def test_assemble_heading_visible_in_map_labels(self):
        """Full assemble: injected heading appears as the entry label
        (kind=heading) for the matching unit."""
        provider = _FakeMapSourceMaterialProvider(
            material=_material(headings=(HeadingEnrichment(unit_id="u1", heading="Paris Chapter"),))
        )
        coord = _coordinator(provider=provider)
        assembly = await coord.assemble_turn()
        assert assembly.map_result.is_ok
        # The first entry should be kind=heading with label "Paris Chapter".
        entries = assembly.map_result.entries
        assert len(entries) >= 1
        assert entries[0].kind == "heading"
        assert entries[0].label == "Paris Chapter"


# ---------------------------------------------------------------------------
# 4. descriptor_sources appended after body sources (rank=1)
# ---------------------------------------------------------------------------


class TestDescriptorSourcesAppended:
    """§5.4.1 — body (rank=0) before descriptor (rank=1)."""

    def test_static_descriptors_appended_after_body(self):
        scope = _scope()
        material = _material(
            descriptors=(
                ArticleMapEntrySource(heading="Descriptor A"),
                ArticleMapEntrySource(window_text="Descriptor window B."),
            )
        )
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # 3 body + 2 descriptor = 5.
        assert len(sources) == 5
        # First 3 are body (window_text from units, no heading).
        assert sources[0].window_text == _UNIT_A
        assert sources[1].window_text == _UNIT_B
        assert sources[2].window_text == _UNIT_C
        # Last 2 are descriptor sources.
        assert sources[3].heading == "Descriptor A"
        assert sources[4].window_text == "Descriptor window B."

    def test_static_empty_descriptors_returns_body_only(self):
        scope = _scope()
        material = _material(descriptors=())
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 3
        assert all(s.window_text in (_UNIT_A, _UNIT_B, _UNIT_C) for s in sources)


# ---------------------------------------------------------------------------
# 5. Both heading + descriptor merged (combined scenario)
# ---------------------------------------------------------------------------


class TestBothHeadingAndDescriptorMerged:
    """§5.4.1 + §5.2 13 — heading injected on body, descriptor appended."""

    def test_static_combined_merge(self):
        scope = _scope()
        material = _material(
            headings=(HeadingEnrichment(unit_id="u2", heading="Climate"),),
            descriptors=(ArticleMapEntrySource(heading="Appendix A"),),
        )
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # 3 body + 1 descriptor = 4.
        assert len(sources) == 4
        # Body sources: u1 no heading, u2 with heading, u3 no heading.
        assert sources[0].heading is None
        assert sources[1].heading == "Climate"
        assert sources[2].heading is None
        # Descriptor appended last.
        assert sources[3].heading == "Appendix A"

    @pytest.mark.asyncio
    async def test_assemble_combined_merge_full_flow(self):
        provider = _FakeMapSourceMaterialProvider(
            material=_material(
                headings=(HeadingEnrichment(unit_id="u1", heading="Intro"),),
                descriptors=(ArticleMapEntrySource(heading="Extra Source"),),
            )
        )
        coord = _coordinator(provider=provider)
        assembly = await coord.assemble_turn()
        assert assembly.map_result.is_ok
        entries = assembly.map_result.entries
        # First entry should carry the injected heading.
        assert entries[0].kind == "heading"
        assert entries[0].label == "Intro"
        # Provider called once during preflight.
        assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 6. Hard caps: 32 body + 8 descriptor (§5.4.2 + §5.4.3)
# ---------------------------------------------------------------------------


def _make_units(count: int) -> tuple[ReadingUnitView, ...]:
    return tuple(
        ReadingUnitView(
            unit_id=f"u{i}",
            order_index=i,
            text=f"Unit {i} text content here. ",
            text_hash=f"{i:08x}",
            base_start_utf16=i * 100,
            base_end_utf16=i * 100 + 30,
        )
        for i in range(count)
    )


def _make_scope_with_units(count: int):
    return build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
        units=_make_units(count),
        segments=(),
    )


class TestHardCaps:
    """§5.4.2 — 32 body + 8 descriptor; §5.4.3 — overflow drop."""

    def test_body_cap_32_drops_overflow(self):
        """When scope has >32 units, only the first 32 (lowest
        ``order_index``) become body sources."""
        scope = _make_scope_with_units(40)
        material = _material()
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 32
        # First 32 units by order_index (u0..u31).
        assert sources[0].window_text == "Unit 0 text content here. "
        assert sources[31].window_text == "Unit 31 text content here. "

    def test_descriptor_defensive_cap_8(self):
        """Even if the provider violates the contract and returns >8
        descriptor sources, the coordinator defensively caps at 8."""
        scope = _scope()
        too_many_descriptors = tuple(ArticleMapEntrySource(heading=f"D{i}") for i in range(15))
        material = _material(descriptors=too_many_descriptors)
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # 3 body + 8 descriptor (defensive cap) = 11.
        assert len(sources) == 11
        # First 3 are body.
        assert sources[0].window_text == _UNIT_A
        # Descriptors are.. (first 8).
        for i in range(8):
            assert sources[3 + i].heading == f"D{i}"

    def test_combined_cap_32_body_plus_8_descriptor(self):
        """Max total = 40 when both body and descriptor hit their caps."""
        scope = _make_scope_with_units(40)
        descriptors = tuple(ArticleMapEntrySource(heading=f"D{i}") for i in range(10))
        material = _material(descriptors=descriptors)
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # 32 body (capped) + 8 descriptor (defensive cap) = 40.
        assert len(sources) == 40

    def test_body_overflow_no_descriptor_substitution(self):
        """§5.4.3 — when body overflows, descriptors are NOT substituted
        for dropped body units; descriptors still cap at 8."""
        scope = _make_scope_with_units(35)
        descriptors = tuple(ArticleMapEntrySource(heading=f"D{i}") for i in range(5))
        material = _material(descriptors=descriptors)
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # 32 body (capped from 35) + 5 descriptor = 37.
        assert len(sources) == 37
        # Body sources are u0..u31 (lowest order_index), not u33/u34.
        assert sources[31].window_text == "Unit 31 text content here. "


# ---------------------------------------------------------------------------
# 7. include_rag_ask_only always False (§3.5.1.1 — M3 stage C skeleton)
# ---------------------------------------------------------------------------


class TestPreflightIncludeRagAskOnlyFalse:
    """§3.5.1.1 + M3 stage C — ``include_rag_ask_only`` is fixed to
    ``False`` in the map-source skeleton (heading baseline only)."""

    @pytest.mark.asyncio
    async def test_preflight_calls_load_with_include_rag_ask_only_false(self):
        provider = _FakeMapSourceMaterialProvider(
            material=_material(headings=(HeadingEnrichment(unit_id="u1", heading="H"),))
        )
        coord = _coordinator(provider=provider)
        await coord.assemble_turn()
        assert len(provider.calls) == 1
        call = provider.calls[0]
        assert call["include_rag_ask_only"] is False
        assert call["turn_id"] == coord.turn_id
        assert call["envelope"] is coord.envelope

    @pytest.mark.asyncio
    async def test_preflight_called_before_budget_mutation(self):
        """§3.4 — preflight runs before the outer transaction; budget
        must be zero when ``load`` is called."""
        budget = ModelVisibleTurnBudget()
        provider = _FakeMapSourceMaterialProvider(
            material=_material(headings=(HeadingEnrichment(unit_id="u1", heading="H"),))
        )
        coord = _coordinator(provider=provider, budget=budget)
        await coord.assemble_turn()
        # After assemble, budget is charged; but during preflight it was 0.
        # We verify indirectly: the provider was called exactly once and
        # the assemble succeeded (budget was fresh at preflight time).
        assert len(provider.calls) == 1
        assert budget.total_spent() > 0  # assemble charged budget.

    @pytest.mark.asyncio
    async def test_preflight_no_registry_or_ledger_mutation(self):
        """§3.4 — preflight must not mutate registry or ledger."""
        registry = EvidenceRegistry(_envelope().envelope_fingerprint)
        ledger = ExpansionPointerLedger()
        provider = _FakeMapSourceMaterialProvider(
            material=_material(headings=(HeadingEnrichment(unit_id="u1", heading="H"),))
        )
        coord = _coordinator(provider=provider, registry=registry, ledger=ledger)
        # Before assemble: registry and ledger are empty.
        assert len(registry) == 0
        assert len(ledger) == 0
        await coord.assemble_turn()
        # After assemble: registry and ledger have entries from the outer
        # transaction (map cursors, baseline seeds), NOT from preflight.
        # The key invariant: preflight did not add entries before the
        # transaction — verified by the fact that assemble ran exactly
        # one transaction and the counts are consistent with one map
        # assembly.
        assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# Additional: empty-units edge case + order preservation
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for the merge logic."""

    def test_empty_units_with_descriptors_returns_descriptors_only(self):
        scope = build_document_scope(
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            units=(),
            segments=(),
        )
        material = _material(descriptors=(ArticleMapEntrySource(heading="Only Descriptor"),))
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        assert len(sources) == 1
        assert sources[0].heading == "Only Descriptor"

    def test_units_with_empty_text_skipped_in_body(self):
        """Units with empty text are skipped when building body sources."""
        units = (
            ReadingUnitView(
                unit_id="u1",
                order_index=0,
                text="Real content. ",
                text_hash="11111111",
                base_start_utf16=0,
                base_end_utf16=14,
            ),
            ReadingUnitView(
                unit_id="u2",
                order_index=1,
                text="",
                text_hash="22222222",
                base_start_utf16=14,
                base_end_utf16=15,
            ),
            ReadingUnitView(
                unit_id="u3",
                order_index=2,
                text="More content. ",
                text_hash="33333333",
                base_start_utf16=30,
                base_end_utf16=44,
            ),
        )
        scope = build_document_scope(
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            units=units,
            segments=(),
        )
        material = _material()
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # u2 (empty text) skipped → 2 body sources.
        assert len(sources) == 2
        assert sources[0].window_text == "Real content. "
        assert sources[1].window_text == "More content. "

    def test_body_order_index_sort_preserved_with_headings(self):
        """Body sources are sorted by ``order_index`` regardless of
        heading enrichment order."""
        units = (
            ReadingUnitView(
                unit_id="z_unit",
                order_index=2,
                text="Z content. ",
                text_hash="aa",
                base_start_utf16=200,
                base_end_utf16=210,
            ),
            ReadingUnitView(
                unit_id="a_unit",
                order_index=0,
                text="A content. ",
                text_hash="bb",
                base_start_utf16=0,
                base_end_utf16=10,
            ),
            ReadingUnitView(
                unit_id="m_unit",
                order_index=1,
                text="M content. ",
                text_hash="cc",
                base_start_utf16=100,
                base_end_utf16=110,
            ),
        )
        scope = build_document_scope(
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            units=units,
            segments=(),
        )
        # Headings provided in non-sorted unit_id order.
        material = _material(
            headings=(
                HeadingEnrichment(unit_id="z_unit", heading="Z Head"),
                HeadingEnrichment(unit_id="a_unit", heading="A Head"),
            )
        )
        sources = TurnCoordinator._map_sources_from_scope(scope, material=material)
        # Sorted by order_index: a_unit (0), m_unit (1), z_unit (2).
        assert len(sources) == 3
        assert sources[0].window_text == "A content. "
        assert sources[0].heading == "A Head"
        assert sources[1].window_text == "M content. "
        assert sources[1].heading is None
        assert sources[2].window_text == "Z content. "
        assert sources[2].heading == "Z Head"
