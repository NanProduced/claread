# Reader Agentic Orchestration Archive

> 状态：DOC-TRUTH-LIFECYCLE-R2（2026-08-03）历史决策索引。archive/** 全文已删除，结论已进入正式文档；Git 历史承担追溯。
> 用途：保留已闭合设计/评审/研究/spike 的决策索引，便于后续回溯结论落点。本目录不保留任何正文文件。

## 索引

| 主题 | 原归档目录 | 关闭状态 | 权威事实归宿 |
|------|------------|----------|-------------|
| Reader Document Graph 设计→评审→worklog（8 文件） | `archive/document-graph/` | 设计已修订吸收（Graph 降为 Snapshot Value Builder） | [`modules/plate-reader-projection.md`](../modules/plate-reader-projection.md) |
| Translation V2 设计→评审→综合→Phase 0（5 文件） | `archive/translation-v2/` | R1-R5 全部 accepted | [`modules/enhancement-layers-and-parsed.md`](../modules/enhancement-layers-and-parsed.md) |
| Plate.js 可行性研究 | `archive/research/R13-plate-js-feasibility-2026-06-18.md` | 决策已落地（D2-P0 accepted） | [`modules/plate-reader-projection.md`](../modules/plate-reader-projection.md) |
| Notion AI sidebar 浮动 UI 研究 | `archive/research/notion-ai-sidebar-floating-ui.md` | 设计已吸收 | [`modules/ask-claread-reader-workspace.md`](../modules/ask-claread-reader-workspace.md) |
| D2-S1 Reading Unit Builder spike 结果 | `archive/spikes/D2-S1-reading-unit-builder-result.md` | accepted_with_changes | [`modules/reading-base-and-units.md`](../modules/reading-base-and-units.md) |
| 外部主要来源研究（Lost in the Middle / LooGLE / ReadAgent / RAPTOR） | `archive/external-primary-sources-2026-07-09.md` | 结论已吸收进 adaptive-design | [`adaptive-reader-orchestration-design.md`](../adaptive-reader-orchestration-design.md) |

## 规则

- 本目录不再保留任何正文文件；如需查阅原文，使用 git history（在 commit 9708b72a 之前 archive/** 子目录仍可恢复）。
- 新决策不写入本目录，直接写入 `target-architecture.md` 决策记录或对应 `modules/*.md`。
- 本 README 仅作为决策落点索引，不复制决策正文。