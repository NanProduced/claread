# RAG Substrate

> 状态：`D2-S1 修订`
> 最后更新：2026-06-18
> 范围：当前 Reading Record 内 RAG substrate、citation 和 provider 边界。

## 目标

本轮 RAG 只服务当前 Reading Record。

不做：

- 全局 User Editorial Assets RAG。
- 跨记录知识库。
- 把 Original Input 默认纳入 Ask 上下文。
- 把外部向量库当成 Claread 业务事实源。

## Truth Layer

默认检索源：

- Stable Reading Base
- Reading Units
- Anchor Segments

辅助检索源：

- Published Enhancement Layers
- Ask saved supplement，需单独治理

默认不进入检索：

- Original Input
- 未确认 Candidate Base
- 用户未确认写入的 Ask answer

Plate Reader Document 不作为 RAG truth layer。它可以提供 UI hint，例如当前 viewport、Plate node path cache 或 citation highlight target，但 citation 必须回源到 Stable Base / Units / Anchor Segments。

## Storage Posture

默认不采用 collection-per-record。

D1 默认策略：

- shared collection
- metadata filter by `tenant_id` / `user_id` / `reading_record_id` / `base_id`
- optional partition only after D2 provider spike 证明必要

这样避免 per-record collection 创建成本和管理复杂度。

## Query Contract

任何 RAG 查询必须带：

- `tenant_id` / `user_id`
- `reading_record_id`
- `base_id`
- `rag_substrate_id`
- `allowed_source_scope`
- `unit_range` 或 viewport/context range
- `query_intent`

默认 pipeline：

```text
query rewrite within record scope
-> metadata filter
-> vector search
-> optional hybrid/full-text/rerank after spike
-> return cited unit refs + snippets
-> answer generation with citation requirement
```

D4 不阻塞 RAG。`article_ready` 不等待 `substrate_ready`。

## Citation DTO

RAG result 必须返回可校验引用：

```json
{
  "rag_substrate_id": "rag_...",
  "base_id": "base_...",
  "unit_id": "unit_...",
  "unit_text_hash": "1a2b3c4d",
  "hash_algorithm": "fnv1a32-utf16",
  "anchor_segment_id": "s1",
  "segment_text_hash": "2b3c4d5e",
  "source_scope": "reading_unit",
  "base_start_utf16": 0,
  "base_end_utf16": 120,
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

- `base_id` 是当前 Reading Record 的 Stable Base。
- `unit_id` 属于当前 record。
- `unit_text_hash` 是 raw 8-char `fnv1a32-utf16`，并与当前 unit 匹配。
- 如提供 `anchor_segment_id`，该 segment 必须属于当前 unit，且 `segment_text_hash` 匹配。
- `base_start_utf16` / `base_end_utf16` 必须 slice 回 `snippet` 或包含 `snippet` 的 chunk source。
- source scope 在 Authorization Envelope 允许范围内。

## Provider 边界

阶段性建议：

- D2 测试阶段优先接入当前已配置的 Zilliz Cloud。
- 上线前评估阿里云 RAG / 向量检索服务 / 百炼知识库。
- 代码通过 `VectorStoreAdapter` / `KnowledgeRetrievalAdapter` 隔离供应商。

首版可以 vector-only + metadata filter。Hybrid retrieval、full-text 和 rerank 不进入 D4。

## D2 Spike

D2 需要验证：

- Stable Base / Units -> chunks -> embeddings -> vector search。
- shared collection + metadata filter 的正确性和性能。
- citation DTO 校验。
- Zilliz 与阿里云候选 provider 的 adapter 可替换性。
