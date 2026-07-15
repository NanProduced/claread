"""S2.5: Reading Record Identity Projection (list read model).

Pure functions that turn raw Agentic Reading Record columns into the
safe ``display_title`` and ``source_label`` strings shown in Library and
the Sidebar recent-reading list.

Display title priority chain (decided in the backend, NOT in the UI):

    1. ``generated_title_zh`` when ``title_generation_status='succeeded'``
       and the value is non-empty.
    2. ``reading_records.title`` when non-empty.
    3. The current-generation ready candidate's ``title`` when exactly
       one exists (``ready_count = 1``). When 0 or 2+ ready candidates
       exist, this layer is skipped and the chain falls through to
       filename / source-type / fallback.
    4. The original input's safe ``filename`` (from
       ``original_inputs.metadata_json->>'filename'``).
    5. A user-friendly source-type label (e.g. "粘贴文本", "上传文件").
    6. Final fallback: ``"未命名解读"``.

``source_label`` is a controlled mapping from
``original_inputs.input_type`` (+ optional filename) to a Chinese
friendly string. Raw ``metadata_json`` is NEVER passed through to the
UI; only the projected ``source_label`` string leaves the backend.
"""

from __future__ import annotations

from dataclasses import dataclass

# Controlled source-type → friendly label map. The keys mirror the
# ``original_inputs.input_type`` CHECK constraint values
# (plain_text / markdown / file_ref / url / image_ref). We deliberately
# do NOT include legacy ``reading_records.source_type`` values (text /
# markdown / file / url / pdf / ocr / image) because the new read model
# derives the label from the more specific ``original_inputs.input_type``.
_SOURCE_TYPE_LABEL_MAP: dict[str, str] = {
    "plain_text": "粘贴文本",
    "markdown": "Markdown 文档",
    "file_ref": "上传文件",
    "url": "网页链接",
    "image_ref": "图片 OCR",
}

# Legacy reading_records.source_type → friendly label map, used only as
# a fallback when original_inputs.input_type is missing (e.g. legacy
# rows that predate the original_inputs table population).
_LEGACY_SOURCE_TYPE_LABEL_MAP: dict[str, str] = {
    "text": "粘贴文本",
    "markdown": "Markdown 文档",
    "file": "上传文件",
    "url": "网页链接",
    "pdf": "PDF 文档",
    "ocr": "图片 OCR",
    "image": "图片 OCR",
}

_FINAL_FALLBACK_TITLE = "未命名解读"


def _clean(value: str | None) -> str | None:
    """Return the stripped value if non-empty, else None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


@dataclass(frozen=True, slots=True)
class ReadingRecordListProjection:
    """Result of projecting a raw reading-record row into safe strings."""

    display_title: str
    source_label: str


def build_reading_record_list_projection(
    *,
    record_title: str | None,
    generated_title_zh: str | None,
    title_generation_status: str | None,
    ready_candidate_title: str | None,
    original_input_type: str | None,
    original_input_filename: str | None,
    source_type: str | None,
) -> ReadingRecordListProjection:
    """Compute the safe ``display_title`` and ``source_label`` for a
    single reading-record list item.

    See module docstring for the display_title priority chain. The
    function is pure and side-effect-free so the priority matrix can be
    unit-tested in isolation.

    Args:
        record_title: ``reading_records.title`` (may be None/empty).
        generated_title_zh: ``reading_records.generated_title_zh``
            (may be None).
        title_generation_status: ``reading_records.title_generation_status``
            (``pending`` / ``succeeded`` / ``failed_retryable``). Only
            ``succeeded`` unlocks layer 1.
        ready_candidate_title: The title of the current-generation
            ready candidate. The caller MUST pass ``None`` when the
            ready candidate count is 0 or 2+ (P2 fix); only pass the
            title when exactly one ready candidate exists.
        original_input_type: ``original_inputs.input_type`` for the
            earliest original input (may be None for legacy rows).
        original_input_filename: ``original_inputs.metadata_json->>'filename'``
            for the earliest original input (may be None).
        source_type: ``reading_records.source_type`` (legacy enum). Used
            only as a fallback for ``source_label`` when
            ``original_input_type`` is missing.
    """
    display_title = _compute_display_title(
        record_title=record_title,
        generated_title_zh=generated_title_zh,
        title_generation_status=title_generation_status,
        ready_candidate_title=ready_candidate_title,
        original_input_filename=original_input_filename,
        original_input_type=original_input_type,
        source_type=source_type,
    )
    source_label = _compute_source_label(
        original_input_type=original_input_type,
        original_input_filename=original_input_filename,
        source_type=source_type,
    )
    return ReadingRecordListProjection(
        display_title=display_title,
        source_label=source_label,
    )


def _compute_display_title(
    *,
    record_title: str | None,
    generated_title_zh: str | None,
    title_generation_status: str | None,
    ready_candidate_title: str | None,
    original_input_filename: str | None,
    original_input_type: str | None,
    source_type: str | None,
) -> str:
    """Apply the display_title priority chain."""
    # Layer 1: succeeded generated_title_zh (must be non-empty).
    if title_generation_status == "succeeded":
        cleaned = _clean(generated_title_zh)
        if cleaned is not None:
            return cleaned

    # Layer 2: reading_records.title (non-empty).
    cleaned_title = _clean(record_title)
    if cleaned_title is not None:
        return cleaned_title

    # Layer 3: current-generation ready candidate title (non-empty).
    cleaned_candidate = _clean(ready_candidate_title)
    if cleaned_candidate is not None:
        return cleaned_candidate

    # Layer 4: original input filename (non-empty).
    cleaned_filename = _clean(original_input_filename)
    if cleaned_filename is not None:
        return cleaned_filename

    # Layer 5: source-type friendly label.
    label = _lookup_source_type_label(
        original_input_type=original_input_type,
        source_type=source_type,
    )
    if label is not None:
        return label

    # Layer 6: final fallback.
    return _FINAL_FALLBACK_TITLE


def _compute_source_label(
    *,
    original_input_type: str | None,
    original_input_filename: str | None,
    source_type: str | None,
) -> str:
    """Compute the controlled source_label string.

    Format:
        - file-like types (file_ref / markdown / image_ref / pdf / ocr /
          image) with a filename: ``"<base label> · <filename>"``.
        - other types: just the base label.
        - unknown: ``"未命名解读"`` (matches the title fallback so the
          UI has a consistent shape).
    """
    base_label = _lookup_source_type_label(
        original_input_type=original_input_type,
        source_type=source_type,
    )
    if base_label is None:
        return _FINAL_FALLBACK_TITLE

    cleaned_filename = _clean(original_input_filename)
    file_like_types = {
        "file_ref",
        "markdown",
        "image_ref",
        # legacy source_type values
        "file",
        "pdf",
        "ocr",
        "image",
    }
    effective_type = original_input_type or source_type or ""
    if cleaned_filename and effective_type in file_like_types:
        return f"{base_label} · {cleaned_filename}"
    return base_label


def _lookup_source_type_label(
    *,
    original_input_type: str | None,
    source_type: str | None,
) -> str | None:
    """Resolve the friendly base label, preferring the new
    ``original_inputs.input_type`` over the legacy
    ``reading_records.source_type``.
    """
    if original_input_type:
        label = _SOURCE_TYPE_LABEL_MAP.get(original_input_type)
        if label is not None:
            return label
    if source_type:
        label = _LEGACY_SOURCE_TYPE_LABEL_MAP.get(source_type)
        if label is not None:
            return label
    return None
