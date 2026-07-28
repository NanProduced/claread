"""L2 — 输入内容格式检测（detected_format）。

把"输入来源"（``source_type``：pasted_text / markdown_file / artifact
等）与"内容格式"（plain_text / markdown / rich_html）解耦：

* ``source_type`` 只描述输入**来自哪里**；
* ``detected_format`` 描述内容**是什么结构**，由 MarkdownSourceParser
  的块结构唯一决定（parser 是块结构的 single source of truth）。

gate、candidate 块构造、normalizer 三方共用同一判定与同一份
``MarkdownParseResult``（每请求只解析一次，见 A4 preparsed 机制）。
禁止再用 ``source_type == "markdown_file"`` 决定是否保留结构。
"""

from __future__ import annotations

from app.schemas.reader_input_adapter import (
    DetectedInputFormat,
    InputAdapterSourceType,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownParseResult,
)


def detect_input_format(
    *,
    source_type: InputAdapterSourceType,
    parse_result: MarkdownParseResult,
) -> DetectedInputFormat:
    """Detect the content format from parser block structure.

    * ``markdown_file`` 来源显式声明 Markdown，恒为 ``markdown``
      （保持既有行为：即使全文只有段落也走 parser 块路径）。
    * 其他来源（pasted_text / txt_file / ocr_text / pdf_text /
      url_text）：parser 产出任何非 ``paragraph`` 块（heading / list /
      blockquote / table / code_block / thematic_break …）即为
      ``markdown``；只有段落的输入保持 ``plain_text``。
    * ``rich_html`` 为保留值，当前不由本函数产出。
    """
    if source_type == "markdown_file":
        return "markdown"
    if any(block.block_type != "paragraph" for block in parse_result.blocks):
        return "markdown"
    return "plain_text"
