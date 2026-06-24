# Reader Agentic Orchestration 重构专项

> 状态：`进行中专项（D6 产品硬化阶段）`
> 最后更新：2026-06-24
> 权威性：本目录是 Reader AI Workflow -> agentic orchestration 重构期间的专项事实源。

本目录用于管理 Reader agentic orchestration 重构的目标架构、阶段计划和 coding agent 上下文。它与当前稳定产品/架构文档分开，因为当前系统仍是旧 AI Workflow 形态，而本目录描述本轮重构的目标状态。

## 范围

本轮包含：

- 用户提交内容的 `learning` 解析。
- 先用 Web Reader 做验证。
- 新 Reader 所需的后端 schema、API、orchestration runtime、worker、event log、usage audit 和 eval hooks。
- Web Reader Article Body 的 Plate.js projection、owner 权限和渐进式渲染合同。
- 当前记录内的 RAG substrate 接入边界。
- 文本、URL、PDF、OCR、文件等输入模式的统一适配边界。

本轮不包含：

- `daily_reader_workflow` 的 runtime 重构。
- `academic workflow` 的 agentic orchestration 重构；待 learning workflow 验证稳定后再单独设计。
- 第一验证阶段的小程序实现。
- 旧开发记录的数据迁移。
- 旧 `render_scene_json` 的兼容映射。
- 全局 User Editorial Assets RAG、跨记录知识库化、自动迁移用户编辑资产。

## 当前前提

- Claread 仍处于开发阶段，尚未上线生产用户数据。
- 本地数据库数据可以在重构中清空，但必须保留词典三表：
  - `dict_entries`
  - `dict_lookup_targets`
  - `dict_redirects`
- 数据库 baseline 可以按目标架构重塑，不需要背负旧开发记录迁移复杂度。
- Web 是第一验证客户端；小程序后续基于稳定后的 Web / API contract 做降级适配。
- `daily_reader_workflow` 继续保持固定 workflow。
- 重构期间解析功能可以暂时不可用；不需要维持旧 Reader UI / 旧 `render_scene_json` contract 可用。

## 当前外部服务假设

这些是 D0.5 初步选型，不是最终不可变承诺：

- RAG 向量库：测试阶段优先使用当前已配置的 Zilliz Cloud；后续上线前评估迁移到阿里云 RAG / 向量检索服务。
- RAG 应用层：优先保持 Claread 自有 RAG contract，不直接把百炼知识库当业务事实源；百炼知识库可作为后续托管 RAG 候选。
- OCR / 文档解析：优先评估阿里云百炼的图像理解、Qwen OCR / VL、文档解析能力。
- 文件上传：测试阶段可使用阿里云 OSS；上线目标为阿里云 OSS + CDN。

## 权威文档

本专项只认以下文档：

1. `target-architecture.md`：目标产品形态与架构边界。
2. `concepts.md`：术语、概念定义和统一口径。
3. `modules/`：D1 模块合同。
4. `implementation-plan.md`：阶段计划、门禁、任务包和验收标准。
5. `spikes/README.md`：D2 spikes 启动清单和执行顺序。
6. `agent-brief.md`：发给 coding agent 的最小上下文。

## 模块文档

| 文档 | 内容 |
|---|---|
| `modules/input-adapter.md` | 输入适配、Source Artifact、Extraction Result、Candidate Base |
| `modules/schema-and-domain-contract.md` | D3 schema 边界、domain contract、运行时表、Plate snapshot DTO、reset/cutover 约束 |
| `modules/reading-base-and-units.md` | Stable Base、Reading Units、Anchor Segments、UTF-16/hash、`article_ready` gate |
| `modules/orchestration-runtime.md` | run/job、worker lease、并发、Authorization Envelope |
| `modules/policy-and-cost-control.md` | Policy Planner、Skip Gate、Model Profile、Prompt Cache、Usage Bucket |
| `modules/enhancement-layers-and-parsed.md` | Enhancement Layer schema、anchor、Parsed Decision |
| `modules/streaming-and-projection.md` | Reader Events、snapshot、SSE、polling fallback |
| `modules/plate-reader-projection.md` | Plate.js Article Body、projection operations、document tools、owner 权限、anchor bridge |
| `modules/rag-substrate.md` | record-scoped RAG、citation DTO、provider adapter |
| `modules/cutover-and-old-workflow.md` | 停服重构、旧 workflow 移除、旧依赖审计 |

`docs/tmp/reader-orchestration/` 下的研究材料只作为证据库。除非任务明确要求回看某份研究报告，否则 coding agent 不应默认读取 TMP 研究文档。

## 文档治理规则

- 不在 `modules/` 之外新增长期设计文档，除非 `implementation-plan.md` 明确要求。
- 新决策写入 `target-architecture.md` 的决策记录。
- 进度和阶段状态写入 `implementation-plan.md`，不要写进研究报告。
- coding agent 默认从 `agent-brief.md` 开始，不从 TMP 文档堆里找上下文。
- 如果实现发现目标架构与代码事实冲突，先暂停并记录冲突，不要自行发明新架构。

## 与稳定文档的关系

`docs/product/current-state.md`、`docs/development/mainline.md` 和既有 `docs/architecture/*` 描述当前已落地基线或稳定子系统。本目录描述重构期间的目标状态。

某个阶段稳定落地后，再把已经成为现实的事实压回稳定文档，并删除或归档过期专项内容。
