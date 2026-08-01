# Markdown 适配当前状态

> 状态：`R-G0G5-R2.1 已完成；source_callout full support；G0–G5 complete`
> 最后更新：2026-08-01
> 范围：Claread 解析链路 Markdown 适配的架构、实现状态、合同、已知问题与延后项。
> 取代关系：本文件是 Markdown 适配的当前事实源；过程文档（`docs/tmp/**`、`.trae/documents/**` 计划文档）仅作历史参考。

---

## 1. 目的

本文件整合 Markdown 适配链路的当前实现状态、合同与已知问题，作为新会话和并行 agent 的单一事实源。上一轮 G0–G5 收口声明曾因真实 Notion 双 MIME 局部融合、单 region 限制和 callout icon 语义边界缺陷失效；R2.1 又补齐了 HTML list/list_item/link fingerprint 投影与无 stub 的真实 G5，现允许写 `source_callout full support` 并将 G0–G5 标记为 complete。Ask Claread/Web Search/RAG 并行 diff 仍按 owner 隔离，不属于本轮修改。

## 2. 架构原则

| 原则 | 说明 |
|------|------|
| 后端是事实源 | Stable Reading Document + Stable Document Blocks + Canonical Text Layer 是跨端事实；Plate Value 是 Web 投影，不持久化为 truth |
| 服务端单次解析 | 同一份输入服务端只解析一次（preparsed 透传），gate / normalizer / candidate / materialization 共用同一 `MarkdownParseResult` |
| 三类 route | `main_reading`（默认进入 canonical text + 正文渲染）/ `rag_ask_only`（仅 RAG 证据，不进正文）/ `metadata_only`（仅元数据） |
| 块型双轨制 | `unit_type` 仅允许 6 个 legacy 值（DB CHECK 约束）；新 block_type 写入 `stable_block_type` 列；只有 `heading` 覆盖 `unit_type` |
| 不重解析历史 | legacy 冻结文档保留原 `normalizer_version`，不批量重解析（CONTRACT Clause 6） |
| 安全与可见降级 | raw HTML / 不安全链接确定性清洗并保留 adaptation notice；边界/语义不确定（如 unclosed fence、task list、footnote）路由 candidate；不静默丢弃 |

## 3. 后端实现状态

### 3.1 解析器与输入链路

| 模块 | 文件 | 状态 |
|------|------|------|
| 权威 Markdown parser | `services/api/app/services/reader_orchestration/markdown_source_parser.py` | ✅ 已落地（markdown-it-py + `mdit-py-plugins`，CommonMark + GFM table/strikethrough） |
| 输入归一化 | `input_document_normalizer.py` | ✅ `NORMALIZER_VERSION = "d6_i3b_structured_source_v1"`；plain-text 路径复用 parser inline flatten |
| 输入门控 | `input_suitability_gate.py` | ✅ 支持 `preparsed: MarkdownParseResult \| None` 透传 |
| Candidate 创建 | `candidate_document_creation_service.py` | ✅ 复用同一 parse result，不再独立正则解析 |
| 物料持久化 | `extracted_artifact_materialization_service.py` | ✅ 上传 `.md` 路径复用 normalizer |
| Parser 诊断 | `NormalizedInputDocument.warnings` | ✅ 透传 parser warnings/unsupported，删除硬编码 `[]` |

### 3.2 冻结与块策略

| 模块 | 文件 | 状态 |
|------|------|------|
| 文档冻结计划 | `document_freeze_plan.py` | ✅ `code_block` / `table` / `table_row` / `table_cell` 默认 `main_reading`（CONTRACT 2026-07-25 re-frozen） |
| Article RAG 索引计划 | `article_rag_index_plan.py` | ✅ `main_reading` 默认可检索；`table` / `table_row` wrapper `rag_eligible=false`，RAG 只针对 `table_cell` 叶子 |
| 块策略 schema | `app/schemas/reader_documents.py` | ✅ `StableDocumentBlock` / `StableDocumentBlockType` / `_DEFAULT_POLICY_BY_BLOCK_TYPE` |

### 3.3 Unit 分类与 Snapshot

| 模块 | 文件 | 状态 |
|------|------|------|
| Unit 分类 | `base_builder.py` | ✅ StableBlockAnnotation 区间匹配命中时 `unit_type` 取 block_type 映射；只有 `heading` 覆盖 `unit_type`，其他保留 heuristic |
| Snapshot 投影 | `snapshot.py` `_build_source_block` | ✅ payload 携带 `stableBlockType` / `headingLevel` / `inlineMarks` / `tableRole` / `parentStableBlockId` |
| ReaderUnitType | `app/schemas/reader_orchestration.py` | ✅ 仅 6 个 legacy 值：`body` / `heading` / `list` / `quote` / `unknown` / `fallback` |

### 3.4 语义大纲跳过

| 模块 | 文件 | 状态 |
|------|------|------|
| 内容充分性短路 | `job_bootstrap.py` `settings_aware_semantic_outline_request_eligibility` | ✅ stable 文档 heading block 数 ≥ 2 时跳过语义大纲 job，诊断 `skipped_markdown_headings_sufficient`；既有 `generation_enabled AND profile_configured` 激活谓词不动 |

### 3.5 行内标记与链接安全

| 模块 | 文件 | 状态 |
|------|------|------|
| inline_marks | `markdown_source_parser.py` | ✅ 在 block `payload_json.inline_marks` 携带 `[{type, start, end, href?}]`，offsets 为 block text_content 内 UTF-16 偏移 |
| 链接白名单 | `markdown_source_parser.py` `SAFE_LINK_PROTOCOLS` | ✅ `http` / `https` / `mailto`；非安全协议剥离 + warning |
| Raw HTML | `markdown_source_parser.py` | ✅ 确定性安全归一化 + `adaptation_notice`；paired rich `<aside>` 进入 `source_callout` 结构树 |
| Callout icon boundary | `markdown_source_parser.py` / `document_freeze_persistence.py` | ✅ 仅 recognized callout wrapper 的独立 emoji-only 首段提升为 `payload_json.display_icon`；wrapper/children parent chain 重写后再冻结，icon 不进入 canonical range |
| Notion dual MIME local fusion | `apps/web/src/lib/clipboard/clipboard-source-negotiation.ts` + `clipboard-source-fusion.ts` | ✅ HTML 富结构保留；N 个高置信、成对、块级 escaped aside 按文档顺序建立 `CalloutFusionFingerprint`，全部匹配后一次性局部替换；任一 mismatch 全量降级为 sanitized HTML，不切换整篇 plain |

## 4. 前端实现状态

### 4.1 阅读页渲染

| 模块 | 文件 | 状态 |
|------|------|------|
| 块级结构投影 | `apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts` + `reader-record-plate-to-plate-value.ts` | ✅ 优先消费 Snapshot 的递归 Stable Block tree；list/table/callout wrapper 与 children 保持层级，不重新解析 raw Markdown |
| Plate 插件与组件 | `apps/web/src/components/editor/plugins/reader-blocks-kit.tsx` + `source-callout-kit.tsx` | ✅ 注册 heading/list/code/blockquote/table/hr/source-callout 与行内 `a` / `strikethrough` 插件；callout 使用可见 note 语义 |
| 行内 marks 渲染 | 投影层 + Plate 叶子 | ✅ `splitLeafByInlineMarks` + `inlineMarksToPlateProps`；`<s>` 语义标签；`a` plugin `options.mode="inline"` |
| 底部重复文本 | `ReaderRecordPlateSurface.tsx` | ✅ 已删除 `ReaderStableSourcePreview` 及其 slot（D1）；文件已不存在 |

### 4.2 Outline

| 模块 | 文件 | 状态 |
|------|------|------|
| Markdown outline | `apps/web/src/lib/reader-plate/projection/reader-outline-view.ts` `projectMarkdownOutlineView` | ✅ 已实现（非 stub）；从 navigation units 提取 heading 序列；depth = `min(headingLevel, 3)` |
| Heading 判定对齐 | 同上 | ✅ 同时检查 `unit_type === "heading"`（后端 canonical）与 `stable_block_type === "heading"`（防御性 A5 payload）；`heading_level` 缺失默认 1（legacy heuristic 兼容） |
| Outline 优先级合并器 | `pickReaderOutlineSource` | ✅ markdown 优先 → semantic 退位 → hide |

### 4.3 输入区

| 模块 | 文件 | 状态 |
|------|------|------|
| Plate 输入框 | `MarkdownTextInput.tsx` | ✅ Plate + MarkdownKit + remarkGfm；修复 placeholder 重叠；catch 静默降级改为可见提示 |
| 反序列化 | `apps/web/src/lib/reader-plate/markdown/deserialize.ts` | ✅ 服务增强层（grammar_note / sentence_analysis / ask_supplement），不解析文章来源 |
| 粘贴保真 | `AnalyzeSubmitForm.tsx` / `submit-mode.ts` | ✅ 未编辑时提交用户原文，编辑后提交 serialize 结果；上传 `.md` 直接提交文件内容 |
| Notion 双 MIME 粘贴 | `MarkdownTextInput.tsx` + clipboard fusion seam | ✅ 真实 `ClipboardItem(text/html + text/plain)` `/app/read` 已验证 h2/marks/list/link/reference/table/两个 callout/trailing 均保留；canonical source 恰好两个 paired aside |
| Reader callout icon | `reader-record-plate-document.ts` + `reader-blocks-kit.tsx` | ✅ 从 Stable wrapper payload 投影 `display_icon`；无 payload/非法 emoji 使用默认 💡；不再从首个正文 child 推断或隐藏 |
| Lint 降级 | `markdown-lint.ts` | ✅ 非阻塞提示（"含可能进入审核的内容"）；注释固化"与后端判定无强制一致承诺" |

## 5. 合同与 Fixture

### 5.1 正式合同

- `services/api/tests/fixtures/markdown_structured_source/CONTRACT.md` — **G0–G5/R2 final-gate evidence**，2026-08-01 re-frozen
  - Clause 1：身份（`parser_name + parser_version + profile`）
  - Clause 2：source range（UTF-16 offset）
  - Clause 3：block 表达（block_type 枚举 + parent-child + inline marks + link 安全）
  - Clause 4：policy（`main_reading` / `rag_ask_only` / `metadata_only` 默认与覆盖）
  - Clause 5：诊断承载（warning / unsupported / candidate / reject 结构化字段）
  - Clause 6：fallback（legacy 不重解析）

### 5.2 Fixture 集

`services/api/tests/fixtures/markdown_structured_source/` 下 20 个 fixture：覆盖 paragraph / heading h1–h6 / marks / links / blockquote / citation/reference / GFM alert / source callout / rich HTML aside / nested list / GFM table / code / footnote / raw HTML / task list / definition list / unsafe link / unclosed fence / reject-empty。每个含 `input.md` + `expected_blocks.json`，并配套 policy/diagnostics 断言。

### 5.3 工程约定

- `unit_type` 列仅允许 6 个 legacy 值（migration 0001 CHECK 约束）
- 新 block_type（paragraph / list_item / blockquote / table / table_row / table_cell / code_block）写入 `stable_block_type` 列
- 只有 `heading` 覆盖 `unit_type`（在 legacy 允许集内 + 下游 A6 / B4 消费 `unit_type == "heading"`）

### 5.4 R-G0G5-R2.1 复核门（2026-08-01；已完成）

- 双 MIME：真实 Chromium `ClipboardItem` 写入 `text/html` + `text/plain`，完整 Notion 风格文章通过 `/app/read`；HTML 富结构保留，N 个 paired escaped aside 按顺序建立结构化 fingerprint，全部成功后在 DOM/Plate fragment seam 一次性局部替换，无法可靠对应时不静默切整篇 plain、不做部分融合。
- Fingerprint：包含 block boundary/document order、可见文本、block/list/list_item/children/marks、ordered `(visibleText, sanitizedHref)` links、link count 与 unsafe-link count；URL 先走共享安全协议归一化，实际文本/URL/结构差异均拒绝融合，稳定 reason 为 `html_aside_fusion_declined`。
- List fingerprint：HTML `ul/ol` 分别归一为 unordered/ordered list；`li` 连续 text/inline children 归一为单一 `list_item_content/lic`，nested list 保留为 list_item 的结构 child；Markdown Plate list tree 与 HTML adapter 映射到同一 fingerprint。Stable 的 list wrapper 仍是 structural-null，role-bearing list_item/paragraph descendants 继承 `source_callout`。
- Source：Confirmed Source `revision=1/status=frozen`，canonical source/hash 正确，真实两-callout链路恰好两个 `<aside>...</aside>`，无 `class/style/on*`，无 escaped aside 或 `[!NOTE]`；降级时保留 sanitized HTML 富结构并让 escaped marker 可见或进入既有 Content Check。
- Icon：Stable wrapper payload 保存 `display_icon`，wrapper 无 canonical range；正文 children 保留 parent chain、inline marks、safe link、canonical ranges 与 `source_callout` T-only policy；emoji-only leaf/Unit/Anchor/automatic target 为 0。translation job/profile manifest 不包含 icon，正文可用 `USER_EXPLICIT` translation。
- Reload：fake executor 只用于确定性 enhancement；fresh snapshot tree == reload snapshot tree，Reader reload 后 icon、正文、trailing paragraph 不重复不丢失。
- Reader：R2.1 real-product 两-callout、source-callout、selection focused Chromium suites 覆盖 `1440×900`、`1280×720`、`390×844` 与 light/dark；共享 `ReaderRecordPlateSurface.tsx` 的 Ask 混合 diff 未修改、未 stage、未纳入本轮 ownership。
- G5 startup：真实 `from app.main import app` / 正常 Uvicorn startup 通过；未注入 `emergency_compact` 或其他 Ask symbol stub，fake namespace 只由既有 deterministic enhancement runner 使用。
- Gates：typecheck、R2 allowlist ESLint/Ruff、focused unit/API suites、真实 Chromium suites、`git diff --check` 与 empty index 均通过；未运行真实 LLM。旧 `test_reader_snapshot_stable_block_reload.py` 的 4 条 E501 长文档行属于既有非 R2 格式问题，未为本任务格式化。

## 6. 已知问题（不阻塞 G0–G5；后续 stage）

来源：`.trae/documents/markdown-adaptation-issues-resolution-plan.md`

| ID | 问题 | 当前状态 |
|----|------|----------|
| P0 | Candidate Review 输入端预警 lint（含链接白名单预处理） | ✅ 已关闭“提交阻断”：前端仅展示 warning badge，后端 parser/适配记录负责最终清洗与 candidate routing；仍不承诺前后端共享 parser |
| P1 | 双 Parser Round-trip 一致性测试 | ⏳ 后续增强。输入端 Plate/MarkdownKit 与后端 markdown-it-py 仍是不同 parser；当前以 raw-paste、fixture、real product DB/E2E contract 约束，不在 Web 重新解析 raw Markdown |
| P2 | 代码块语言标识可见提示 | ⏳ 后续增强。结构与 language metadata 已保留并通过 Snapshot；可见 badge/highlighting 不属于本次收口 |
| P3 | `renderInlineMarks` 潜在 bug | ⏳ 后续增强。当前结构化 source marks 已由 Stable Block payload/Reader projection 覆盖并有 fixture/browser 断言；旧 renderer 的非主路径仍需独立清理 |

## 7. 延后项（明确移出当前范围）

| 项 | 原因 |
|----|------|
| SourceBlob / 物理去重 / OSS ETag | 独立基线推进，不在 Markdown 适配关键路径 |
| OCR 矩阵 / 图片格式扩展 | 独立基线推进 |
| Markdown 编辑器 / WYSIWYG round-trip | 当前只读渲染 + 输入框 Plate WYSIWYG 已满足需求 |
| 表格中文翻译 / cell-level annotation | 表格原文渲染已落地，翻译层扩展延后 |
| math / footnote / 任意 raw HTML 完整语义 | 仍按合同降级或 candidate；paired rich `<aside>` 的 R2 多 region 双 MIME 与 icon 端到端收口不扩展任意 HTML/math/footnote 的完整语义 |
| 历史数据批量重解析 | CONTRACT Clause 6 明确禁止；用户手动重新提交 |
| `include_rag_ask_only=True` 路径接通 | M3 stage C 决策，`turn_coordinator.py` 硬编码 False，推迟到后续 stage |
| Mermaid 静态落差 | 已闭环（`mermaid_static_only` 诊断 + `data-mermaid="true"` 不执行） |
| A3 链接安全单点化 | `_extract_and_strip_links` 嵌套括号 / 带 title / html 截断 scheme 场景的穷举 fixture 待补 |
| A7 candidate 路由分布统计 | 用 fixture + 真实风格样本跑门控的报告脚本待补 |

## 8. 文档索引

### 8.1 正式文档（当前事实源）

- 本文件：`docs/initiatives/reader-agentic-orchestration/modules/markdown-adaptation-state.md`
- 合同：`services/api/tests/fixtures/markdown_structured_source/CONTRACT.md`（G0–G5 closure evidence）
- 输入适配模块：`docs/initiatives/reader-agentic-orchestration/modules/input-adapter.md`
- 文档与 Unit 模块：`docs/initiatives/reader-agentic-orchestration/modules/reading-base-and-units.md`
- Reader Plate Surface UI：`docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md`
- Plate Reader Projection：`docs/initiatives/reader-agentic-orchestration/modules/plate-reader-projection.md`
- Frontend Integration Contract：`docs/initiatives/reader-agentic-orchestration/modules/frontend-integration-contract.md`

### 8.2 计划文档（已加状态修正，保留作历史参考）

- 主计划：`.trae/documents/markdown-ecosystem-refactor-plan-2026-07-24.md`（A1–A7 / B1–B4 / C1–C2 已基本落地）
- 遗留问题计划：`.trae/documents/markdown-adaptation-issues-resolution-plan.md`（P0–P3 待实施）
- 早期 Plate Markdown 化：`.trae/documents/reader-plate-markdown-and-output-strategy.md`（阶段一已完成）
- 早期续接：`.trae/documents/reader-plate-markdown-and-output-strategy-resume.md`（P0-Step-4/5/6 已完成）

### 8.3 归档 TMP 文档（已完成使命，仅作历史参考）

- 整体评审：`docs/tmp/TMP-reader-markdown-logic-review-2026-07-24.md`
- 07-22 重校准计划：`docs/tmp/TMP-reader-markdown-rich-input-refactor-plan-2026-07-22.md`
- 07-16 早期计划：`docs/tmp/TMP-reader-markdown-rich-input-refactor-plan-2026-07-16.md`
- 07-16 深度研究：`docs/tmp/reader-orchestration/TMP-reader-markdown-rich-input-deep-research-2026-07-16.md`
- Plate v53 能力核验：`docs/tmp/reader-orchestration/research/TMP-plate-v53-markdown-readonly-capability-research-2026-07-16.md`

## 9. 验证入口

- R2.1 real product：`pnpm --filter=@claread/web typecheck` 通过；真实 Chromium `reader-markdown-g5-real-product.spec.ts` 为 1 passed，真实 `ClipboardItem` 双 MIME、正常 FastAPI/PostgreSQL、deterministic fake executor、fresh/reload 与 `USER_EXPLICIT` body translation 均在同一链路中验证；真实 record 为 `6227c991-2792-4528-b35f-1f492e3a2a2d`。
- R2.1 Chromium：`source-callout-aside.spec.ts` 为 13 passed（含两个带 list/nested-list 的 callout、URL mismatch 可见降级、escaped/unclosed/fenced/inline guards）；`reader-selection-floating-toolbar.spec.ts --grep "native text selection|copy button|source_callout|390px|1280px|dark mode"` 为 6 passed。
- R2.1 Web unit：5-file focused command 为 135 passed，source-callout icon/adapter command 为 69 passed；clipboard negotiation 单文件为 29 passed。
- R2.1 API：parser/freeze/reload/display-icon/prompt-profile/safe-normalization/job policy focused command 为 328 passed，1 个 `.pytest_cache` ACL warning。真实记录证据包含 Confirmed Source revision 1/frozen、source hash `c25f8640f5685668c85a15d45fab2d3ea6a248390a932a458b1fb031219b8b3e`、Stable 38 blocks、两个 wrapper payload、list/list_item parent chains、`u17–u24` source_callout T-only units、zero icon Unit/Anchor/job target、fresh snapshot == reload snapshot。
- Gates：任务 allowlist ESLint 通过；Ruff 任务文件结构检查通过。`test_reader_snapshot_stable_block_reload.py` 保留 4 条既有 E501 长文档行，严格未忽略 E501 的全文件 Ruff 会只报告这 4 条，不对其做无关格式化；`git diff --check` 通过，`git diff --cached --name-only` 为空。
- 混合工作区隔离：Ask Claread/Web Search/RAG diff 原样保留；共享 `ReaderRecordPlateSurface.tsx` 未修改、未格式化、未 stage、未回滚；未运行真实 LLM、未做历史记录批量迁移、未新增 LLM 分类节点。
