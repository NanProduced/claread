"""Article RAG Contract Seam tests.

Verifies the frozen Article RAG embedding + vector contract is a single
source of truth shared by bootstrap, worker, retrieval, vector adapter,
and settings. The contract lives in ``app.contracts.article_rag_contract``
(low-dependency, no version dimensions) so ``app.config.settings`` can
import it without triggering service-level circular imports.

Single-path invariants enforced here:

  * The contract module is low-dependency (no settings / services
    imports at module load time).
  * All four service modules reference the SAME contract instance
    (identity check, not just equality).
  * ``Settings`` default Zilliz collection + vector dim are sourced
    from the contract — no second Python literal.
  * No duplicate ``article_rag_chunks`` literal in production Python
    code under ``app/``.
  * The contract module does not reintroduce version dimensions
    (``index_version`` / ``chunker_version`` / ``profile_fingerprint``
    / registry / resolver / compatibility alias).
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from app.contracts.article_rag_contract import (
    ARTICLE_RAG_EMBEDDING_CONTRACT,
    ArticleRagEmbeddingContract,
)

# ---------------------------------------------------------------------------
# Contract module structure
# ---------------------------------------------------------------------------


class TestContractModuleIsLowDependency:
    """The contract module MUST be low-dependency — no settings / services
    imports at module load time. This is what allows ``app.config.settings``
    to import the contract without triggering service-level circular imports.
    """

    def test_contract_module_imports_cleanly(self) -> None:
        """Importing the contract module does not pull in settings or
        any reader_orchestration service module."""
        import importlib

        mod = importlib.import_module("app.contracts.article_rag_contract")
        assert mod is not None
        assert hasattr(mod, "ARTICLE_RAG_EMBEDDING_CONTRACT")
        assert hasattr(mod, "ArticleRagEmbeddingContract")

    def test_contract_module_source_has_no_settings_or_services_imports(
        self,
    ) -> None:
        """The contract module source MUST NOT import from
        ``app.config`` or ``app.services``."""
        mod_path = Path(__file__).resolve().parents[1] / (
            "app/contracts/article_rag_contract.py"
        )
        source = mod_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_substrings = ("app.config", "app.services")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for forbidden in forbidden_substrings:
                    assert forbidden not in module, (
                        f"contract module imports from {forbidden!r} "
                        f"(node module={module!r}) — must stay low-dependency"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_substrings:
                        assert forbidden not in alias.name, (
                            f"contract module imports {forbidden!r} "
                            f"(alias name={alias.name!r}) — must stay low-dependency"
                        )


# ---------------------------------------------------------------------------
# Contract field values
# ---------------------------------------------------------------------------


class TestContractFieldsMatchCurrentReality:
    """The contract captures the 6 fields downstream code actually needs:
    document/query embedding model, dimension, document/query text type,
    vector collection. No version / fingerprint / chunker fields.
    """

    def test_contract_is_frozen_dataclass_with_slots(self) -> None:
        assert dataclasses.is_dataclass(ArticleRagEmbeddingContract)
        # Frozen + slots — callers MUST NOT mutate.
        params = ArticleRagEmbeddingContract.__dataclass_params__
        assert params.frozen is True
        # slots=True on Python 3.10+ produces __slots__.
        assert hasattr(ArticleRagEmbeddingContract, "__slots__")

    def test_contract_field_set_excludes_version_dimensions(self) -> None:
        """No index_version / chunker_version / profile_fingerprint /
        registry / resolver / compatibility_alias fields."""
        field_names = {f.name for f in dataclasses.fields(ArticleRagEmbeddingContract)}
        forbidden_fields = {
            "index_version",
            "chunker_version",
            "profile_fingerprint",
            "registry",
            "resolver",
            "compatibility_alias",
            "plan_version",
            "runtime_version_override",
        }
        assert not (field_names & forbidden_fields), (
            f"contract must not carry version dimensions; "
            f"found {field_names & forbidden_fields}"
        )

    def test_contract_field_values(self) -> None:
        assert ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_model == "text-embedding-v4"
        assert ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_dimension == 1024
        assert ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_text_type == "provider_default"
        assert ARTICLE_RAG_EMBEDDING_CONTRACT.query_embedding_model == "text-embedding-v4"
        assert ARTICLE_RAG_EMBEDDING_CONTRACT.query_embedding_text_type == "provider_default"
        assert ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection == "article_rag_chunks"

    def test_contract_instance_is_hashable_and_frozen(self) -> None:
        """Frozen dataclass — mutation raises, hash works."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection = "tampered"  # type: ignore[misc]
        # Hashable — safe to use as module-level constant.
        assert hash(ARTICLE_RAG_EMBEDDING_CONTRACT) == hash(ARTICLE_RAG_EMBEDDING_CONTRACT)


# ---------------------------------------------------------------------------
# All consumers reference the same contract instance (identity)
# ---------------------------------------------------------------------------


class TestConsumersReferenceUnifiedContract:
    """bootstrap, worker, retrieval, vector_store MUST all reference the
    SAME contract instance from ``app.contracts.article_rag_contract`` —
    not a copy, not a re-definition with equal values.
    """

    def test_bootstrap_references_unified_contract(self) -> None:
        from app.services.reader_orchestration.article_rag_index_bootstrap import (
            ARTICLE_RAG_EMBEDDING_CONTRACT as bootstrap_contract,
        )

        assert bootstrap_contract is ARTICLE_RAG_EMBEDDING_CONTRACT

    def test_worker_references_unified_contract(self) -> None:
        from app.services.reader_orchestration.article_rag_index_worker import (
            ARTICLE_RAG_EMBEDDING_CONTRACT as worker_contract,
        )

        assert worker_contract is ARTICLE_RAG_EMBEDDING_CONTRACT

    def test_retrieval_references_unified_contract(self) -> None:
        from app.services.reader_orchestration.article_rag_retrieval_service import (
            ARTICLE_RAG_EMBEDDING_CONTRACT as retrieval_contract,
        )

        assert retrieval_contract is ARTICLE_RAG_EMBEDDING_CONTRACT

    def test_vector_store_references_unified_contract(self) -> None:
        from app.services.reader_orchestration.article_rag_vector_store import (
            ARTICLE_RAG_EMBEDDING_CONTRACT as vector_store_contract,
        )

        assert vector_store_contract is ARTICLE_RAG_EMBEDDING_CONTRACT


# ---------------------------------------------------------------------------
# Settings default collection + dim sourced from contract
# ---------------------------------------------------------------------------


class TestSettingsDefaultsSourcedFromContract:
    """``Settings.reader_article_rag_zilliz_collection`` default MUST come
    from the contract — no second ``article_rag_chunks`` Python literal
    in ``settings.py``. Same for ``reader_article_rag_vector_dim``.
    """

    def test_settings_default_collection_matches_contract(self) -> None:
        from app.config.settings import Settings

        settings = Settings()
        assert (
            settings.reader_article_rag_zilliz_collection
            == ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection
        )

    def test_settings_default_vector_dim_matches_contract(self) -> None:
        from app.config.settings import Settings

        settings = Settings()
        assert (
            settings.reader_article_rag_vector_dim
            == ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_dimension
        )

    def test_settings_source_has_no_article_rag_chunks_literal(self) -> None:
        """The settings.py source MUST NOT contain the ``article_rag_chunks``
        Python literal — the default must be sourced from the contract."""
        settings_path = Path(__file__).resolve().parents[1] / (
            "app/config/settings.py"
        )
        source = settings_path.read_text(encoding="utf-8")
        assert '"article_rag_chunks"' not in source, (
            "settings.py still contains the \"article_rag_chunks\" literal — "
            "must source from app.contracts.article_rag_contract"
        )
        assert "'article_rag_chunks'" not in source, (
            "settings.py still contains the 'article_rag_chunks' literal — "
            "must source from app.contracts.article_rag_contract"
        )


# ---------------------------------------------------------------------------
# No duplicate article_rag_chunks literal in production Python
# ---------------------------------------------------------------------------


class TestNoDuplicateLiteralInProductionPython:
    """After the contract seam refactor, the ``article_rag_chunks`` literal
    MUST appear in exactly ONE place in production Python code under
    ``app/``: the contract module.
    """

    @pytest.fixture(scope="class")
    def production_python_files_with_literal(self) -> list[Path]:
        """All production Python files under app/ that contain the
        ``article_rag_chunks`` literal (string form)."""
        api_root = Path(__file__).resolve().parents[1]
        app_root = api_root / "app"
        matches: list[Path] = []
        for path in app_root.rglob("*.py"):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # Match the literal as a Python string token — quoted form.
            if '"article_rag_chunks"' in source or "'article_rag_chunks'" in source:
                matches.append(path)
        return matches

    def test_literal_appears_only_in_contract_module(
        self,
        production_python_files_with_literal: list[Path],
    ) -> None:
        """The literal ``article_rag_chunks`` (in quoted form) MUST only
        appear in ``app/contracts/article_rag_contract.py`` among all
        production Python files."""
        api_root = Path(__file__).resolve().parents[1]
        contract_module = api_root / "app" / "contracts" / "article_rag_contract.py"

        non_contract_matches = [
            p for p in production_python_files_with_literal if p != contract_module
        ]
        assert non_contract_matches == [], (
            "article_rag_chunks literal found in non-contract production "
            f"Python files: {[str(p.relative_to(api_root)) for p in non_contract_matches]}"
        )

    def test_contract_module_contains_literal(self) -> None:
        """Sanity: the contract module DOES contain the literal (otherwise
        the previous test would trivially pass with the literal missing
        everywhere)."""
        api_root = Path(__file__).resolve().parents[1]
        contract_module = api_root / "app" / "contracts" / "article_rag_contract.py"
        source = contract_module.read_text(encoding="utf-8")
        assert (
            '"article_rag_chunks"' in source
            or "'article_rag_chunks'" in source
        ), "contract module must define the article_rag_chunks literal"


# ---------------------------------------------------------------------------
# Contract module does not reintroduce version dimensions
# ---------------------------------------------------------------------------


class TestContractModuleDoesNotReintroduceVersionDimensions:
    """The contract module source MUST NOT reference any version / profile /
    fingerprint / registry / resolver / compatibility alias tokens.
    """

    def test_no_version_dimension_tokens_in_contract_source(self) -> None:
        api_root = Path(__file__).resolve().parents[1]
        contract_module = api_root / "app" / "contracts" / "article_rag_contract.py"
        source = contract_module.read_text(encoding="utf-8")

        forbidden_tokens = [
            "index_version",
            "chunker_version",
            "profile_fingerprint",
            "plan_version",
            "registry",
            "resolver",
            "compatibility_alias",
            "runtime_version_override",
            "article_rag_index_v1",
            "article_rag_index_v2",
        ]
        # Docstring mentions of these tokens are OK (e.g., "no index_version");
        # we only flag code-level references. Walk the AST and check
        # string literals + identifiers.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_tokens, (
                    f"contract module references forbidden token {node.id!r}"
                )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Allow docstring sentences that mention these tokens
                # in a "does not include" context. We only flag standalone
                # string values that look like real configuration values.
                # Concretely: if the string EQUALS a forbidden token, fail.
                if node.value in forbidden_tokens:
                    pytest.fail(
                        f"contract module has string literal equal to "
                        f"forbidden token {node.value!r}"
                    )


# ---------------------------------------------------------------------------
# FrozenInstanceError — dataclasses.FrozenInstanceError is the canonical
# name on Python 3.10+.
# ---------------------------------------------------------------------------
