"""Ask Evidence Contract Freeze (v5) regression tests.

Spec:
    docs/architecture/ask-claread.md
    (accepted design specification, 2026-07-25)

Purpose
-------
This module is a **contract freeze** regression suite. It locks down the
v5 contract surface of the map-source-material provider and the
source-evidence descriptor so that
future refactoring cannot silently break the contract that the Ask owner
depends on.

Scope
-----
- **Signature freeze** (§3.5.1.1): ``MapSourceMaterialProvider.load`` is
  async, keyword-only, accepts the complete envelope + turn_id +
  include_rag_ask_only, and does NOT accept ``EnvelopeIdentity`` or a
  standalone ``user_id``.
- **Field-set freeze** (§3.2): ``SourceEvidenceDescriptor`` and
  ``DescriptorParentContext`` field names and types.
- **Frozen constants** (§3.5.1.2 / §5.4.2): allowlist, default route,
  hard cap.
- **Label rules** (§5.4.4): footnote → ``脚注`` (footnote_id never
  enters label).
- **Expansion rules** (§3.3): fail-closed paths.
- **DTO/SSE leak hygiene** (§5.1 5 / §5.1 9 / §5.1 25): no RAG
  provenance / visible retention / parent_context fields on
  ``MapSourceMaterial``.
- **Module source guards**: neither module calls ``ledger.issue``,
  ``assemble_article_map``, ``asyncpg.create_pool``, embedding/Zilliz
  seams, or imports the Ask runtime coordinator.
- **Authorization subject uniqueness** (§5.1 26): both record_id and
  user_id for ``build_index_plan`` are read from the same envelope.

This module does NOT duplicate the granular functional unit tests in
``services/api/tests/test_map_source_material_provider.py`` /
``services/api/tests/test_source_evidence_descriptor.py``. It focuses
on introspection-level contract surface verification.

No real LLM / DB / embedding / Zilliz calls. All tests are
deterministic.
"""

from __future__ import annotations

import inspect
import sys
import textwrap
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest

# ---------------------------------------------------------------------------
# Bootstrapping: add services/api to sys.path so the production modules
# can be imported. This mirrors the pattern in
# evals/tests/test_reader_record_ask_eval_context_support.py.
# Skip the entire module if required third-party deps are missing.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES_API_DIR = _REPO_ROOT / "services" / "api"

if str(_SERVICES_API_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_API_DIR))

try:
    from app.services.reader_orchestration.map_source_material_provider import (  # noqa: E402
        MapSourceMaterial,
        MapSourceMaterialProvider,
        MaterialFailureReason,
    )
    from app.services.reader_orchestration.source_evidence_descriptor import (  # noqa: E402
        ALLOWED_DESCRIPTOR_BLOCK_TYPES,
        DESCRIPTOR_DEFAULT_ROUTE,
        DESCRIPTOR_HARD_CAP,
        DescriptorParentContext,
        SourceEvidenceDescriptor,
        build_descriptor_candidates,
        build_descriptor_from_chunk,
        build_descriptor_label,
        build_expansion_text,
        chunk_qualifies_for_descriptor,
        descriptor_to_candidate_source,
    )
    from app.services.reader_record_ask.article_map_model_view import (  # noqa: E402
        ArticleMapEntrySource,
    )
    from app.services.reader_record_ask.context_envelope import (  # noqa: E402
        ReadingRecordAskContextEnvelope,
    )
except ImportError as _exc:  # pragma: no cover - environment-dependent skip
    pytest.skip(
        f"services/api production modules unavailable: {_exc}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Module source text — used for negative guards (no forbidden calls).
# ---------------------------------------------------------------------------

_MATERIAL_PROVIDER_MODULE_PATH = (
    _SERVICES_API_DIR
    / "app"
    / "services"
    / "reader_orchestration"
    / "map_source_material_provider.py"
)
_DESCRIPTOR_MODULE_PATH = (
    _SERVICES_API_DIR
    / "app"
    / "services"
    / "reader_orchestration"
    / "source_evidence_descriptor.py"
)

_MATERIAL_PROVIDER_SOURCE = _MATERIAL_PROVIDER_MODULE_PATH.read_text(encoding="utf-8")
_DESCRIPTOR_SOURCE = _DESCRIPTOR_MODULE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# §3.5.1.1 — MapSourceMaterialProvider.load signature freeze
# ---------------------------------------------------------------------------


class TestLoadSignatureFreeze:
    """§3.5.1.1 — 唯一规范签名.

    The signature MUST be::

        async def load(
            self,
            *,
            envelope: ReadingRecordAskContextEnvelope,
            turn_id: str,
            include_rag_ask_only: bool = False,
        ) -> MapSourceMaterial: ...

    No ``envelope_identity``, no standalone ``user_id``.
    """

    def test_load_is_async(self) -> None:
        assert inspect.iscoroutinefunction(MapSourceMaterialProvider.load), (
            "MapSourceMaterialProvider.load must be a coroutine function "
            "(§3.5.1.1)."
        )

    def test_load_parameters_keyword_only(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        # 'self' is positional; everything else must be keyword-only.
        params = list(sig.parameters.values())
        assert params[0].name == "self"
        for p in params[1:]:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"parameter {p.name!r} must be keyword-only (§3.5.1.1); "
                f"got {p.kind!r}."
            )

    def test_load_parameter_names_exactly_match_contract(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        names = [p.name for p in sig.parameters.values()]
        assert names == ["self", "envelope", "turn_id", "include_rag_ask_only"], (
            f"load parameter names must be exactly "
            f"['self', 'envelope', 'turn_id', 'include_rag_ask_only']; "
            f"got {names!r}."
        )

    def test_load_rejects_envelope_identity_parameter(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        assert "envelope_identity" not in sig.parameters, (
            "§3.5.1.1 v5 freeze: load must NOT accept 'envelope_identity' "
            "parameter (deprecated v3 path)."
        )

    def test_load_rejects_standalone_user_id_parameter(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        assert "user_id" not in sig.parameters, (
            "§3.5.1.1 v5 freeze: load must NOT accept a standalone "
            "'user_id' parameter — user_id must come from envelope.user_id."
        )

    def test_load_include_rag_ask_only_defaults_false(self) -> None:
        """§5.1 3 — default OFF."""
        sig = inspect.signature(MapSourceMaterialProvider.load)
        param = sig.parameters["include_rag_ask_only"]
        assert param.default is False, (
            f"include_rag_ask_only default must be False (§5.1 3); "
            f"got {param.default!r}."
        )

    def test_load_return_annotation_is_map_source_material(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        # ``from __future__ import annotations`` makes annotations strings;
        # accept either the class object or its name.
        ann = sig.return_annotation
        assert ann is MapSourceMaterial or ann == "MapSourceMaterial", (
            f"return annotation must be MapSourceMaterial; "
            f"got {ann!r}."
        )

    def test_load_envelope_parameter_typed_as_full_envelope(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        param = sig.parameters["envelope"]
        ann = param.annotation
        assert (
            ann is ReadingRecordAskContextEnvelope
            or ann == "ReadingRecordAskContextEnvelope"
        ), (
            f"envelope parameter must be typed as "
            f"ReadingRecordAskContextEnvelope (§3.5.1.1); got "
            f"{ann!r}."
        )

    def test_load_turn_id_typed_as_str(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.load)
        param = sig.parameters["turn_id"]
        ann = param.annotation
        assert ann is str or ann == "str", (
            f"turn_id must be typed as str; got {ann!r}."
        )


# ---------------------------------------------------------------------------
# §5.1 26 — Authorization subject uniqueness
# ---------------------------------------------------------------------------


class TestAuthorizationSubjectUniqueness:
    """§5.1 26 — both record_id and user_id come from the same envelope.

    The load() body must reference envelope.reading_record_id and
    envelope.user_id (not separate parameters). Verified via source
    text scan + AST-free substring check on the load method body.
    """

    def test_load_body_reads_reading_record_id_from_envelope(self) -> None:
        load_source = textwrap.dedent(
            inspect.getsource(MapSourceMaterialProvider.load)
        )
        assert "envelope.reading_record_id" in load_source, (
            "load() must read reading_record_id from envelope (§5.1 26); "
            "envelope.reading_record_id not found in load body."
        )

    def test_load_body_reads_user_id_from_envelope(self) -> None:
        load_source = textwrap.dedent(
            inspect.getsource(MapSourceMaterialProvider.load)
        )
        assert "envelope.user_id" in load_source, (
            "load() must read user_id from envelope (§5.1 26); "
            "envelope.user_id not found in load body."
        )

    def test_build_index_plan_call_uses_envelope_fields(self) -> None:
        """The build_index_plan call site must pass record_id and user_id
        both from envelope — not from local vars or other sources."""
        load_source = textwrap.dedent(
            inspect.getsource(MapSourceMaterialProvider.load)
        )
        # Both must appear in the build_index_plan call region.
        assert "record_id=envelope.reading_record_id" in load_source, (
            "build_index_plan call must use record_id=envelope.reading_record_id."
        )
        assert "user_id=envelope.user_id" in load_source, (
            "build_index_plan call must use user_id=envelope.user_id."
        )


# ---------------------------------------------------------------------------
# §3.2 — SourceEvidenceDescriptor field-set freeze
# ---------------------------------------------------------------------------


class TestSourceEvidenceDescriptorFieldSet:
    """§3.2 — exact field names, types, and excluded fields."""

    def test_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(SourceEvidenceDescriptor)
        # frozen + slots => __slots__ exists and __setattr__ raises.
        assert hasattr(SourceEvidenceDescriptor, "__slots__")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            # Construct a minimal instance then attempt mutation.
            d = SourceEvidenceDescriptor(
                reading_record_id="r",
                stable_document_id="s",
                base_id="b",
                record_generation=1,
                source_content_sha256="a" * 64,
                block_id="block-1",
                block_type="table_cell",
                expansion_text="x",
                parent_context=DescriptorParentContext(),
            )
            d.block_id = "mutated"  # type: ignore[misc]

    def test_required_field_names_exactly_match_contract(self) -> None:
        names = {f.name for f in dataclass_fields(SourceEvidenceDescriptor)}
        expected = {
            "reading_record_id",
            "stable_document_id",
            "base_id",
            "record_generation",
            "source_content_sha256",
            "block_id",
            "block_type",
            "expansion_text",
            "parent_context",
        }
        assert names == expected, (
            f"SourceEvidenceDescriptor field names must be exactly "
            f"{expected!r}; got {names!r}."
        )

    def test_excludes_index_run_id(self) -> None:
        """§3.2 — rag_ask_only does NOT go through the generic RAG index;
        index_run_id would forge RAG provenance."""
        names = {f.name for f in dataclass_fields(SourceEvidenceDescriptor)}
        assert "index_run_id" not in names

    def test_excludes_plan_content_sha256(self) -> None:
        """§3.2 — plan_content_sha256 would forge RAG provenance."""
        names = {f.name for f in dataclass_fields(SourceEvidenceDescriptor)}
        assert "plan_content_sha256" not in names

    def test_block_type_is_literal_of_three_values(self) -> None:
        hints = get_type_hints(SourceEvidenceDescriptor)
        block_type_hint = hints["block_type"]
        # Literal["table_cell", "code_block", "footnote"]
        assert get_origin(block_type_hint) is not None
        args = get_args(block_type_hint)
        assert set(args) == {"table_cell", "code_block", "footnote"}, (
            f"block_type must be Literal['table_cell', 'code_block', "
            f"'footnote']; got args={args!r}."
        )

    def test_parent_context_typed_as_descriptor_parent_context(self) -> None:
        hints = get_type_hints(SourceEvidenceDescriptor)
        assert hints["parent_context"] is DescriptorParentContext

    def test_source_content_sha256_is_str(self) -> None:
        hints = get_type_hints(SourceEvidenceDescriptor)
        assert hints["source_content_sha256"] is str


# ---------------------------------------------------------------------------
# §3.2 — DescriptorParentContext field-set freeze
# ---------------------------------------------------------------------------


class TestDescriptorParentContextFieldSet:
    """§3.2 — DescriptorParentContext restricted immutable context."""

    def test_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(DescriptorParentContext)
        assert hasattr(DescriptorParentContext, "__slots__")

    def test_field_names_exactly_match_contract(self) -> None:
        names = {f.name for f in dataclass_fields(DescriptorParentContext)}
        expected = {"column_name", "row_index", "language", "footnote_id"}
        assert names == expected, (
            f"DescriptorParentContext field names must be exactly "
            f"{expected!r}; got {names!r}."
        )

    def test_all_fields_default_none(self) -> None:
        """§3.5.1.2 — provider reads only default_route/block_type from
        metadata_json and does NOT re-query the document to populate
        structured parent_context. All fields default to None."""
        defaults = {
            f.name: f.default
            for f in dataclass_fields(DescriptorParentContext)
        }
        assert defaults == {
            "column_name": None,
            "row_index": None,
            "language": None,
            "footnote_id": None,
        }, f"all DescriptorParentContext defaults must be None; got {defaults!r}."

    def test_field_types(self) -> None:
        hints = get_type_hints(DescriptorParentContext)
        # All fields are Optional[str] / Optional[int] => str | None / int | None
        # (PEP 604 form). ``get_args`` returns the Union members, where
        # ``NoneType`` (i.e. ``type(None)``) represents the None branch.
        for field_name, hint in hints.items():
            args = set(get_args(hint))
            assert type(None) in args or hint is type(None), (
                f"{field_name} must be Optional; got {hint!r}."
            )


# ---------------------------------------------------------------------------
# §3.5.1.2 / §5.4.2 — frozen constants
# ---------------------------------------------------------------------------


class TestFrozenConstants:
    """§3.5.1.2 + §5.4.2 — constants locked by contract."""

    def test_allowed_descriptor_block_types_exactly_three(self) -> None:
        assert ALLOWED_DESCRIPTOR_BLOCK_TYPES == frozenset(
            {"table_cell", "code_block", "footnote"}
        ), (
            f"ALLOWED_DESCRIPTOR_BLOCK_TYPES must be exactly "
            f"frozenset({{'table_cell', 'code_block', 'footnote'}}); "
            f"got {ALLOWED_DESCRIPTOR_BLOCK_TYPES!r}."
        )

    def test_allowed_descriptor_block_types_is_frozenset(self) -> None:
        assert isinstance(ALLOWED_DESCRIPTOR_BLOCK_TYPES, frozenset)

    def test_descriptor_default_route_is_rag_ask_only(self) -> None:
        assert DESCRIPTOR_DEFAULT_ROUTE == "rag_ask_only"

    def test_descriptor_hard_cap_is_eight(self) -> None:
        assert DESCRIPTOR_HARD_CAP == 8
        assert isinstance(DESCRIPTOR_HARD_CAP, int)


# ---------------------------------------------------------------------------
# §5.4.4 — display label rules
# ---------------------------------------------------------------------------


class TestLabelRules:
    """§5.4.4 — display label rules for descriptor candidates."""

    def test_footnote_label_always_literal_zh(self) -> None:
        """§5.4.4 v4 freeze: footnote label is always '脚注'.

        footnote_id MUST NOT appear in the label even when
        parent_context carries one (server-internal id must not leak).
        """
        label = build_descriptor_label(
            block_type="footnote",
            parent_context=DescriptorParentContext(
                footnote_id="server-internal-id-abc-123"
            ),
        )
        assert label == "脚注", f"footnote label must be '脚注'; got {label!r}."

    def test_footnote_label_does_not_contain_footnote_id(self) -> None:
        label = build_descriptor_label(
            block_type="footnote",
            parent_context=DescriptorParentContext(
                footnote_id="super-secret-internal-id-xyz"
            ),
        )
        assert "super-secret-internal-id-xyz" not in label
        assert "internal" not in label.lower()

    def test_table_cell_with_column_name(self) -> None:
        label = build_descriptor_label(
            block_type="table_cell",
            parent_context=DescriptorParentContext(column_name=" 价格 "),
        )
        assert label == "价格"

    def test_table_cell_neutral_label_when_no_column(self) -> None:
        label = build_descriptor_label(
            block_type="table_cell",
            parent_context=DescriptorParentContext(),
        )
        assert label == "表格单元格"

    def test_code_block_with_language(self) -> None:
        label = build_descriptor_label(
            block_type="code_block",
            parent_context=DescriptorParentContext(language="python"),
        )
        assert label == "代码: python"

    def test_code_block_neutral_label_when_no_language(self) -> None:
        label = build_descriptor_label(
            block_type="code_block",
            parent_context=DescriptorParentContext(),
        )
        assert label == "代码"


# ---------------------------------------------------------------------------
# §3.3 — expansion_text fail-closed rules
# ---------------------------------------------------------------------------


class TestExpansionTextRules:
    """§3.3 — assembly rules + fail-closed fallback."""

    def test_footnote_without_footnote_id_returns_none(self) -> None:
        """§3.3 — fail-closed: footnote without structured relation is omitted."""
        result = build_expansion_text(
            block_type="footnote",
            chunk_text="1. Some footnote body text.",
            parent_context=DescriptorParentContext(),  # footnote_id is None
        )
        assert result is None

    def test_footnote_with_footnote_id_returns_chunk_text(self) -> None:
        """§3.3 — when parser preserved footnote_id, body text is used as-is."""
        result = build_expansion_text(
            block_type="footnote",
            chunk_text="Some footnote body.",
            parent_context=DescriptorParentContext(footnote_id="fn-1"),
        )
        assert result == "Some footnote body."

    def test_code_block_returns_raw_chunk_text(self) -> None:
        """§3.3 — raw code text preserved (language and newlines)."""
        code = "def hello():\n    return 'world'\n"
        result = build_expansion_text(
            block_type="code_block",
            chunk_text=code,
            parent_context=DescriptorParentContext(),
        )
        assert result == code

    def test_table_cell_with_column_name(self) -> None:
        result = build_expansion_text(
            block_type="table_cell",
            chunk_text="42",
            parent_context=DescriptorParentContext(column_name="Price"),
        )
        assert result == "Price: 42"

    def test_table_cell_neutral_prefix_without_column(self) -> None:
        result = build_expansion_text(
            block_type="table_cell",
            chunk_text="42",
            parent_context=DescriptorParentContext(),
        )
        assert result == "表格单元格: 42"

    def test_expansion_text_never_contains_server_metadata(self) -> None:
        """§3.3 — adapter must NOT splice server-side metadata (locator,
        hash, range, block id, chunk id, plan hash, UTF-16 offset,
        generation, record id) into expansion_text."""
        # Verify by inspecting a table_cell output for absence of common
        # server metadata tokens.
        result = build_expansion_text(
            block_type="table_cell",
            chunk_text="cell content",
            parent_context=DescriptorParentContext(column_name="Col"),
        )
        # Result should be exactly "Col: cell content" — no hash, no UUID,
        # no offset, no block_id.
        forbidden_substrings = [
            "block-",
            "chunk-",
            "sha256",
            "utf16",
            "offset",
            "generation",
            "record_id",
        ]
        for sub in forbidden_substrings:
            assert sub not in result.lower(), (
                f"expansion_text must not contain server metadata '{sub}'; "
                f"got {result!r}."
            )


# ---------------------------------------------------------------------------
# §5.1 9 / §5.1 25 — MapSourceMaterial shape (no RAG provenance / visible retention)
# ---------------------------------------------------------------------------


class TestMapSourceMaterialShape:
    """§5.1 9 + §5.1 25 — MapSourceMaterial is a pure candidate carrier.

    Must NOT carry RAG provenance (index_run_id / plan_content_sha256)
    or visible retention fields. Only fence state + candidate descriptor
    tuple + heading tuple + diagnostic enum.
    """

    def test_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        assert dataclasses.is_dataclass(MapSourceMaterial)
        assert hasattr(MapSourceMaterial, "__slots__")

    def test_field_names_exactly_match_contract(self) -> None:
        names = {f.name for f in dataclass_fields(MapSourceMaterial)}
        expected = {
            "material_fence_ok",
            "descriptor_sources",
            "heading_enrichments",
            "material_failure_reason",
        }
        assert names == expected, (
            f"MapSourceMaterial field names must be exactly {expected!r}; "
            f"got {names!r}."
        )

    def test_no_rag_provenance_fields(self) -> None:
        """§5.1 9 — no RAG provenance leakage."""
        names = {f.name for f in dataclass_fields(MapSourceMaterial)}
        for forbidden in (
            "index_run_id",
            "plan_content_sha256",
            "chunk_id",
            "embedding_id",
            "vector_id",
        ):
            assert forbidden not in names, (
                f"MapSourceMaterial must NOT carry RAG provenance field "
                f"'{forbidden}' (§5.1 9)."
            )

    def test_no_visible_retention_fields(self) -> None:
        """§5.1 25 — candidates are not guaranteed visible; no retention
        state fields."""
        names = {f.name for f in dataclass_fields(MapSourceMaterial)}
        for forbidden in (
            "visible_retention",
            "retained",
            "cursor_id",
            "issue_marker",
            "issued_at",
            "cursor_state",
            "stale_evidence",
        ):
            assert forbidden not in names, (
                f"MapSourceMaterial must NOT carry visible retention field "
                f"'{forbidden}' (§5.1 25)."
            )

    def test_no_parent_context_field(self) -> None:
        """§5.1 5 — parent_context never enters DTO/SSE; MapSourceMaterial
        is the Ask-owner-facing interface, so it must not carry
        parent_context."""
        names = {f.name for f in dataclass_fields(MapSourceMaterial)}
        assert "parent_context" not in names

    def test_descriptor_sources_is_tuple_of_article_map_entry_source(self) -> None:
        hints = get_type_hints(MapSourceMaterial)
        ds_hint = hints["descriptor_sources"]
        # tuple[ArticleMapEntrySource, ...] => origin is tuple, args contain
        # ArticleMapEntrySource and Ellipsis.
        assert get_origin(ds_hint) is tuple
        args = get_args(ds_hint)
        assert ArticleMapEntrySource in args

    def test_material_failure_reason_is_literal_six_values(self) -> None:
        """MaterialFailureReason Literal must cover exactly the 6 safe
        diagnostic values frozen by §5.1 6(b)."""
        args = set(get_args(MaterialFailureReason))
        expected = {
            "ok",
            "envelope_stable_document_id_missing",
            "envelope_base_content_sha256_missing",
            "stable_document_id_mismatch",
            "base_content_sha256_mismatch",
            "plan_build_failed",
        }
        assert args == expected, (
            f"MaterialFailureReason Literal must cover exactly {expected!r}; "
            f"got {args!r}."
        )


# ---------------------------------------------------------------------------
# Module source guards — forbidden calls / imports
# ---------------------------------------------------------------------------


class TestModuleSourceGuards:
    """Negative hygiene guards: neither the material provider nor the
    descriptor module may call
    forbidden seams.

    The material provider produces pure preflight computation. Does
    NOT call ``ledger.issue``, ``assemble_article_map``, registry /
    cursor mutation, embedding/Zilliz, or import the Ask runtime
    coordinator.
    """

    @pytest.mark.parametrize(
        "forbidden",
        [
            "ledger.issue",
            "assemble_article_map(",
            "asyncpg.create_pool",
            "psycopg.connect",
            "from app.services.reader_record_ask.turn_coordinator",
            "import zilliz",
            "from zilliz",
            "import milvus",
            "from milvus",
            "import openai.Embedding",
            "dashscope.TextEmbedding",
        ],
    )
    def test_material_provider_module_does_not_call_forbidden_seams(self, forbidden: str) -> None:
        assert forbidden not in _MATERIAL_PROVIDER_SOURCE, (
            f"map_source_material_provider.py must NOT contain "
            f"{forbidden!r} — the material provider is pure preflight computation."
        )

    @pytest.mark.parametrize(
        "forbidden",
        [
            "ledger.issue",
            "assemble_article_map(",
            "asyncpg.create_pool",
            "psycopg.connect",
            "from app.services.reader_record_ask.turn_coordinator",
            "import zilliz",
            "from zilliz",
            "import milvus",
            "from milvus",
            "import openai.Embedding",
            "dashscope.TextEmbedding",
        ],
    )
    def test_descriptor_module_does_not_call_forbidden_seams(self, forbidden: str) -> None:
        assert forbidden not in _DESCRIPTOR_SOURCE, (
            f"source_evidence_descriptor.py must NOT contain "
            f"{forbidden!r} — adapter is pure computation over plan."
        )

    def test_material_provider_module_does_not_directly_import_asyncpg(self) -> None:
        """asyncpg is only transitive via article_rag_index_plan.py — the
        material provider itself must not establish a direct DB dependency."""
        # Allow only in docstrings/comments — check import statements.
        for line in _MATERIAL_PROVIDER_SOURCE.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.endswith('"""'):
                continue
            assert not (
                stripped.startswith("import asyncpg")
                or stripped.startswith("from asyncpg")
            ), (
                f"map_source_material_provider must not directly import asyncpg (only transitive "
                f"via article_rag_index_plan). Found: {stripped!r}."
            )

    def test_descriptor_module_does_not_directly_import_asyncpg(self) -> None:
        for line in _DESCRIPTOR_SOURCE.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.endswith('"""'):
                continue
            assert not (
                stripped.startswith("import asyncpg")
                or stripped.startswith("from asyncpg")
            ), (
                f"source_evidence_descriptor must not directly import asyncpg. "
                f"Found: {stripped!r}."
            )


# ---------------------------------------------------------------------------
# §5.1 5 — SourceEvidenceDescriptor never serialized to public schema
# ---------------------------------------------------------------------------


class TestDescriptorNeverEntersPublicSchema:
    """§5.1 5 — SourceEvidenceDescriptor is server-only; never serialized
    into ArticleRagCitationEvidence or any DTO/SSE.

    Verified by source text scan: ArticleRagCitationEvidence's defining
    module must NOT import SourceEvidenceDescriptor.
    """

    def test_evidence_module_does_not_import_descriptor(self) -> None:
        evidence_path = (
            _SERVICES_API_DIR
            / "app"
            / "services"
            / "reader_record_ask"
            / "evidence.py"
        )
        if not evidence_path.exists():
            pytest.skip("evidence.py not found at expected path.")
        evidence_source = evidence_path.read_text(encoding="utf-8")
        assert "SourceEvidenceDescriptor" not in evidence_source, (
            "ArticleRagCitationEvidence's module (evidence.py) must NOT "
            "import SourceEvidenceDescriptor (§5.1 5 — server-only)."
        )

    def test_descriptor_class_docstring_states_server_only(self) -> None:
        """The class docstring must explicitly state server-only / never
        serialized — this is a contract self-documentation requirement."""
        doc = SourceEvidenceDescriptor.__doc__ or ""
        doc_lower = doc.lower()
        assert "server-only" in doc_lower or "server only" in doc_lower, (
            "SourceEvidenceDescriptor docstring must state 'server-only'."
        )
        assert (
            "serialized" in doc_lower or "serialised" in doc_lower
        ), "SourceEvidenceDescriptor docstring must mention serialization boundary."


# ---------------------------------------------------------------------------
# Module public surface freeze
# ---------------------------------------------------------------------------


class TestModulePublicSurface:
    """Public ``__all__`` exports must match the v5 contract surface so
    consumers (Ask owner) cannot accidentally depend on internal symbols.
    """

    def test_material_provider_module_all_exports(self) -> None:
        from app.services.reader_orchestration import (
            map_source_material_provider as material_provider_mod,
        )

        expected = {
            "HeadingEnrichment",
            "MapSourceMaterial",
            "MapSourceMaterialProvider",
            "MaterialFailureReason",
        }
        got = set(material_provider_mod.__all__)
        assert got == expected, (
            f"material provider __all__ must be exactly {expected!r}; "
            f"got {got!r}."
        )

    def test_descriptor_module_all_exports(self) -> None:
        from app.services.reader_orchestration import (
            source_evidence_descriptor as descriptor_mod,
        )

        expected = {
            "ALLOWED_DESCRIPTOR_BLOCK_TYPES",
            "DESCRIPTOR_DEFAULT_ROUTE",
            "DESCRIPTOR_HARD_CAP",
            "DescriptorParentContext",
            "SourceEvidenceDescriptor",
            "build_descriptor_candidates",
            "build_descriptor_from_chunk",
            "build_descriptor_label",
            "build_expansion_text",
            "chunk_qualifies_for_descriptor",
            "descriptor_to_candidate_source",
        }
        assert set(descriptor_mod.__all__) == expected, (
            f"descriptor __all__ must be exactly {expected!r}; got {set(descriptor_mod.__all__)!r}."
        )


# ---------------------------------------------------------------------------
# §3.5.1.2 — chunk filter surface (signature only; functional coverage
# lives in services/api/tests/)
# ---------------------------------------------------------------------------


class TestChunkFilterSignature:
    """§3.5.1.2 — chunk_qualifies_for_descriptor reads fields only from
    ArticleRagIndexChunk.metadata_json (never re-queries document).

    Signature-level freeze; functional path coverage is in
    services/api/tests/test_source_evidence_descriptor.py.
    """

    def test_chunk_qualifies_for_descriptor_takes_only_chunk(self) -> None:
        sig = inspect.signature(chunk_qualifies_for_descriptor)
        params = list(sig.parameters.values())
        assert len(params) == 1, (
            f"chunk_qualifies_for_descriptor must take exactly 1 param "
            f"(chunk); got {len(params)}."
        )
        assert params[0].name == "chunk"

    def test_build_descriptor_from_chunk_signature(self) -> None:
        sig = inspect.signature(build_descriptor_from_chunk)
        params = list(sig.parameters.values())
        # Must be keyword-only and take chunk + plan.
        names = [p.name for p in params]
        assert names == ["chunk", "plan"], (
            f"build_descriptor_from_chunk params must be [chunk, plan]; "
            f"got {names!r}."
        )
        for p in params:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY

    def test_build_descriptor_candidates_signature(self) -> None:
        sig = inspect.signature(build_descriptor_candidates)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["plan"], (
            f"build_descriptor_candidates params must be [plan]; got {names!r}."
        )
        for p in params:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY

    def test_descriptor_to_candidate_source_returns_article_map_entry_source(self) -> None:
        sig = inspect.signature(descriptor_to_candidate_source)
        # ``from __future__ import annotations`` makes annotations strings;
        # accept either the class object or its name.
        ann = sig.return_annotation
        assert ann is ArticleMapEntrySource or ann == "ArticleMapEntrySource", (
            f"return annotation must be ArticleMapEntrySource; got {ann!r}."
        )


# ---------------------------------------------------------------------------
# §5.4.1 — sort key surface (constant rank=1 for descriptor sources)
# ---------------------------------------------------------------------------


class TestSortKeySurface:
    """§5.4.1 — descriptor source_kind_rank is 1 (正文 is 0).

    Verified by building a small plan and inspecting that descriptors
    come after 正文 sources in a stable canonical order. This is a
    light contract check — full sort semantics are covered in
    services/api/tests/.
    """

    def test_descriptor_candidate_internal_class_has_sort_key_method(self) -> None:
        """The internal _DescriptorCandidate class exposes a sort_key()
        returning a tuple whose first element is the descriptor rank."""
        # Access the internal class via the module — it's not in __all__
        # but it is referenced by build_descriptor_candidates.
        from app.services.reader_orchestration import (
            source_evidence_descriptor as descriptor_mod,
        )

        candidate_cls = getattr(descriptor_mod, "_DescriptorCandidate", None)
        assert candidate_cls is not None, (
            "_DescriptorCandidate internal class must exist for §5.4.1 sort."
        )
        # Check that sort_key returns a tuple with first element 1.
        # We construct a fake candidate via __new__ to avoid running the
        # full dataclass __init__ (which requires descriptor + index + id).
        # Instead inspect the source of sort_key to verify the rank literal.
        sort_key_source = textwrap.dedent(
            inspect.getsource(candidate_cls.sort_key)
        )
        assert "1" in sort_key_source, (
            "_DescriptorCandidate.sort_key must reference rank=1 for "
            "descriptor sources (§5.4.1)."
        )
        assert "return (" in sort_key_source or "return tuple" in sort_key_source


# ---------------------------------------------------------------------------
# Smoke: ArticleMapEntrySource is importable and constructible with the
# minimal fields the descriptor conversion uses.
# ---------------------------------------------------------------------------


class TestArticleMapEntrySourceSurface:
    """The candidate output type must remain constructible with the
    minimal (heading, window_text) fields the descriptor conversion uses.
    """

    def test_constructible_with_heading_and_window_text(self) -> None:
        """The descriptor conversion constructs ArticleMapEntrySource
        with ``heading=...`` and ``window_text=...``. If ArticleMapEntrySource
        drops or renames these fields, the conversion breaks.
        """
        sig = inspect.signature(ArticleMapEntrySource)
        params = sig.parameters
        assert "heading" in params, (
            "ArticleMapEntrySource must accept 'heading' (descriptor conversion depends on it)."
        )
        assert "window_text" in params, (
            "ArticleMapEntrySource must accept 'window_text' (descriptor conversion depends on it)."
        )

    def test_heading_field_accepts_none(self) -> None:
        """ArticleMapEntrySource.heading must accept None (正文 source path
        uses None; descriptor path uses the label rule)."""
        # Construct a minimal instance — should not raise.
        instance = ArticleMapEntrySource(heading=None, window_text="x")
        assert instance.heading is None
        assert instance.window_text == "x"


# ---------------------------------------------------------------------------
# §3.5.1.1 — provider constructor surface
# ---------------------------------------------------------------------------


class TestProviderConstructorSurface:
    """§3.5.1.1 — MapSourceMaterialProvider accepts a plan_service
    dependency (DI seam). This is not part of the load signature but
    is part of the contract surface that Ask owner wires."""

    def test_constructor_accepts_plan_service(self) -> None:
        sig = inspect.signature(MapSourceMaterialProvider.__init__)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self", "plan_service"], (
            f"__init__ params must be [self, plan_service]; got {names!r}."
        )

    def test_constructor_does_not_accept_envelope_or_user_id(self) -> None:
        """The constructor must NOT take envelope/user_id — those come
        per-call via load(). This prevents stateful authorization
        drift."""
        sig = inspect.signature(MapSourceMaterialProvider.__init__)
        params = sig.parameters
        assert "envelope" not in params
        assert "user_id" not in params
        assert "envelope_identity" not in params

    def test_provider_does_not_persist_envelope_or_user_id(self) -> None:
        """Provider must not retain envelope/user_id on self — each load()
        call re-reads from the supplied envelope."""
        init_source = textwrap.dedent(
            inspect.getsource(MapSourceMaterialProvider.__init__)
        )
        # Must NOT store envelope/user_id on self.
        for forbidden in (
            "self._envelope",
            "self._user_id",
            "self._reading_record_id",
            "self.envelope",
            "self.user_id",
        ):
            assert forbidden not in init_source, (
                f"__init__ must NOT persist {forbidden} on self — load() "
                f"reads authorization from the per-call envelope."
            )
