# RAG Substrate

> 状态：`D6 文档型 Reader 修订`
> 最后更新：2026-06-25
> 范围：当前 Reading Record 内 RAG substrate、document/block citation 和 provider 边界。

## 目标

本轮 RAG 只服务当前 Reading Record。

不做：

- 全局 User Editorial Assets RAG。
- 跨记录知识库。
- 把 Original Input 默认纳入 Ask 上下文。
- 把外部向量库当成 Claread 业务事实源。

## Truth Layer

默认检索源：

- Stable Reading Document
- Stable Document Blocks
- Canonical Text Layer
- Reading Units
- Anchor Segments

辅助检索源：

- Published Enhancement Layers
- Ask saved supplement，需单独治理

默认不进入检索：

- Original Input
- 未确认 Candidate Document
- 用户未确认写入的 Ask answer

Plate Reader Document 不作为 RAG truth layer。它可以提供 UI hint，例如当前 viewport、Plate node path cache 或 citation highlight target，但 citation 必须回源到 Stable Reading Document / Stable Document Blocks / Canonical Text Layer / Units / Anchor Segments。

V1 RAG substrate 应覆盖整个 Stable Reading Document，并用 `source_scope` 分层：

| source_scope | 来源 | 默认用途 |
|---|---|---|
| `main_reading_text` | paragraph、list item、blockquote、caption、适合阅读的 footnote text | Ask/RAG + 主解析引用 |
| `heading` | heading blocks | 结构上下文、导航、query rewrite |
| `table_cell` | table cell text | Ask/RAG 引用；默认不跑普通段落式 grammar/sentence analysis |
| `image_ocr` | image block 的 OCR text | Ask/RAG 引用；仅用户确认“作为正文阅读”后进入主解析 |
| `footnote` | footnote block | Ask/RAG 引用；主解析低优先级 |
| `code_block` | code block text | Ask/RAG 引用；默认不做英语学习解析 |
| `published_layer` | 已发布 enhancement layer | 辅助上下文，需单独治理 |

## Storage Posture

默认不采用 collection-per-record。

D1 默认策略：

- shared collection
- metadata filter by `tenant_id` / `user_id` / `reading_record_id` / `stable_document_id` / `base_id`
- optional partition only after D2 provider spike 证明必要

这样避免 per-record collection 创建成本和管理复杂度。

RAG substrate schema 至少需要保存：

- substrate identity、record/base/document/generation。
- chunker version、embedding profile、provider、collection / index metadata。
- chunk source：`block_id`、`block_type`、`source_scope`、可选 `unit_id` / `anchor_segment_id`。
- content hash、snippet、canonical text offsets 或 block-local offsets。
- provider vector id / provider ref。

## Query Contract

任何 RAG 查询必须带：

- `tenant_id` / `user_id`
- `reading_record_id`
- `base_id`
- `stable_document_id` 或等价 document/base identity
- `rag_substrate_id`
- `allowed_source_scope`
- `block_range`、`unit_range` 或 viewport/context range
- `query_intent`

默认 pipeline：

```text
query rewrite within record scope
-> metadata filter
-> vector search
-> optional hybrid/full-text/rerank after spike
-> return cited block/unit refs + snippets
-> answer generation with citation requirement
```

D4 不阻塞 RAG。`article_ready` 不等待 `substrate_ready`。

## Citation DTO

RAG result 必须返回可校验引用：

```json
{
  "rag_substrate_id": "rag_...",
  "stable_document_id": "doc_...",
  "base_id": "base_...",
  "block_id": "block_...",
  "block_type": "paragraph",
  "source_scope": "main_reading_text",
  "unit_id": "unit_...",
  "unit_text_hash": "1a2b3c4d",
  "hash_algorithm": "fnv1a32-utf16",
  "anchor_segment_id": "s1",
  "segment_text_hash": "2b3c4d5e",
  "base_start_utf16": 0,
  "base_end_utf16": 120,
  "block_start_offset": 0,
  "block_end_offset": 120,
  "snippet": "...",
  "score": 0.82,
  "provider_ref": {
    "provider": "zilliz",
    "collection": "claread_reader_chunks",
    "vector_id": "..."
  }
}
```

Ask 接受 citation 前必须校验：

- `stable_document_id` / `base_id` 是当前 Reading Record 的 active Stable Reading Document / base。
- `block_id` 属于当前 Stable Reading Document，且 `source_scope` 与 block 类型匹配。
- 如提供 `unit_id`，该 unit 必须属于当前 record/base。
- 如提供 `unit_text_hash`，它必须是 raw 8-char `fnv1a32-utf16`，并与当前 unit 匹配。
- 如提供 `anchor_segment_id`，该 segment 必须属于当前 unit，且 `segment_text_hash` 匹配。
- `base_start_utf16` / `base_end_utf16` 或 block-local offsets 必须 slice 回 `snippet` 或包含 `snippet` 的 chunk source。
- source scope 在 Authorization Envelope 允许范围内。

主阅读文本 citation 应尽量回到 `unit_id` / `anchor_segment_id`。table、image OCR、footnote、code 等非主解析块可以只回到 `block_id` + block-local offsets，但必须能在 Reader Plate projection 中定位对应文档块。

## Provider 边界

阶段性建议：

- D2 测试阶段优先接入当前已配置的 Zilliz Cloud。
- 上线前评估阿里云 RAG / 向量检索服务 / 百炼知识库。
- 代码通过 `VectorStoreAdapter` / `KnowledgeRetrievalAdapter` 隔离供应商。

首版可以 vector-only + metadata filter。Hybrid retrieval、full-text 和 rerank 不进入 D4。

RAG worker 不应先做成只绑定线性文本的 chunk store。Stable Document Block contract 是 RAG V1 的前置任务；否则 table/image/footnote citation 会在后续返工。

## D2 Spike

D2 需要验证：

- Stable Reading Document / Blocks / Canonical Text / Units -> chunks -> embeddings -> vector search。
- shared collection + metadata filter 的正确性和性能。
- block-scoped 与 unit-scoped citation DTO 校验。
- Zilliz 与阿里云候选 provider 的 adapter 可替换性。
