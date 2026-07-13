# Reader Agentic Orchestration Archive

> 状态：DOC-R3 归档目录（2026-07-13）
> 用途：存放已闭合的设计/评审/研究文档，保留作历史证据。所有结论已压缩进正式文档。

## 归档规则

- 本目录文件为**历史归档**，不再作为活跃事实源。
- 每份归档文件的权威事实归宿见下方索引表。
- 不得从归档文件复制正文回正式文档。
- 归档文件不删除，保留作可审计历史证据。

## 归档索引

### document-graph/（8 文件）— Reader Document Graph 设计→评审→worklog 链

| 文件 | 原路径 | 主题 | 关闭状态 | 权威事实归宿 |
|------|--------|------|----------|-------------|
| `TMP-reader-document-graph-design-2026-06-27.md` | `docs/tmp/reader-orchestration/` | Reader Document Graph 初版设计 | 设计已修订吸收（Graph 降为 Snapshot Value Builder） | [`modules/plate-reader-projection.md`](../modules/plate-reader-projection.md) |
| `reader-document-graph-design-review-1.md` | `docs/tmp/reader-orchestration/review/` | Review round 1 | 已吸收 | 同上 |
| `reader-document-graph-design-review-2.md` | `docs/tmp/reader-orchestration/review/` | Review round 2 | 已吸收 | 同上 |
| `reader-document-graph-design-review-3.md` | `docs/tmp/reader-orchestration/review/` | Review round 3 | 已吸收 | 同上 |
| `reader-document-graph-design-review-4.md` | `docs/tmp/reader-orchestration/review/` | Review round 4 | 已吸收 | 同上 |
| `reader-document-graph-design-review-5.md` | `docs/tmp/reader-orchestration/review/` | Review round 5 | 已吸收 | 同上 |
| `reader-document-graph-design-review-6.md` | `docs/tmp/reader-orchestration/review/` | Review round 6 | 已吸收 | 同上 |
| `TMP-reader-document-graph-review-worklog-2026-06-27.md` | `docs/tmp/reader-orchestration/review/` | Review worklog | 已吸收 | 同上 |

### translation-v2/（5 文件）— Translation V2 设计→评审→综合→Phase 0 链

| 文件 | 原路径 | 主题 | 关闭状态 | 权威事实归宿 |
|------|--------|------|----------|-------------|
| `translation-v2-design-review-1.md` | `docs/tmp/reader-orchestration/review/` | Translation V2 review 1 | R1-R5 全部 accepted | [`modules/enhancement-layers-and-parsed.md`](../modules/enhancement-layers-and-parsed.md) |
| `translation-v2-design-review-2.md` | `docs/tmp/reader-orchestration/review/` | Translation V2 review 2 | 已吸收 | 同上 |
| `translation-v2-design-review-3.md` | `docs/tmp/reader-orchestration/review/` | Translation V2 review 3 | 已吸收 | 同上 |
| `translation-v2-review-synthesis-2026-06-27.md` | `docs/tmp/reader-orchestration/review/` | Review synthesis | 正式文档吸收后归档 | 同上 |
| `translation-v2-phase0-current-state-and-plan-2026-06-30.md` | `docs/tmp/reader-orchestration/review/` | Phase 0 current state and plan | 已被 backend-contract-design-spike 取代 | 同上 |

### research/（2 文件）— 研究材料

| 文件 | 原路径 | 主题 | 关闭状态 | 权威事实归宿 |
|------|--------|------|----------|-------------|
| `R13-plate-js-feasibility-2026-06-18.md` | `docs/tmp/reader-orchestration/research/` | Plate.js 可行性研究 | 决策已落地（D2-P0 accepted） | [`modules/plate-reader-projection.md`](../modules/plate-reader-projection.md) |
| `notion-ai-sidebar-floating-ui.md` | `docs/initiatives/reader-agentic-orchestration/research/` | Notion AI sidebar 浮动 UI 研究 | 设计已吸收 | [`modules/ask-claread-reader-workspace.md`](../modules/ask-claread-reader-workspace.md) |

### spikes/（1 文件）— Spike 结果

| 文件 | 原路径 | 主题 | 关闭状态 | 权威事实归宿 |
|------|--------|------|----------|-------------|
| `D2-S1-reading-unit-builder-result.md` | `docs/initiatives/reader-agentic-orchestration/spikes/` | Reading Unit Builder spike 结果 | accepted_with_changes | [`modules/reading-base-and-units.md`](../modules/reading-base-and-units.md) |

### 根目录（1 文件）— 外部来源研究

| 文件 | 原路径 | 主题 | 关闭状态 | 权威事实归宿 |
|------|--------|------|----------|-------------|
| `external-primary-sources-2026-07-09.md` | `docs/initiatives/reader-agentic-orchestration/tmp/` | 外部主要来源研究（Lost in the Middle / LooGLE / ReadAgent / RAPTOR） | 结论已吸收进 adaptive-design | [`adaptive-reader-orchestration-design.md`](../adaptive-reader-orchestration-design.md) |
