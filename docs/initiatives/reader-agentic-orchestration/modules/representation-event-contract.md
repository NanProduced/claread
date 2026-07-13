# Snapshot Representation Event Contract

> 状态：`已接受设计；O4-R2 后端 atomic slices A+B+C 已完成；O4-R2-D Web payload-aware classifier 已完成；PUX-R4 局部 applier / fragment / SSE 仍未实施`
> 最后更新：2026-07-13（由 T4.2a-O4-R1 研究结论压缩；已完成 review 修订；O4-R2-D 由 T4.2a-O4-R2-D 追加状态）
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
