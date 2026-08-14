"""Reading Record Identity Projection — pure unit tests for the
priority matrix and source_label mapping.

These tests exercise
:mod:`app.services.reader_orchestration.reading_record_list_projection`
in isolation (no DB, no HTTP). The DB-backed route-level behavior is
covered by ``test_reader_orchestration_api.py`` and the new
``test_reading_record_list_route.py``.
"""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.reading_record_list_projection import (
    build_reading_record_list_projection,
)


# ---------------------------------------------------------------------------
# display_title priority matrix
# ---------------------------------------------------------------------------


def test_layer1_succeeded_generated_title_zh_wins() -> None:
    """Layer 1: succeeded + non-empty generated_title_zh wins over
    record.title and everything else."""
    projection = build_reading_record_list_projection(
        record_title="English Title",
        generated_title_zh="生成的中文标题",
        title_generation_status="succeeded",
        ready_candidate_title="Candidate Title",
        original_input_type="plain_text",
        original_input_filename="notes.txt",
        source_type="text",
    )
    assert projection.display_title == "生成的中文标题"


def test_layer1_skipped_when_status_not_succeeded() -> None:
    """title_generation_status != succeeded must NOT use generated_title_zh
    even if it is non-empty. Falls through to record.title (layer 2)."""
    for status in ("pending", "failed_retryable", None):
        projection = build_reading_record_list_projection(
            record_title="English Title",
            generated_title_zh="should not be used",
            title_generation_status=status,
            ready_candidate_title=None,
            original_input_type="plain_text",
            original_input_filename=None,
            source_type="text",
        )
        assert projection.display_title == "English Title", status


def test_layer1_skipped_when_generated_title_zh_empty() -> None:
    """succeeded status but empty generated_title_zh falls through to
    record.title."""
    for empty in ("", "   ", None):
        projection = build_reading_record_list_projection(
            record_title="English Title",
            generated_title_zh=empty,
            title_generation_status="succeeded",
            ready_candidate_title=None,
            original_input_type="plain_text",
            original_input_filename=None,
            source_type="text",
        )
        assert projection.display_title == "English Title", empty


def test_layer2_record_title_when_no_succeeded_generated() -> None:
    """Layer 2: non-empty record.title wins when layer 1 is absent."""
    projection = build_reading_record_list_projection(
        record_title="My Reading",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title="Candidate",
        original_input_type="plain_text",
        original_input_filename="file.txt",
        source_type="text",
    )
    assert projection.display_title == "My Reading"


def test_layer3_ready_candidate_title_when_no_record_title() -> None:
    """Layer 3: ready candidate title wins when record.title is empty
    and no succeeded generated title."""
    projection = build_reading_record_list_projection(
        record_title=None,
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title="Candidate Title",
        original_input_type="plain_text",
        original_input_filename="file.txt",
        source_type="text",
    )
    assert projection.display_title == "Candidate Title"


def test_layer4_filename_when_no_titles() -> None:
    """Layer 4: original input filename wins when all title layers are
    empty."""
    projection = build_reading_record_list_projection(
        record_title=None,
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="file_ref",
        original_input_filename="report.pdf",
        source_type="file",
    )
    assert projection.display_title == "report.pdf"


def test_layer5_source_type_label_when_nothing_else() -> None:
    """Layer 5: source-type friendly label wins when all title/filename
    layers are empty."""
    projection = build_reading_record_list_projection(
        record_title=None,
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="plain_text",
        original_input_filename=None,
        source_type="text",
    )
    assert projection.display_title == "粘贴文本"


def test_layer6_final_fallback() -> None:
    """Layer 6: final fallback '未命名解读' when everything is missing."""
    projection = build_reading_record_list_projection(
        record_title=None,
        generated_title_zh=None,
        title_generation_status=None,
        ready_candidate_title=None,
        original_input_type=None,
        original_input_filename=None,
        source_type=None,
    )
    assert projection.display_title == "未命名解读"


def test_whitespace_only_values_are_treated_as_empty() -> None:
    """Whitespace-only title / candidate / filename must not be used."""
    projection = build_reading_record_list_projection(
        record_title="   ",
        generated_title_zh="  ",
        title_generation_status="succeeded",
        ready_candidate_title="\t\n",
        original_input_type="plain_text",
        original_input_filename="  ",
        source_type="text",
    )
    # All layers 1-4 are empty; layer 5 yields the source-type label.
    assert projection.display_title == "粘贴文本"


# ---------------------------------------------------------------------------
# source_label mapping
# ---------------------------------------------------------------------------


def test_source_label_plain_text_no_filename() -> None:
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="plain_text",
        original_input_filename=None,
        source_type="text",
    )
    assert projection.source_label == "粘贴文本"


def test_source_label_file_ref_with_filename() -> None:
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="file_ref",
        original_input_filename="report.pdf",
        source_type="file",
    )
    assert projection.source_label == "上传文件 · report.pdf"


def test_source_label_url_no_filename() -> None:
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="url",
        original_input_filename=None,
        source_type="url",
    )
    assert projection.source_label == "网页链接"


def test_source_label_image_ref_with_filename() -> None:
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="image_ref",
        original_input_filename="scan.png",
        source_type="image",
    )
    assert projection.source_label == "图片 OCR · scan.png"


def test_source_label_falls_back_to_legacy_source_type() -> None:
    """When original_input_type is missing, use reading_records.source_type."""
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type=None,
        original_input_filename=None,
        source_type="pdf",
    )
    assert projection.source_label == "PDF 文档"


def test_source_label_unknown_type_falls_back_to_default() -> None:
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type=None,
        original_input_filename=None,
        source_type=None,
    )
    assert projection.source_label == "未命名解读"


def test_source_label_does_not_leak_raw_metadata() -> None:
    """source_label must be a controlled string, never raw metadata."""
    projection = build_reading_record_list_projection(
        record_title="X",
        generated_title_zh=None,
        title_generation_status="pending",
        ready_candidate_title=None,
        original_input_type="file_ref",
        original_input_filename="doc.pdf",
        source_type="file",
    )
    # The label must NOT contain raw metadata key names or JSON.
    assert "metadata_json" not in projection.source_label
    assert "source_ref" not in projection.source_label
    assert "{" not in projection.source_label
    assert "}" not in projection.source_label
