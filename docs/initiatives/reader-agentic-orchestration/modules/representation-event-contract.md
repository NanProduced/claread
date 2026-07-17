# Snapshot Representation Event Contract

> 状态：`已接受设计；O4-R2 A+B+C + O4-R2-D 已完成；PUX-R4 / SSE 仍未实施；T5.1 L0/L1 为 accepted-snapshot 本地投影；T5.3 semantic_outline durable layer 已发布 `layer_published` 但不进 snapshot`
> 最后更新：2026-07-17（T5.3b：补 semantic outline durable / snapshot 边界；不扩 transport）
> 范围：会改变 Reader Plate snapshot 可观察表示的写入，及其 `reader_events` 合同。

## 目标与边界

本文解决 LP-R4 发现的三个 representation coverage gap：

| Gap | 业务事实 | 统一事件类型 | 目的 |
|---|---|---|---|
| G1 | 用户高亮、笔记等 User Editorial Assets | `projection_ops` | 使用户资产的创建、更新、删除或 merge 可被页面 catch-up。 |
| G2 | `reader_ask_supplements` | `projection_ops` | 使 Ask 补充内容的创建、恢复、更新或删除可被页面 catch-up。 |
| G3 | 用户可见的 record metadata，例如 display-title 状态 | `record_state_changed` | 使记录元数据变化不会只推进 cursor 而留下陈旧 snapshot。 |

它是 `reader_events` 的 representation-change 子合同；通用 envelope、sequence、gap detection、polling 与未来 SSE 语义仍以 [`streaming-and-projection.md`](./streaming-and-projection.md) 为准。

本文不批准 ETag、304、压缩、fragment route、SSE、WebSocket、JSON Patch 或 Plate 增量 applier。`snapshot_id` 仍不是 HTTP ETag。

## 已接受的架构决策

1. `reader_events` 是同一 PostgreSQL 内的 **transactional reader event log**，不是跨系统 outbox。业务事实、record-scoped sequence 与 event insert 必须在同一事务内提交；不新增 outbox、CDC、relay worker 或 broker。
2. G1/G2 复用既有 `projection_ops`，G3 复用既有 `record_state_changed`；本阶段不新增 event type、不做 migration。
3. 事件表达稳定的领域变化；客户端决定如何投递、回退或应用。`full_snapshot_until_pux_r4`、`cursor_only` 等 rollout / consumer 策略不得持久化在 event payload 中。
4. `id` 是事件 occurrence identity，`sequence` 是 per-record catch-up cursor，`generation + base_id` 是 representation scope fence；三者不可互相替代。
5. 任何失败、stale generation/base、或真实 no-op 都不得留下 event、sequence 或客户端 cursor 前进。是否重试写事务沿用或显式选择现有 writer retry policy；O4-R2 不得借此引入全局 serialization-retry 机制。

## 事务与可见性不变量

对 G1/G2/G3 的每个实际表示变化，写入方必须在同一事务中完成：

1. 校验 record ownership、active `generation` 与 `base_id` fence；
2. 写入或更新业务事实；
3. 分配该 record 的 sequence；
4. 校验、脱敏并插入 `reader_events`；
5. commit 后才允许 polling（以及未来 transport）读取。

任一步失败均 rollback。尤其不允许“先 commit facts，再异步补 signal”。真 no-op（如 `ON CONFLICT DO NOTHING`、已删除、幂等重复 merge）不得分配 sequence；内容或可见性确实变化才发一个 event。

## 稳定 payload v1

外层 envelope 已提供 `id`、`reading_record_id`、`sequence`、`event_type` 与 `created_at`。representation payload 只放解释这次领域变化所必需的稳定字段：

```json
{
  "schema_version": 1,
  "representation_section": "user_assets",
  "operation": "upsert",
  "generation": 7,
  "base_id": "base_...",
  "target_keys": ["asset_..."]
}
```

允许的 `representation_section` 为 `user_assets`、`ask_supplements`、`record_metadata`。`operation` 是受限词表（例如 `upsert`、`delete`、`merge`、`status_changed`）；G3 另以 allowlist 表达变更 metadata field。`target_keys`、asset/supplement identifier 必须是稳定 opaque domain identifier，不得是 raw Plate/Slate path。

以下内容禁止进入 payload：用户选中文本、笔记正文、Ask prompt/answer、layer raw output、认证信息、内部 diagnostics、stack trace，及任何以 transport rollout 命名的字段（包括 `reload_policy`）。payload schema、serialized byte size、target count 与 key length 必须由集中 validator fail-closed 校验；O4-R2 在实现前确定具体上限并加入负向测试。超限不得截断为看似可应用的事件，应 rollback 或发出既有 reset/reload signal。

## G1/G2/G3 映射与客户端分类

| 变化 | 事件 payload 事实 | 当前客户端（尚无 applier） | PUX-R4+ 客户端 |
|---|---|---|---|
| G1 user asset | `projection_ops` + `user_assets` + operation + opaque target | full snapshot reload | 仅在 schema/op/target 均支持时局部应用；否则 reload。 |
| G2 Ask supplement | `projection_ops` + `ask_supplements` + operation + opaque target | full snapshot reload | 同上。 |
| G3 用户可见 record metadata（含 title pending / terminal 状态） | `record_state_changed` + `record_metadata` + allowlisted field | full snapshot reload | 有对应 metadata reducer 后局部更新；否则 reload。 |
| 不影响当前 snapshot 的已验证状态变化 | 不得伪装成 representation change | cursor-only 可接受 | cursor-only 可接受。 |

当前 polling 不能仅按 `event_type` 一概 reload 或一概 cursor-only。O4-R2 应建立唯一的 payload-aware classifier：只有已知 schema、section、operation、generation/base fence 和受支持 target 才可选择局部 apply；当前不具备 applier 的 G1/G2 一律 reload。未知 schema、未知 operation、target 不完整、generation/base 不一致或 event gap 一律走可靠的 snapshot reload / reset 路径。

特别地，G3 的 `title_generation_status = pending` 是否最终保留在公共 snapshot 仍是产品/API 决策；在它仍可观察的当前合同下，**不得**把该事件标成 cursor-only。O4-R2 采用 full snapshot fallback；日后若引入受测的 record-metadata reducer，才可单独改变消费者策略。

## Reset、重放与未来 transport

`projection_reset_required` 只表示消费者无法安全连续消费（gap、未知不兼容 schema、丢失 target、generation/base fence 失败或受限 payload），不是常规内容更新事件。snapshot 永远是恢复 domain truth 的可靠路径。

未来 SSE 只替换通知/事件传输，不反向定义本合同；其 Last-Event-ID 只是经授权、经 generation/base/gap 校验的 reconnect hint，不是 representation revision。PUX-R4 将另行定义 applier checkpoint、幂等和 interaction-preserving local projection；它不可以重写已持久化事件的领域含义。

## Polling / Page Seam：accepted/rejected snapshot 合同边界

> 来源：T4.2a-PUX-R4-R3-R1 闭合（commit `9a925f82`）。本节固化 polling/page seam 与 Reader Plate Surface 之间关于 accepted/rejected snapshot 的合同边界，防止把 Surface 的 duplicate-snapshot guard 误称为 stale/fence rejection。

### accepted / rejected 定义

- **accepted snapshot**：通过 polling/page seam 的 generation/base fence 与单调 cursor 校验的 snapshot，被允许进入 Surface value swap。
- **rejected snapshot**：在 polling/page seam 被 stale sequence、generation/base fence 失败或 layer regression 拦截的 snapshot，**不得**进入 Surface value swap。cursor hold，当前 accepted UI 保持。

### Surface 的 same-snapshot early-return 不是 rejection

Reader Record Plate Surface 对 `snapshot_id` 与上次 targeted apply 相同的 accepted snapshot 做 early-return，仅是 **duplicate accepted snapshot guard**（防止同一 accepted snapshot 重复走 value swap / merger 路径），不承担 stale/fence rejection 语义。stale/fence rejection 只发生在 polling/page seam。

### 跨 seam 的不变量

1. rejected snapshot 的拒绝信号由 polling/page seam 产出（progressive-status `data-last-rejected` + `data-reject-reason`），Surface 不产生 rejection 标记。
2. rejected snapshot 不得触发 Surface `setValue` / merger / targeted apply；当前 accepted UI（含已打开的 Quick Peek、grammar accordion、selection）必须保持。**确定性导航 rail 状态不得由 rejected snapshot 驱动交换**（不得用拒绝值重建 L0/L1 items 或 target map）。
3. accepted snapshot 的 value swap 路径可与 Surface duplicate-snapshot guard 叠加：同一 `snapshot_id` 的重复 accepted 推送被 guard 跳过，但不改变其 accepted 身份。
4. source identity（`{generation, base_id}`）变化时，polling/page seam 与 Surface 必须协同清理：seam 侧拒绝旧 source 的 in-flight snapshot，Surface 侧清理 selection、Quick Peek、anchor、restore token 与 grammar expansion（见 [`reader-record-plate-surface-ui.md`](./reader-record-plate-surface-ui.md) Quick Peek source-identity close）；导航 rail 同步按 `sourceIdentityKey = base_id:generation` 清空 active / focus / scroll-lock / target cache（见同文件 [Deterministic Navigation](./reader-record-plate-surface-ui.md#deterministic-navigation-l0--l1)）。

### Deterministic Navigation 与 accepted snapshot 边界

> 来源：T5.1 L0/L1 闭合。导航不扩 representation event 合同；仅明确与 accepted/rejected 边界的关系。

- **L0 / L1 是 accepted snapshot 上的本地 deterministic projection**（`projectReaderRecordNavigation` + `ReaderRecordNavigationRail`）。不新增 layer、`reader_events` 类型、polling 协议字段或 transport。
- 导航只消费 **已 accepted** 的 snapshot + 当前 Plate document；rejected snapshot **不得**进入 Surface 或导航状态交换。
- Surface 的 same-snapshot early-return 仍只是 **duplicate accepted snapshot guard**，不等于 stale/fence rejection；也不能替代导航侧的 source-identity reset 或 target-cache revalidation。
- Plate `setValue` remount 后的 DOM 节点失效由前端 validated target resolver 处理（`isConnected` + 当前 plate document 归属 + unit_id 匹配）；这是本地投影正确性，不是新的 event 语义。
- **明确未批准**：SSE、WebSocket、JSON Patch、ETag/304、通用 Plate tree diff 作为导航或 outline 交付通道。

### Semantic outline durable layer 与 snapshot 边界

> 来源：T5.2a `2bf3db97` + T5.3 `781e4117`（T5.3b 文档同步）。本节只固定 **event / snapshot 可观察边界**；worker/publisher 细节见 [`implementation-plan.md`](../implementation-plan.md#t53-semantic-outline-worker--durable-layer)。

- **Durable truth**：published `enhancement_layers` 行，`layer_type='semantic_outline'`，`target_scope='record'`，`target_key='document'`。成功发布走既有 `reader_events.event_type='layer_published'`（payload 含 `layer_type` / target / generation），**不**新增 event type、**不**新增 representation_section。
- **当前 snapshot 合同**：`ReaderPlateSnapshot` **仍不**挂 `semantic_outline` 字段或专用 projection；T5.2a schema 注释与 T5.3 实现均保持该边界。客户端若仅靠 snapshot 重建页面，**不得**假设 outline 已可见。
- **发布 fail-closed**：validator `V=0` / failed / stale，或 job lease / route / provenance / target-key fence 失败时，**不** insert layer、**不**分配 sequence、**不**发 `layer_published`；旧同源 published outline 保持。
- **与 L0/L1**：outline **不得**写入或改写 `navigation.units`；L0/L1 继续只做 accepted-snapshot 本地投影。outline **不**阻塞 `article_ready`。
- **下一门**：T5.4-R0 设计如何（若）把 published outline 暴露进 snapshot / DTO；T5.5 才做 UI。在此之前，`layer_published`（`layer_type=semantic_outline`）对当前无 outline applier 的 Web 仍按既有 `layer_published` 可靠 reload 分类即可，但 reload 后的 snapshot **仍不含** outline projection。
- **明确未批准**：不得借 outline durable 落地批准 SSE、WebSocket、JSON Patch、ETag/304、压缩或通用 tree diff。

### 未批准的传输改造

本节不批准 SSE、WebSocket、JSON Patch、ETag/304、压缩或通用 tree diff。accepted/rejected 判定仍基于 polling/page seam 的 full snapshot reload 合同；PUX-R4 interaction-stable incremental projection 与 semantic fragment transport 仍属未实施范畴。L0/L1 与 T5.3 outline durable 的落地 **均不**构成对上述传输改造的批准。

## O4-R2 实施门槛

O4-R2 仅实现本合同，至少覆盖：

- G1/G2 create/update/delete/merge 与 G3 可见状态变化的 same-transaction publish；
- rollback、stale writer、true no-op、重复请求及失败 event insert 不留 phantom sequence/event；
- payload validator、redaction、resource limits、unknown schema/operation 与 generation/base fence 的负向测试；
- payload-aware polling classifier：当前 G1/G2/G3 可见变更可靠 reload，非表示变化才可 cursor-only；
- 不修改 snapshot HTTP schema、ETag、压缩、fragment transport、SSE 或 WebSocket。

### 实施状态

- **后端 atomic slices A+B+C（O4-R2）**：已完成。G1 `projection_ops` + `user_assets`、G2 `projection_ops` + `ask_supplements`、G3 `record_state_changed` + `record_metadata` 在同一 PostgreSQL 事务内发布；payload v1 validator、redaction、resource limits、no-op detection、unknown schema/operation 与 generation/base fence 负向测试已建立。详见 `services/api/app/services/reader_orchestration/representation_event_payload.py` 及对应 writer。
- **Web payload-aware classifier（O4-R2-D）**：已完成。`apps/web/src/lib/reader-plate-snapshot/representation-event-classifier.ts` 提供唯一纯函数 `classifyReaderEvent`，由 `polling.ts`/`progressive-transition.ts` 调用，替换静态 `RELOAD_TRIGGER_EVENT_TYPES` 判定。G1/G2/G3 可见变更、未知 schema/section/operation、target_keys 缺失/非法、generation/base fence 不一致一律 reload 或 reset，绝不当作 cursor-only 静默推进。`layer_published`/`record_product_state_updated`/`projection_reset_required` 既有可靠 reload 保留。PUX-R2 单 cursor / 单调 reload 合同不变：reload 成功才推进 cursor，stale snapshot 拒绝或 reload 失败时 cursor hold。
- **PUX-R4 interaction-stable incremental projection**：仍未实施。Plate 局部增量 applier、skeleton/shimmer、accordion/selection/scroll anchor 保留、semantic fragment transport、SSE 通知通道、ETag、304、压缩、JSON Patch、WebSocket 均未改动。

实现完成后，才进入 PUX-R4 interaction-stable incremental projection 的独立设计与实现。
