# Workflow 架构（历史文档）

> **状态**: `HISTORICAL` | **最后验证**: 2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：Architectural Cutover Complete；旧 v3 workflow、`analysis_results.render_scene_json` 事实源、`learning_workflow.py` 已物理删除。本文档保留作历史证据，不再代表当前生产架构。当前 Reader orchestration 架构见 `docs/initiatives/reader-agentic-orchestration/target-architecture.md`。）

本文档记录 cutover 前 Claread 旧分析 workflow（v3 思路）的稳定事实。旧仓库中的 v0/v1/v2 是更早的历史方案。cutover 后旧 v3 workflow 已物理删除，新链以 Reader orchestration 为当前生产架构。

## 当前生产架构（cutover 后）

Reader orchestration 的当前生产架构以 `docs/initiatives/reader-agentic-orchestration/target-architecture.md` 为权威，核心事实源：

- Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events`
- Web 通过 `/app/read` 与 `/app/reader/[recordId]` + BFF `/api/web/reader/records/*` 接入
- FastAPI `/reader/records/*` 与 record-nested Ask v2

旧 `analysis_results.render_scene_json` 事实源、`learning_workflow.py` 固定全量 graph、旧 `/analyze` workflow 已物理删除。

## 以下为旧 v3 workflow 历史内容（仅供回看）

### 旧当前原则

- 输入预处理、语义分析、输出组织、渲染投影分层。
- 后端生成 canonical result，并把 `analysis_results.render_scene_json` 作为全量结果快照真相源持久化（已删除）。
- 客户端阅读页消费专用 `reader scene view`，不再长期绑定 `/records` 的全量 `render_scene_json` 契约（已删除）。
- workflow 运行完成后会额外写入 `analysis_debug_snapshots`，保存运行时调试摘要（已删除）。
- 小程序当前使用降级后的 render scene；Web 后续可以生成更丰富的 render profile（已删除）。
- `schema_version` 和 `workflow_version` 必须保留，便于回看、回归和 eval（已删除）。

### 旧主要链路

1. 接收文本、阅读目标和用户配置。
2. 预处理输入，包括语言检测、句切分和快速退出。
3. 根据 reading goal 选择差异化策略。
4. 执行分析 workflow。
5. 生成可保存的分析记录与结果快照。
6. 写入运行时调试摘要与使用量审计。
7. 为目标客户端组装 `reader scene view`。
8. 用户资产与记录、词典、反馈、批注建立关联。

### 旧结果分层

旧稳定事实可理解为四层：

```text
canonical analysis result
  -> persisted render scene snapshot
  -> reader scene view
  -> client local UI state
```

- `persisted render scene snapshot`
  - 指 `analysis_results.render_scene_json`（已删除）
  - 保留全量结果快照，服务 Directus observability、Inspector 和后续 compare/debug
- `reader scene view`
  - 指专门给 Web / 小程序阅读页消费的精简视图层（已删除）
  - 当前后端已有独立 reader API，不再要求阅读页直接吃 `/records` 的全量快照
- `client local UI state`
  - 指阅读器展开态、滚动位置、临时交互状态等
  - 不进入后端 canonical truth

### 旧 Reading Goal

旧主线包含：

| Goal | 用途 |
|------|------|
| `daily_reading` | 日常阅读理解 |
| `exam` | 考试阅读场景 |
| `academic` | 学术阅读场景 |

后续新增 goal 或 variant 时，必须同步 API schema、数据库记录和前端配置。

### 旧 Learning Workflow 主链路

```text
Draft schema → NormalizedAnnotation → CanonicalSpan → RenderScene range/multi_range
```

具体数据流：

1. LLM 输出 DraftAnnotation（含 anchor_quotes）
2. normalize_and_ground 阶段将 anchor_quotes resolve 为 CanonicalSpan
3. postprocess（dedup、conflict resolution、density control）基于 canonical span
4. project_normalized_to_render_scene 将 CanonicalSpan 转为 UTF-16 sentence-local range
5. RenderScene 输出 RangeAnchor / MultiRangeAnchor 给前端消费

### 旧 RenderScene Range Anchor Contract

- offset_unit = "utf16"
- start / end 是前端 JavaScript 可直接 slice 的 UTF-16 code unit offset
- 半开区间 [start, end)
- range 坐标相对于 RenderScene 中对应 sentence_id 的 sentence render text
- 每个 range 必须带 text，用于校验 slice(start, end) === text
- fail-closed：range 校验失败时丢弃 mark/range 并记录 warning，不 fallback 到 text search
- multi_range 任一 part 校验失败，整条 mark 不渲染
- 旧 TextAnchor / MultiTextAnchor 仍在 InlineMarkAnchor 联合类型中保留

### 旧 Repair 策略

- 当前唯一 repair 路径：item-level patch repair
- 开关：repair_enabled（config/env/state 三级优先级，默认 True）
- 触发策略：should_trigger_patch_repair()，基于 combined repair-worthy drops（drop_log + canonical_drop_log）
- 触发阈值：ANCHOR_FAILURE_THRESHOLD = 0.35
- repair_stats 口径：pre_repair_annotation_count 使用 normalized_annotations 长度，patch_failure_ratio 基于 combined drops 计算

### 旧 LLM Config Observability

- llm_config_snapshot：记录 profile、provider、adapter、model、structured_output 配置、thinking 开关、parallel_tool_calls
- per-agent structured_output_runtime：区分 resolved config（静态解析）和 observed behavior（运行时填充）
- tool_choice=required 实验 profile 是可选配置，不是默认生产策略

### 旧三端 Range/Multi_range 支持状态

- Web Reader：完整支持 range/multi_range 渲染，fail-closed
- Eval Center：完整支持 range/multi_range 展示与诊断（已删除）
- 小程序：完整支持 range/multi_range 数据解析和渲染适配，fail-closed
