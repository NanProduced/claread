# D2 Spikes

> 状态：`active`
> 最后更新：2026-06-18
> 用途：Reader agentic orchestration D2 技术验证入口。Spike 输出只记录短结论；被接受的长期结论写回 `target-architecture.md` 或对应 `modules/*.md`。

## 规则

- 每个 spike 目标是降低一个 D3/D4 实施风险，不做完整功能。
- 每个 spike 输出包括：verdict、关键数据、影响的正式文档、是否进入 D3。
- 不为旧 `render_scene_json` 或旧 `analysis_tasks` 做兼容验证。
- 旧开发数据不迁移；schema spike 只需确认如何保留词典三表。

## D2-P0 Plate Dependency / API / License

状态：`accepted_with_changes`

| 项 | 内容 |
|---|---|
| 问题 | Plate.js core、Markdown、comment/suggestion/AI 相关包是否可用于 Claread Reader？ |
| 输入文档 | `modules/plate-reader-projection.md` |
| 验证 | 当前 `platejs` 版本、React/Next 兼容、license、插件可用性、替代实现路径 |
| 输出 | dependency/license matrix、allowed packages、fallback plan |
| 通过标准 | 不依赖未验证商业能力；核心 Base Plate Snapshot 可基于已可用 package 实现。 |
| 结论 | Plate 53.x 稳定主线依赖对齐通过；`@claread/web` typecheck 与 test 通过。53.2.2 并非所有子包都有，实际采用各包 latest stable 53.x。 |

## D2-P1 Base Plate Snapshot

状态：`accepted`

| 项 | 内容 |
|---|---|
| 问题 | Stable Base / Units / Anchor Segments 能否直接生成 Plate Article Body，不经过旧 `render_scene_json`？ |
| 输入文档 | `modules/plate-reader-projection.md`、`modules/reading-base-and-units.md` |
| 验证 | Base Plate node schema、owner metadata、canonical text mapping、snapshot reload |
| 输出 | Base Plate Snapshot DTO、focused tests、example document |
| 通过标准 | D4 article_ready 可渲染原文和基础导航，且不依赖旧 scene contract。 |
| 结论 | 采用 `ReaderPlateSnapshot` wrapper、`reader_unit`、`reader_source_block`、`reader_anchor_segment`。Snapshot wrapper 使用 `schema_kind`；开发期不创建 `ReaderPlateSnapshotV1/V2` 类型；`sentence_id` 仅作兼容 alias。 |

## D2-P2 Projection Operations / Replay

状态：`accepted`

| 项 | 内容 |
|---|---|
| 问题 | domain-targeted projection ops 能否支持渐进式 Plate 更新与断线恢复？ |
| 输入文档 | `modules/streaming-and-projection.md`、`modules/plate-reader-projection.md` |
| 验证 | op idempotency、event order、snapshot reload、gap recovery、unresolved target fallback |
| 输出 | `projection_ops` envelope、frontend applier contract、replay tests |
| 通过标准 | 不持久化 raw Slate path ops；100 个 projection ops replay 后与 snapshot 重建一致。 |
| 结论 | 采用 domain-targeted `projection_ops`；gap、unresolved target、hash mismatch、policy failure 均触发 snapshot reload。 |

## D2-P3 Selection / Anchor / Owner

状态：`accepted_with_changes`

| 项 | 内容 |
|---|---|
| 问题 | Plate selection 能否稳定转为 Claread domain anchor，并执行 owner 权限？ |
| 输入文档 | `modules/plate-reader-projection.md`、`modules/enhancement-layers-and-parsed.md` |
| 验证 | selection -> anchor、UTF-16/hash、multi-segment selection、owner deny/allow matrix |
| 输出 | path adapter contract、owner policy tests、anchor round-trip tests |
| 通过标准 | 所有持久回写都使用 domain anchor；stable/system/user/ask 权限边界可测试。 |
| 结论 | 复用现有 UTF-16/hash、selection fallback 和 mark overlap 思路；后端不校验 raw path，`system_ai` truth 只能 hide/dismiss/revision。 |

## D2-P4 Ask Document Tools

状态：`accepted`

| 项 | 内容 |
|---|---|
| 问题 | Ask Claread 能否通过 document tools 读写 Reader Plate projection 的对应 domain facts？ |
| 输入文档 | `modules/plate-reader-projection.md`、`modules/orchestration-runtime.md` |
| 验证 | `read_range`、`propose_highlight`、`propose_note`、`write_ai_supplement`、`revise_ai_annotation`、用户确认 |
| 输出 | tool schema、Authorization Envelope checks、projection event examples |
| 通过标准 | Ask 不能直接改 Stable Base 或覆盖 User Editorial Assets；写入用户资产必须用户确认。 |
| 结论 | Ask 走 document tools；`revise_ai_annotation` 默认不直接覆盖 System Annotation Layer。 |

## D2-S1 Reading Unit Builder

状态：`accepted_with_changes`

| 项 | 内容 |
|---|---|
| 问题 | paragraph + sentence fallback 是否能生成稳定、可 anchor 的 Reading Units？ |
| 输入文档 | `modules/reading-base-and-units.md` |
| 验证 | UTF-16 offsets、`fnv1a32-utf16` hash、unit order、空行/长段落/Unicode 文本 |
| 输出 | `spikes/D2-S1-reading-unit-builder-result.md`；`modules/reading-base-and-units.md` 与 `modules/enhancement-layers-and-parsed.md` 已修正 |
| 通过标准 | D4 纯文本样本可生成 Stable Base + Units，并通过 anchor validation。 |
| 结论 | 方向通过；必须新增 Anchor Segment，span offsets 保持 Anchor Segment local；segment 通常是 sentence，必要时可为 clause/fallback window。 |

## D2-S2 DB Job Lease / Publish Guard

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | PostgreSQL-backed job lease 是否足以支撑 D4 worker？ |
| 输入文档 | `modules/orchestration-runtime.md` |
| 验证 | claim、heartbeat、lease expiry、stale recovery、retry budget、late worker publish guard |
| 输出 | SQL pattern、state transitions、publish transaction checklist |
| 通过标准 | late result、cancel、supersede、duplicate worker 都不能双写 layer。 |

## D2-S3 Policy Planner / Skip Gate

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | D4 是否能用 0-token deterministic Policy Planner 完成调度？ |
| 输入文档 | `modules/policy-and-cost-control.md` |
| 验证 | `policy.layer_applicable`、pre-claim Skip Gate、rationale_code、policy_version、skip/no-event 规则 |
| 输出 | policy table seed、Decision schema、focused tests |
| 通过标准 | translation job 的 run/skip/pause/reject 决策不需要 LLM。 |

## D2-S4 Translation Worker Structured Output

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | PydanticAI typed worker 是否适合 D4 translation layer？ |
| 输入文档 | `modules/enhancement-layers-and-parsed.md`、`modules/policy-and-cost-control.md` |
| 验证 | strict JSON schema、output retries、usage limits、max_tokens、provider transport |
| 输出 | TranslationResult schema、retry policy、model profile requirement |
| 通过标准 | 代表性 units 可稳定输出 schema-valid translation，并记录 usage。 |

## D2-S5 SSE / Polling Projection

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | reader_events + snapshot first + SSE/polling fallback 是否足够？ |
| 输入文档 | `modules/streaming-and-projection.md` |
| 验证 | record-scoped sequence、event id dedupe、gap detection、snapshot reload、business event 同事务写入 |
| 输出 | event sequence strategy、BFF transport choice、frontend recovery contract |
| 通过标准 | 客户端断线/重连/重复事件不会造成 layer 重复渲染或状态回退。 |

## D2-S6 Model Profile / Cost Baseline

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | 当前 DashScope / DeepSeek provider 哪些模型适合 D4 translation 和 D5 layers？ |
| 输入文档 | `modules/policy-and-cost-control.md` |
| 验证 | 官方 model id、structured output support、context limit、cache status、fallback chain、成本 |
| 输出 | `model_profiles` 字段确认、translation benchmark、fallback recommendation |
| 通过标准 | D4 translation profile 和 fallback profile 可配置化，不需要改代码切模型。 |

## D2-S7 Prompt Cache / Usage Bucket

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | provider cache 与 usage audit 是否能量化 token economy？ |
| 输入文档 | `modules/policy-and-cost-control.md` |
| 验证 | static prompt prefix、cache hit/miss reporting、`ai_usage_events` 扩展字段、usage aggregates |
| 输出 | usage field list、adapter metadata contract、`usage_by_layer` / `usage_by_cache_status` 草案 |
| 通过标准 | D4 translation 成本能按 record、layer、model profile、cache status 归因。 |

## D2-S8 RAG Substrate

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | Stable Base / Units 能否构建 record-scoped RAG substrate？ |
| 输入文档 | `modules/rag-substrate.md` |
| 验证 | shared collection + metadata filter、chunk hash、citation DTO、provider adapter |
| 输出 | vector store adapter requirement、citation validation tests、provider posture |
| 通过标准 | 查询结果必须可校验回当前 record/base/unit/hash。 |

## D2-S9 Length Class / Envelope Defaults

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | 不同长度文章的默认 unit range、token budget、continuation 语义是什么？ |
| 输入文档 | `modules/orchestration-runtime.md`、`modules/policy-and-cost-control.md` |
| 验证 | word/token estimate、short/medium/long/extra-long 边界、pause/resume/continue 行为 |
| 输出 | Length Class seed values、Authorization Envelope defaults |
| 通过标准 | D4 不会因超长文本无界入队，也不会阻塞短文 `article_ready`。 |

## D2-S10 Cutover / Old Dependency Audit

状态：`ready`

| 项 | 内容 |
|---|---|
| 问题 | 停服重构时哪些旧表/服务可删、改写或保留？ |
| 输入文档 | `modules/cutover-and-old-workflow.md` |
| 验证 | analysis、reader scene、Ask、User Editorial Assets、usage audit、Daily Reader 依赖 |
| 输出 | delete / rewrite / keep matrix、schema reset checklist |
| 通过标准 | D3 schema baseline 可重写，并明确保护词典三表与 Daily Reader 边界。 |

## 后续建议执行顺序

1. D2-P0 Plate Dependency / API / License
2. D2-P1 Base Plate Snapshot
3. D2-P2 Projection Operations / Replay
4. D2-P3 Selection / Anchor / Owner
5. D2-P4 Ask Document Tools
6. D2-S2 DB Job Lease / Publish Guard
7. D2-S3 Policy Planner / Skip Gate
8. D2-S4 Translation Worker Structured Output
9. D2-S5 SSE / Polling Projection
10. D2-S6 Model Profile / Cost Baseline
11. D2-S7 Prompt Cache / Usage Bucket
12. D2-S9 Length Class / Envelope Defaults
13. D2-S8 RAG Substrate
14. D2-S10 Cutover / Old Dependency Audit

D4 纵切的最低硬依赖是 D2-S1、D2-P0、D2-P1、D2-S2 到 D2-S7 和 D2-S9 的结论。当前执行顺序仍建议先完成 D2-P0 到 D2-P4，降低 Plate 方向的架构风险；如果 D4 只做 snapshot reload，不强制等待 D2-P2 到 D2-P4。若 D4 要做真实增量 Plate projection、用户选择锚点或 Ask document tools，则对应 spike 必须前置完成。RAG 与 cutover audit 可以并行，但不阻塞纯文本 translation 纵切。
