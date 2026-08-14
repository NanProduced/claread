"""Thread memory mapping layer（ H6 + H7 处理约定）。

H6 处理约定（ §4.2(d) 步骤 7 注释 §13.2 H6）：
    InternalCitationBinding → SourceBinding 映射有损（InternalCitationBinding
    无 ``validity_check`` / ``fence_type`` / ``source_id`` 字段，``fence_values``
    散落于 ``rag_citation`` 与 ``evidence_scope`` 两处）。``derive_source_bindings``
    在 Host 侧实现映射，``validity_check`` 由 Host 调用 citation_navigation 计算
    （ agent 负责的 fence.py），compactor 仅消费已派生的 SourceBinding，不接触
    InternalCitationBinding 原始字段。

H7 处理约定（ §4.2(d) 步骤 7 注释 §13.2 H7）：
    web citation 降级无代码路径——``web_evidence_registry.get`` 只有 match/raise
    两态（fail-closed 是核心安全不变量，不可改为三态）。
    ``degrade_web_citation_to_hint`` 捕获 ``ValueError``（fingerprint 失配）后产出
    free-text hint，不进 ``A_bind`` allowlist（不作为本轮 citation truth）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.services.reader_record_ask.thread_memory.schema import SourceBinding


def derive_source_bindings(
    turn_run_bindings: list[dict[str, Any]],
) -> list[SourceBinding]:
    """H6: 从 InternalCitationBinding（ID-only dict）派生 SourceBinding 列表。

    输入：``ThreadMemoryRepository.list_bindings_for_compaction`` 返回的 dict
    列表——每个 dict 仅含 ID 类字段（``citation_id`` / ``handle_id`` /
    ``source_kind`` / ``unit_id`` / ``anchor_segment_id`` / ``kind`` /
    ``source_tool``）+ ``rag_citation`` + ``turn_run_id``，不含 ``snippet`` /
    ``canonical_url`` 等内容字段。

    fence_type 推导：
        - article 且 ``rag_citation.stable_document_id`` 非空 → 'stable_document'
        - article 且无 stable_document_id → 'reading_record'
        - web → 'reading_record'

    ``validity_check`` 初始为 ``{'status': 'unchecked', 'last_validated_turn': 0}``；
    fence 复核在 fence.py 中由 agent 实现，本层不接触。

    本函数不接触 InternalCitationBinding 的 ``snippet`` / ``canonical_url`` 等
    内容字段（仅取 ID 类字段）。web binding 无 ``rag_citation``，其 ``source_id``
    取 ``handle_id``（H6 有损映射：``canonical_url`` 属内容字段，本层不接触）。
    """
    result: list[SourceBinding] = []
    for b in turn_run_bindings:
        source_kind = b.get("source_kind")
        rag = b.get("rag_citation") or {}
        if not isinstance(rag, dict):
            rag = {}
        binding_id = b.get("citation_id") or b.get("handle_id") or ""
        if not binding_id:
            continue

        if source_kind == "article":
            stable_document_id = rag.get("stable_document_id") or ""
            reading_record_id = rag.get("reading_record_id") or ""
            if stable_document_id:
                fence_type = "stable_document"
                source_id = stable_document_id
            else:
                fence_type = "reading_record"
                source_id = reading_record_id
            fence_values: dict[str, Any] = {
                "reading_record_id": reading_record_id,
                "stable_document_id": stable_document_id,
                "base_id": rag.get("base_id") or "",
                "record_generation": (
                    str(rag["record_generation"])
                    if rag.get("record_generation") is not None
                    else ""
                ),
            }
        elif source_kind == "web":
            # web binding 无 rag_citation；source_id 取 handle_id（H6 有损映射：
            # InternalCitationBinding.canonical_url 属内容字段，本层不接触）。
            fence_type = "reading_record"
            source_id = b.get("handle_id") or ""
            fence_values = {}
        else:
            # 未知 source_kind：跳过（不进入 compaction 输入）。
            continue

        result.append(
            SourceBinding(
                binding_id=binding_id,
                source_type=source_kind,
                source_id=source_id,
                fence_type=fence_type,
                fence_values=fence_values,
                validity_check={
                    "status": "unchecked",
                    "last_validated_turn": 0,
                },
            )
        )
    return result


def degrade_web_citation_to_hint(binding: dict[str, Any]) -> dict[str, Any]:
    """H7: 将 web citation 降级为 free-text 搜索线索。

    设计为捕获 ``ValueError``（fingerprint 失配）后的降级路径：
    ``web_evidence_registry.get`` 只有 match/raise 两态（fail-closed 核心安全
    不变量），本函数提供第三态——产出 free-text hint，不进 ``A_bind`` allowlist
    （不作为本轮 citation truth，仅作 prior context / 搜索线索，冻结决策 #6）。

     输出 WebSearchHint dict 含 ``display_domain`` / ``retrieved_at``
    / ``web_title``，**永不含 ``canonical_url`` / ``source_fingerprint``**。
    canonical URL 和 fingerprint 是内容字段，降级后的 hint 不可保留它们
    （否则破坏 fail-closed 安全不变量——hint 是 free-text，不应携带可被
    注入或重放的来源指针）。``display_domain`` 从 URL 解析得到，仅保留
    netloc/hostname，不保留完整 URL。本函数本身不抛异常（任何解析失败均
    降级为空 hint）。
    """
    try:
        canonical_url = binding.get("canonical_url") or ""
        retrieved_at = binding.get("retrieved_at") or ""
        web_title = binding.get("web_title") or ""

        display_domain = ""
        if canonical_url:
            parsed = urlparse(canonical_url)
            display_domain = parsed.netloc or parsed.hostname or ""

        return {
            "display_domain": display_domain,
            "retrieved_at": retrieved_at,
            "web_title": web_title,
        }
    except Exception:
        return {
            "display_domain": "",
            "retrieved_at": "",
            "web_title": "",
        }
