"""LLM cover selection, Chinese caption generation and layout tagging (B-1).

One cheap multimodal call per article (route ``daily_cover``) picks 1 cover
+ 0-1 inline image from the pixel-qualified candidates and writes a Chinese
caption grounded in the article title + text excerpt — never from the image
alone (decision-record §5). Degradation path: no multimodal model or call
failure → first qualified candidate, no caption; the chain never breaks.

Layout tags map dimensions to the three fixed rendering slots of the daily
reader surface brief (full-bleed / two-third / half-float); Track C renders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import get_settings
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.routes import MODEL_ROUTE_DAILY_COVER
from app.services.daily_reader.cover_download import ValidatedCandidate

logger = logging.getLogger(__name__)

LAYOUT_FULL_BLEED = "full-bleed"
LAYOUT_TWO_THIRD = "two-third"
LAYOUT_HALF_FLOAT = "half-float"

SELECTION_MODE_LLM = "llm"
SELECTION_MODE_FALLBACK_FIRST = "fallback_first"

_TEXT_EXCERPT_CHARS = 1500


def layout_for_dimensions(width: int, height: int) -> str:
    """Map aspect ratio to the three fixed layout slots (surface brief §4)."""
    if height <= 0:
        return LAYOUT_TWO_THIRD
    ratio = width / height
    if ratio >= 1.9:
        return LAYOUT_FULL_BLEED
    if ratio >= 1.25:
        return LAYOUT_TWO_THIRD
    return LAYOUT_HALF_FLOAT


def build_image_block(
    *,
    block_id: str,
    role: str,
    url: str,
    width: int,
    height: int,
    caption_zh: str = "",
    source_caption: str = "",
) -> dict:
    """body_json image block contract (additive; rendering is Track C)."""
    return {
        "id": block_id,
        "role": role,  # cover | inline
        "url": url,
        "width": width,
        "height": height,
        "layout": layout_for_dimensions(width, height),
        "caption_zh": caption_zh.strip() or None,
        "source_caption": source_caption.strip() or None,
    }


@dataclass
class SelectedImage:
    index: int
    caption_zh: str


@dataclass
class CoverSelection:
    mode: str
    cover: SelectedImage | None = None
    inline: SelectedImage | None = None


class _CoverSelectOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    cover_index: int = Field(description="封面图片编号（0 开始）")
    cover_caption_zh: str = Field(default="", description="封面中文图说（一句话）")
    inline_index: int | None = Field(default=None, description="正文插图编号，可为空")
    inline_caption_zh: str | None = Field(default=None, description="正文插图中文图说")


def _build_cover_prompt(title: str, text_excerpt: str, count: int) -> str:
    return f"""你是一本中英双语精读刊物的图片编辑。
请根据文章标题与正文片段为文章挑选配图并撰写中文图说。

文章标题：{title}

文章开头/相关段落：
{text_excerpt}

候选图片按编号 0 到 {count - 1} 依次附在后面。
要求：
1. 选出 1 张与文章主题最相关、视觉质量最好的图片作为封面（cover_index）。
2. 如果还有另一张承载文章关键信息、适合放在正文中的图片，
可另选 1 张作为插图（inline_index）；没有合适的则留空。
3. 为每张选中的图片写一句中文图说。图说必须依据文章标题与段落内容撰写，
点出图片与文章的关系；禁止仅凭画面臆测或编造文章未提及的事实。"""


async def select_cover_images(
    *,
    title: str,
    text_excerpt: str,
    candidates: list[ValidatedCandidate],
) -> CoverSelection:
    """Select cover (+optional inline) and generate captions.

    Never raises: any model/LLM failure degrades to the first qualified
    candidate with no caption (SELECTION_MODE_FALLBACK_FIRST).
    """
    if not candidates:
        return CoverSelection(mode=SELECTION_MODE_FALLBACK_FIRST)

    def _fallback() -> CoverSelection:
        # Deterministic no-LLM pick: widest qualified candidate (tie → first),
        # so degraded runs still meet the >=1200px quality bar.
        best = max(range(len(candidates)), key=lambda i: candidates[i].width)
        return CoverSelection(
            mode=SELECTION_MODE_FALLBACK_FIRST,
            cover=SelectedImage(index=best, caption_zh=""),
        )

    model = None
    model_config = None
    try:
        from app.llm.router import build_model_for_route

        settings = get_settings()
        model, model_config = build_model_for_route(settings, MODEL_ROUTE_DAILY_COVER)
    except Exception as exc:
        logger.warning("daily_cover model resolution failed: %s", exc)

    if model is None:
        logger.warning(
            "daily_cover model unavailable; using first qualified candidate without caption"
        )
        return _fallback()

    try:
        assert_real_llm_allowed(
            "app.services.daily_reader.cover_select.select_cover_images",
            model_config=model_config,
        )
        output = await _run_cover_select_span(
            title=title,
            text_excerpt=text_excerpt,
            candidates=candidates,
            model=model,
        )
    except Exception as exc:
        logger.warning(
            "LLM cover selection failed, falling back to widest qualified candidate: %s",
            exc,
        )
        return _fallback()

    if output is None or not (0 <= output.cover_index < len(candidates)):
        logger.warning("LLM cover selection returned invalid index; falling back")
        return _fallback()

    selection = CoverSelection(
        mode=SELECTION_MODE_LLM,
        cover=SelectedImage(index=output.cover_index, caption_zh=output.cover_caption_zh),
    )
    if (
        output.inline_index is not None
        and 0 <= output.inline_index < len(candidates)
        and output.inline_index != output.cover_index
    ):
        selection.inline = SelectedImage(
            index=output.inline_index,
            caption_zh=output.inline_caption_zh or "",
        )
    return selection


@traceable(name="daily_cover_select_llm_call", run_type="llm")
async def _run_cover_select_span(
    *,
    title: str,
    text_excerpt: str,
    candidates: list[ValidatedCandidate],
    model: object,
) -> _CoverSelectOutput | None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import BinaryImage

    agent = Agent(
        model=model,
        output_type=_CoverSelectOutput,
        name="daily_cover_select_agent",
        retries=1,
        output_retries=2,
        instrument=False,
    )

    # agent.run accepts Sequence[UserContent]: plain text + image parts.
    prompt_parts: list = [
        _build_cover_prompt(
            title, (text_excerpt or "")[:_TEXT_EXCERPT_CHARS], len(candidates)
        )
    ]
    for candidate in candidates:
        media_type = candidate.fetched.content_type.split(";")[0].strip().lower()
        if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            media_type = "image/jpeg"
        prompt_parts.append(BinaryImage(data=candidate.fetched.data, media_type=media_type))

    result = await agent.run(prompt_parts)
    return result.output
