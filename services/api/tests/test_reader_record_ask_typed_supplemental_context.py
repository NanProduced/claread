"""Supplemental typed-context contract tests.

Locks the Ask-owned supplemental typed context seam:

- verbatim LaTeX (math_blocks / inline_math) enters the model context in a
  stable, deterministic order;
- priority: selection hit -> visible range -> remaining source order;
- deterministic item/char hard caps with explicit truncation records;
- identity fence fail-closed on stable document / base / hash / generation
  mismatch;
- image items expose structural metadata only (alt/title + block locator);
  raw/effective URLs never surface and no OCR / visual claims are made;
- code blocks, canonical text, units, T/V/G/S and RAG paths are untouched;
- typed provenance never mints article citation handles ([1][2] /
  ``evh_``) and is not a SourceEvidenceDescriptor;
- an empty typed payload leaves the existing Ask prompt byte-identical.

Offline only: no DB, no provider, no network.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)
from app.services.reader_orchestration.stable_document_query_service import (
    StableDocumentProjectionBlock,
)
from app.services.reader_record_ask.context_envelope import (
    EnvelopeInitialAnchor,
    EnvelopeVisibleRange,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    ReadingUnitView,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    ModelViewRenderer,
    ModelVisibleTurnBudget,
)
from app.services.reader_record_ask.turn_prompt import (
    account_partition_equals_first_surface,
    mint_turn_frame_prompt_capability,
)
from app.services.reader_record_ask.typed_supplemental_context import (
    TYPED_SUPPLEMENTAL_MAX_CHARS,
    TYPED_SUPPLEMENTAL_MAX_ITEMS,
    TypedImageItem,
    TypedInlineMathItem,
    TypedMathBlockItem,
    TypedSupplementalContextIdentityError,
    assemble_typed_supplemental_view,
    build_typed_supplemental_context,
)

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_SHA = "c" * 64

_UNIT_A = "Alpha sentence one about Paris in 2019. "
_UNIT_B = "Bravo paragraph about climate policy in London."
_UNIT_C = "Charlie closing remarks."


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


def _envelope(
    *,
    anchor_base_span: tuple[int, int] | None = None,
    visible_order_span: tuple[int, int] | None = None,
    generation: int = 1,
    sha: str = _SHA,
    doc: UUID | None = _DOC,
):
    anchor = None
    if anchor_base_span is not None:
        anchor = EnvelopeInitialAnchor(
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=5,
            selected_text="Alpha",
            text_hash="abcd1234",
            base_start_utf16=anchor_base_span[0],
            base_end_utf16=anchor_base_span[1],
        )
    visible_range = None
    if visible_order_span is not None:
        visible_range = EnvelopeVisibleRange(
            start_unit_order_index=visible_order_span[0],
            end_unit_order_index=visible_order_span[1],
        )
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=generation,
            stable_document_id=doc,
            base_content_sha256=sha,
            initial_anchor=anchor,
            visible_range=visible_range,
            readiness_state="ready",
            product_state="ready",
        )
    )


def _block(
    order: int,
    payload: dict | None = None,
    *,
    block_type: str = "paragraph",
    span: tuple[int, int] | None = None,
    block_id: str | None = None,
) -> StableDocumentProjectionBlock:
    return StableDocumentProjectionBlock(
        block_id=block_id or f"b{order}",
        parent_block_id=None,
        order_index=order,
        block_type=block_type,
        text_content="x",
        payload=payload or {},
        source_refs={},
        quality={},
        canonical_text_start_utf16=span[0] if span else None,
        canonical_text_end_utf16=span[1] if span else None,
        interpretation_policy={},
    )


def _projection(
    blocks=(),
    *,
    generation: int = 1,
    sha: str = _SHA,
    doc: UUID = _DOC,
    record_id: UUID = _RECORD,
    base_id: UUID = _BASE,
):
    return SimpleNamespace(
        reading_record_id=record_id,
        record_generation=generation,
        active_base_id=base_id,
        base=SimpleNamespace(content_sha256=sha),
        stable_document=SimpleNamespace(stable_document_id=doc),
        blocks=tuple(blocks),
    )


def _build(proj=None, *, envelope=None, **kwargs):
    return build_typed_supplemental_context(
        projection=proj if proj is not None else _projection(),
        envelope=envelope if envelope is not None else _envelope(),
        units=_units(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Verbatim math + stable source order
# ---------------------------------------------------------------------------


def test_math_items_enter_model_context_verbatim_in_stable_order() -> None:
    proj = _projection(
        blocks=(
            _block(
                0,
                {
                    "math_blocks": [{"latex": "E = mc^2", "display": True}],
                    "inline_math": [
                        {"latex": "$a*b*c$", "display": False, "before_utf16": 5},
                        {"latex": "\\|A-B\\|", "display": False, "before_utf16": 20},
                    ],
                },
                span=(0, 40),
            ),
            _block(
                1,
                {
                    "inline_math": [
                        {"latex": "x_1+x_2", "display": False, "before_utf16": 0}
                    ]
                },
                span=(40, 60),
                block_type="heading",
            ),
        )
    )
    payload = _build(proj, envelope=_envelope())

    kinds = [type(item) for item in payload.items]
    assert kinds == [
        TypedMathBlockItem,
        TypedInlineMathItem,
        TypedInlineMathItem,
        TypedInlineMathItem,
    ]
    # Verbatim LaTeX survives byte-for-byte.
    assert payload.items[0].latex == "E = mc^2"
    assert payload.items[1].latex == "$a*b*c$"
    assert payload.items[2].latex == "\\|A-B\\|"
    assert payload.items[3].latex == "x_1+x_2"
    # Locators: block id + order index + array ordinal; inline extras.
    assert payload.items[0].block_id == "b0"
    assert payload.items[0].order_index == 0
    assert payload.items[1].ordinal == 0
    assert payload.items[1].before_utf16 == 5
    assert payload.items[1].display is False
    assert payload.items[2].ordinal == 1
    assert payload.items[2].before_utf16 == 20
    assert payload.items[3].block_id == "b1"
    assert payload.items[3].order_index == 1

    renderer = ModelViewRenderer()
    section, charged = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=renderer,
    )
    assert charged > 0
    for latex in ("E = mc^2", "$a*b*c$", "\\|A-B\\|", "x_1+x_2"):
        assert latex in section


def test_default_caps_are_deterministic_module_constants() -> None:
    assert TYPED_SUPPLEMENTAL_MAX_ITEMS == 24
    assert TYPED_SUPPLEMENTAL_MAX_CHARS == 4000


def test_callers_can_tighten_but_not_expand_hard_caps() -> None:
    blocks = tuple(
        _block(i, {"math_blocks": [{"latex": f"L{i}-" + ("x" * 180)}]})
        for i in range(30)
    )
    payload = _build(
        _projection(blocks),
        max_items=TYPED_SUPPLEMENTAL_MAX_ITEMS * 10,
        max_chars=TYPED_SUPPLEMENTAL_MAX_CHARS * 10,
    )
    section, _ = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=ModelViewRenderer(),
    )

    assert len(payload.items) <= TYPED_SUPPLEMENTAL_MAX_ITEMS
    assert len(section) <= TYPED_SUPPLEMENTAL_MAX_CHARS
    assert payload.truncation.dropped_item_count > 0


# ---------------------------------------------------------------------------
# 2. Priority: selection hit -> visible range -> source order
# ---------------------------------------------------------------------------


def test_priority_selection_then_visible_then_source_order() -> None:
    proj = _projection(
        blocks=(
            _block(0, {"math_blocks": [{"latex": "SEL", "display": False}]}, span=(0, 10)),
            _block(
                1,
                {"math_blocks": [{"latex": "VIS", "display": False}]},
                span=(120, 140),
            ),
            _block(
                2,
                {"math_blocks": [{"latex": "FAR", "display": False}]},
                span=(300, 320),
            ),
            _block(3, {"math_blocks": [{"latex": "SEL2", "display": False}]}, span=(4, 12)),
        )
    )
    envelope = _envelope(
        anchor_base_span=(0, 8),
        visible_order_span=(1, 1),  # u2 only: base 100..~132
    )
    payload = _build(proj, envelope=envelope)
    latexes = [item.latex for item in payload.items]
    assert latexes == ["SEL", "SEL2", "VIS", "FAR"]


def test_selection_and_visible_absent_falls_back_to_pure_source_order() -> None:
    proj = _projection(
        blocks=(
            _block(5, {"math_blocks": [{"latex": "L5", "display": False}]}),
            _block(6, {"math_blocks": [{"latex": "L6", "display": False}]}),
        )
    )
    payload = _build(proj, envelope=_envelope())
    assert [item.latex for item in payload.items] == ["L5", "L6"]


# ---------------------------------------------------------------------------
# 3. Deterministic truncation over budget
# ---------------------------------------------------------------------------


def test_item_cap_truncation_is_deterministic_and_explicit() -> None:
    blocks = tuple(
        _block(i, {"math_blocks": [{"latex": f"L{i}", "display": False}]})
        for i in range(6)
    )
    kwargs = dict(max_items=3)
    payload_a = _build(_projection(blocks), **kwargs)
    payload_b = _build(_projection(blocks), **kwargs)
    # Deterministic: identical payloads across runs.
    assert payload_a == payload_b
    assert len(payload_a.items) == 3
    assert [item.latex for item in payload_a.items] == ["L0", "L1", "L2"]
    # Explicit truncation record.
    assert payload_a.truncation.dropped_item_count == 3
    assert payload_a.truncation.dropped_char_count > 0

    renderer = ModelViewRenderer()
    section, _charged = assemble_typed_supplemental_view(
        payload_a,
        budget=ModelVisibleTurnBudget(),
        renderer=renderer,
    )
    assert "truncated" in section
    assert "3" in section  # dropped count surfaced explicitly
    assert "L5" not in section

    # No truncation -> no marker.
    full_payload = _build(_projection(blocks[:2]))
    assert full_payload.truncation.dropped_item_count == 0
    section_full, _ = assemble_typed_supplemental_view(
        full_payload,
        budget=ModelVisibleTurnBudget(),
        renderer=ModelViewRenderer(),
    )
    assert "truncated" not in section_full


def test_char_cap_truncates_on_prefix_boundary_only() -> None:
    blocks = tuple(
        _block(i, {"math_blocks": [{"latex": f"LONGER-LATEX-{i}", "display": False}]})
        for i in range(4)
    )
    payload = _build(_projection(blocks), max_chars=120)
    assert len(payload.items) < 4
    assert payload.truncation.dropped_item_count == 4 - len(payload.items)
    # Kept items are exactly the priority-order prefix.
    all_payload = _build(_projection(blocks))
    expected_prefix = [item.latex for item in all_payload.items][: len(payload.items)]
    assert [item.latex for item in payload.items] == expected_prefix


def test_char_cap_bounds_the_complete_rendered_section() -> None:
    blocks = tuple(
        _block(
            i,
            {"math_blocks": [{"latex": f"FORMULA-{i}-" + ("x" * 40)}]},
        )
        for i in range(8)
    )
    payload = _build(_projection(blocks), max_chars=520)
    section, charged = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=ModelViewRenderer(),
    )

    assert section
    assert len(section) <= 520
    assert charged == len(section)
    assert payload.truncation.dropped_item_count > 0


def test_frozen_markdown_payload_flows_into_consumer_without_translation() -> None:
    parsed = MarkdownSourceParser().parse(
        "Prose $x^2 + y^2$ end.\n\n$$\nE = mc^2\n$$\n\n![Chart](https://example.com/chart.png)"
    )
    blocks = tuple(
        _block(
            block.order_index,
            dict(block.payload_json),
            block_type=block.block_type,
            block_id=block.block_id,
        )
        for block in parsed.blocks
    )

    payload = _build(_projection(blocks))
    section, _ = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=ModelViewRenderer(),
    )

    assert "x^2 + y^2" in section
    assert "E = mc^2" in section
    assert "Chart" in section
    assert "https://example.com/chart.png" not in section


# ---------------------------------------------------------------------------
# 4. Identity fence fail-closed
# ---------------------------------------------------------------------------


def test_identity_happy_path_returns_payload() -> None:
    payload = _build()
    assert payload.items == ()
    assert payload.truncation.dropped_item_count == 0


@pytest.mark.parametrize(
    ("mutate"),
    ["generation", "base_id", "sha", "record_id", "stable_doc"],
)
def test_identity_mismatch_fails_closed(mutate: str) -> None:
    kwargs: dict = {}
    if mutate == "generation":
        kwargs["generation"] = 2
    elif mutate == "base_id":
        kwargs["base_id"] = UUID("55555555-5555-5555-5555-555555555555")
    elif mutate == "sha":
        kwargs["sha"] = "d" * 64
    elif mutate == "record_id":
        kwargs["record_id"] = UUID("66666666-6666-6666-6666-666666666666")
    elif mutate == "stable_doc":
        kwargs["doc"] = UUID("77777777-7777-7777-7777-777777777777")
    with pytest.raises(TypedSupplementalContextIdentityError):
        _build(_projection(**kwargs))


def test_identity_allows_envelope_without_stable_document_id() -> None:
    envelope = _envelope(doc=None)
    payload = _build(_projection(), envelope=envelope)
    assert payload.items == ()


# ---------------------------------------------------------------------------
# 5. Image metadata safety: no URLs, no OCR claims
# ---------------------------------------------------------------------------


def test_image_metadata_exposes_no_urls_and_no_visual_claims() -> None:
    proj = _projection(
        blocks=(
            _block(
                0,
                {
                    "source_url": "https://cdn.example.com/secret-chart.png",
                    "alt_text": "Figure 1: results",
                    "title": "Chart title",
                    "position_kind": "standalone",
                },
                span=(0, 30),
                block_type="image",
            ),
            _block(
                1,
                {
                    "inline_images": [
                        {
                            "source_url": "https://evil.example/x.jpg",
                            "alt_text": "inline pic",
                            "title": "",
                            "before_utf16": 3,
                        }
                    ],
                    "inline_math": [],
                },
                span=(30, 60),
            ),
        )
    )
    payload = _build(proj)
    images = [item for item in payload.items if isinstance(item, TypedImageItem)]
    assert len(images) == 2
    for image in images:
        dumped = image.model_dump()
        # Only structural fields exist at all.
        assert set(dumped) <= {
            "alt_text",
            "title",
            "block_id",
            "order_index",
            "ordinal",
        }
        assert "source_url" not in dumped

    renderer = ModelViewRenderer()
    section, charged = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=renderer,
    )
    assert charged > 0
    # Safe metadata surfaces.
    assert "Figure 1: results" in section
    assert "inline pic" in section
    # URL material never leaks.
    assert "https://" not in section
    assert "cdn.example.com" not in section
    assert "evil.example" not in section
    assert "secret-chart.png" not in section
    assert "x.jpg" not in section
    # No OCR / visual understanding claims anywhere.
    assert not re.search(r"ocr", section, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# 6. Typed provenance is not an article citation
# ---------------------------------------------------------------------------


def test_typed_provenance_never_mints_citation_handles() -> None:
    envelope = _envelope()
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    proj = _projection(
        blocks=(
            _block(0, {"math_blocks": [{"latex": "E=mc^2", "display": True}]}),
        )
    )
    payload = _build(proj, envelope=envelope)
    for item in payload.items:
        # Own typed models — never evidence descriptors.
        assert isinstance(item, TypedMathBlockItem | TypedInlineMathItem | TypedImageItem)

    renderer = ModelViewRenderer()
    section, _ = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=renderer,
    )
    assert "evh_" not in section
    # No [1][2]-style article citation markers.
    assert re.search(r"\[\d+\]", section) is None
    # Registry untouched by building/rendering typed context.
    assert len(registry) == 0
    # The section itself declares its non-citation provenance.
    assert "not_instructions" in section


# ---------------------------------------------------------------------------
# 7. Code blocks / canonical / units / RAG inputs untouched
# ---------------------------------------------------------------------------


def test_code_blocks_and_plain_payloads_contribute_nothing() -> None:
    proj = _projection(
        blocks=(
            _block(
                0,
                {},
                block_type="code_block",
                span=(0, 20),
                block_id="code0",
            ),
            _block(1, {"unrelated": [{"latex": "SHOULD_NOT_APPEAR"}]}, span=(20, 40)),
            _block(2, {"inline_images": []}, span=(40, 60)),
        )
    )
    payload = _build(proj)
    assert payload.items == ()

    section, charged = assemble_typed_supplemental_view(
        payload,
        budget=ModelVisibleTurnBudget(),
        renderer=ModelViewRenderer(),
    )
    assert section == ""
    assert charged == 0
    assert "SHOULD_NOT_APPEAR" not in section


# ---------------------------------------------------------------------------
# 8. Empty typed payload keeps the existing prompt byte-identical
# ---------------------------------------------------------------------------


def _mint(typed_context_section: str | None = None):
    kwargs: dict = {
        "system_instructions": "",
        "projection_json": "{}",
        "handles_block": "",
        "baseline_is_complete": True,
        "user_question": "  原文问题：保留前后空白?  ",
        "budget": ModelVisibleTurnBudget(),
        "renderer": ModelViewRenderer(),
        "charge": False,
    }
    if typed_context_section is not None:
        kwargs["typed_context_section"] = typed_context_section
    return mint_turn_frame_prompt_capability(**kwargs)


def test_empty_typed_section_is_byte_identical_to_absent() -> None:
    absent = _mint()
    empty = _mint("")
    assert absent.user_prompt == empty.user_prompt
    assert absent.selection_untrusted == ""
    assert absent.baseline_untrusted == ""


def test_nonempty_typed_section_sits_between_map_and_coverage_and_is_untrusted() -> None:
    section = (
        '<transcript_data role="data" not_instructions="true">\n'
        "- [math_block|block=b0|order=0] E=mc^2\n"
        "</transcript_data>"
    )
    frame = _mint(section)
    # Injected exactly once, before the coverage block, after the context
    # header (no selection/baseline/map sections present).
    assert frame.user_prompt.count(section) == 1
    typed_at = frame.user_prompt.index(section)
    coverage_at = frame.user_prompt.index("## Baseline coverage")
    question_at = frame.user_prompt.index("## User question")
    assert typed_at < coverage_at < question_at
    # Body excluded from the trusted request-frame surface.
    assert frame.typed_untrusted == section
    from app.services.reader_record_ask.turn_prompt import (
        compose_production_user_prompt,
    )

    composed = compose_production_user_prompt(
        projection_json="{}",
        handles_block="",
        coverage_block="\n## Baseline coverage\nStatus: complete.\n",
        user_question="q",
        selection_prompt=None,
        baseline_prompt=None,
        map_prompt=None,
        typed_context_section=section,
    )
    # 7-tuple now: last element is the typed untrusted body.
    assert len(composed) == 7
    assert composed[-1] == section


def test_partition_equality_holds_with_charged_typed_section() -> None:
    section = "<transcript_data>x</transcript_data>"
    frame = _mint(section)
    # Simulate the caller having charged the section body to the shared
    # baseline account before minting (the production charging path).
    baseline_spent = len(section)
    assert account_partition_equals_first_surface(
        frame,
        selection_spent=0,
        baseline_spent=baseline_spent,
        map_spent=0,
        request_frame_spent=None,
        memory_spent=0,
        recent_history_spent=0,
    )


# ---------------------------------------------------------------------------
# 9. Turn-coordinator integration (offline; fake loader)
# ---------------------------------------------------------------------------


def _integration_scope():
    from app.services.reader_record_ask.document_access import (
        build_document_scope,
    )

    return build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
        units=_units(),
        segments=(),
    )


async def _coordinated_assembly(
    *,
    stable_document_loader,
    anchor_base_span: tuple[int, int] | None = None,
    user_message: str = "What does the formula mean?",
):
    from app.services.reader_record_ask.agent import _SYSTEM_INSTRUCTIONS
    from app.services.reader_record_ask.document_access import (
        InMemoryDocumentAccess,
    )
    from app.services.reader_record_ask.evidence_expansion import (
        ExpansionPointerLedger,
    )
    from app.services.reader_record_ask.turn_coordinator import TurnCoordinator

    coordinator = TurnCoordinator(
        envelope=_envelope(anchor_base_span=anchor_base_span),
        document_access=InMemoryDocumentAccess(snapshot=_integration_scope()),
        user_message=user_message,
        system_instructions=_SYSTEM_INSTRUCTIONS,
        pointer_ledger=ExpansionPointerLedger(),
        stable_document_loader=stable_document_loader,
    )
    return await coordinator.assemble_turn()


@pytest.mark.asyncio
async def test_coordinator_without_loader_has_no_typed_section() -> None:
    assembly = await _coordinated_assembly(stable_document_loader=None)
    assert assembly.baseline_context.is_injected
    assert "## Supplemental typed context" not in assembly.user_prompt


@pytest.mark.asyncio
async def test_coordinator_injects_typed_section_from_loader() -> None:
    proj = _projection(
        blocks=(
            _block(
                0,
                {"math_blocks": [{"latex": "E = mc^2", "display": True}]},
                span=(0, 40),
            ),
            _block(
                1,
                {
                    "inline_math": [
                        {"latex": "$a*b*c$", "display": False, "before_utf16": 3}
                    ]
                },
                span=(40, 60),
            ),
        )
    )

    async def loader() -> object:
        return proj

    assembly = await _coordinated_assembly(
        stable_document_loader=loader,
        anchor_base_span=(0, 8),
    )
    assert assembly.baseline_context.is_injected
    # Section injected exactly once with verbatim LaTeX; selection-hit
    # block (tier 0) renders before the source-order inline one.
    assert assembly.user_prompt.count("## Supplemental typed context") == 1
    assert "E = mc^2" in assembly.user_prompt
    assert "$a*b*c$" in assembly.user_prompt
    assert assembly.turn_frame.typed_untrusted != ""
    assert assembly.user_prompt.count("E = mc^2") == 1


@pytest.mark.asyncio
async def test_coordinator_fail_closed_on_projection_identity_mismatch() -> None:
    async def loader() -> object:
        return _projection(generation=2)

    assembly = await _coordinated_assembly(stable_document_loader=loader)
    # Fail-closed: non-runnable assembly with a typed failure reason.
    assert not assembly.baseline_context.is_injected
    reason = assembly.baseline_context.baseline_failure_reason or ""
    assert "supplemental typed context" in reason
    assert assembly.user_prompt == "What does the formula mean?"


@pytest.mark.asyncio
async def test_coordinator_loader_io_failure_is_fail_soft_absent() -> None:
    async def loader() -> object:
        raise RuntimeError("simulated projection load failure")

    assembly = await _coordinated_assembly(stable_document_loader=loader)
    assert assembly.baseline_context.is_injected
    assert "## Supplemental typed context" not in assembly.user_prompt
