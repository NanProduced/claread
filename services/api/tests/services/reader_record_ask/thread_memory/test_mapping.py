"""mapping.py 单元测试（H6 + H7）。"""

from __future__ import annotations

from app.services.reader_record_ask.thread_memory.mapping import (
    degrade_web_citation_to_hint,
    derive_source_bindings,
)


class TestDeriveSourceBindings:
    def test_article_with_stable_document_id(self) -> None:
        # H6: article binding with stable_document_id -> fence_type='stable_document'
        bindings = [
            {
                "turn_run_id": "tr1",
                "citation_id": "cit_1",
                "handle_id": "evh_abc",
                "source_kind": "article",
                "unit_id": "u1",
                "anchor_segment_id": "a1",
                "kind": "search_hit",
                "source_tool": "search_current_article",
                "rag_citation": {
                    "stable_document_id": "doc_42",
                    "base_id": "base_1",
                    "record_generation": 7,
                    "reading_record_id": "rr_9",
                },
            },
        ]
        result = derive_source_bindings(bindings)
        assert len(result) == 1
        sb = result[0]
        assert sb.binding_id == "cit_1"
        assert sb.source_type == "article"
        assert sb.fence_type == "stable_document"
        assert sb.source_id == "doc_42"
        assert sb.fence_values["stable_document_id"] == "doc_42"
        assert sb.fence_values["base_id"] == "base_1"
        assert sb.fence_values["record_generation"] == "7"
        assert sb.fence_values["reading_record_id"] == "rr_9"
        assert sb.validity_check == {
            "status": "unchecked",
            "last_validated_turn": 0,
        }

    def test_article_without_stable_document_id_falls_back_to_reading_record(
        self,
    ) -> None:
        bindings = [
            {
                "turn_run_id": "tr1",
                "citation_id": "cit_2",
                "handle_id": "evh_def",
                "source_kind": "article",
                "unit_id": None,
                "anchor_segment_id": None,
                "kind": "search_hit",
                "source_tool": "search_current_article",
                "rag_citation": {
                    "stable_document_id": "",
                    "base_id": "",
                    "record_generation": 1,
                    "reading_record_id": "rr_1",
                },
            },
        ]
        result = derive_source_bindings(bindings)
        assert len(result) == 1
        sb = result[0]
        assert sb.fence_type == "reading_record"
        assert sb.source_id == "rr_1"

    def test_article_with_missing_rag_citation(self) -> None:
        # rag_citation None: stable_document_id empty -> reading_record fence,
        # source_id empty (reading_record_id also empty).
        bindings = [
            {
                "turn_run_id": "tr1",
                "citation_id": "cit_3",
                "handle_id": "evh_ghi",
                "source_kind": "article",
                "rag_citation": None,
            },
        ]
        result = derive_source_bindings(bindings)
        assert len(result) == 1
        sb = result[0]
        assert sb.fence_type == "reading_record"
        assert sb.source_id == ""

    def test_web_binding_maps_to_reading_record_fence(self) -> None:
        # H6: web -> fence_type='reading_record'; source_id from handle_id
        # (canonical_url is a content field, not touched by this layer).
        bindings = [
            {
                "turn_run_id": "tr1",
                "citation_id": "cit_web",
                "handle_id": "evh_web1",
                "source_kind": "web",
                "unit_id": None,
                "anchor_segment_id": None,
                "kind": "web",
                "source_tool": "search_web",
                "rag_citation": None,
            },
        ]
        result = derive_source_bindings(bindings)
        assert len(result) == 1
        sb = result[0]
        assert sb.source_type == "web"
        assert sb.fence_type == "reading_record"
        assert sb.source_id == "evh_web1"
        assert sb.fence_values == {}

    def test_unknown_source_kind_skipped(self) -> None:
        bindings = [
            {
                "turn_run_id": "tr1",
                "citation_id": "cit_x",
                "handle_id": "evh_x",
                "source_kind": "unknown",
                "rag_citation": None,
            },
        ]
        assert derive_source_bindings(bindings) == []

    def test_empty_input(self) -> None:
        assert derive_source_bindings([]) == []

    def test_mixed_batch(self) -> None:
        bindings = [
            {
                "citation_id": "c_art",
                "handle_id": "evh_a",
                "source_kind": "article",
                "rag_citation": {
                    "stable_document_id": "doc1",
                    "base_id": "b1",
                    "record_generation": 1,
                    "reading_record_id": "rr1",
                },
            },
            {
                "citation_id": "c_web",
                "handle_id": "evh_w",
                "source_kind": "web",
                "rag_citation": None,
            },
        ]
        result = derive_source_bindings(bindings)
        assert len(result) == 2
        assert result[0].source_type == "article"
        assert result[0].fence_type == "stable_document"
        assert result[1].source_type == "web"
        assert result[1].fence_type == "reading_record"


class TestDegradeWebCitationToHint:
    def test_produces_hint_without_source_fingerprint(self) -> None:
        binding = {
            "citation_id": "cit_web",
            "source_kind": "web",
            "canonical_url": "https://www.example.com/article/path",
            "retrieved_at": "2026-07-30T00:00:00Z",
            "web_title": "Some Title",
            "source_fingerprint": "sha256:abc",
        }
        hint = degrade_web_citation_to_hint(binding)
        assert "display_domain" in hint
        assert hint["display_domain"] == "www.example.com"
        assert hint["retrieved_at"] == "2026-07-30T00:00:00Z"
        assert hint["web_title"] == "Some Title"
        # H7: must NOT carry source_fingerprint.
        assert "source_fingerprint" not in hint

    def test_does_not_raise_on_missing_fields(self) -> None:
        binding: dict = {"source_kind": "web"}
        hint = degrade_web_citation_to_hint(binding)
        assert "display_domain" in hint
        assert hint["display_domain"] == ""
        assert "source_fingerprint" not in hint

    def test_does_not_raise_on_empty_binding(self) -> None:
        hint = degrade_web_citation_to_hint({})
        assert isinstance(hint, dict)
        assert "source_fingerprint" not in hint
        assert hint["display_domain"] == ""

    def test_handles_malformed_url(self) -> None:
        binding = {
            "canonical_url": "not a url at all",
            "retrieved_at": "2026-07-30",
        }
        hint = degrade_web_citation_to_hint(binding)
        # Must not raise; display_domain may be empty or partial.
        assert isinstance(hint["display_domain"], str)
        assert "source_fingerprint" not in hint

    def test_extracts_domain_from_url_with_path(self) -> None:
        binding = {
            "canonical_url": "https://news.example.org/section/article?id=1",
            "retrieved_at": "2026-07-29T12:00:00Z",
        }
        hint = degrade_web_citation_to_hint(binding)
        assert hint["display_domain"] == "news.example.org"
        assert "source_fingerprint" not in hint
