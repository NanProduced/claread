# R13 Plate.js 可行性调研

> 状态：调研报告（中文）
> 日期：2026-06-18
> 项目：Claread Reader（`C:\Users\nanpr\claread\claread`）
> 2026-06-27 校准：本文是 Plate.js 选型与早期架构调研，不再记录当前代码接入状态。当前接入状态见 `docs/initiatives/reader-agentic-orchestration/modules/reader-plate-component-integration.md`。
> 配套文档：
> - 决策落地：[`docs/initiatives/reader-agentic-orchestration/target-architecture.md`](../../../initiatives/reader-agentic-orchestration/target-architecture.md) §"Reader Projection 与 Plate Document"、决策 D1-012 ~ D1-017
> - 当前接入矩阵：[`docs/initiatives/reader-agentic-orchestration/modules/reader-plate-component-integration.md`](../../../initiatives/reader-agentic-orchestration/modules/reader-plate-component-integration.md)
> - 计划增量：[`docs/initiatives/reader-agentic-orchestration/implementation-plan.md`](../../../initiatives/reader-agentic-orchestration/implementation-plan.md)
> - Agent 简报：[`docs/initiatives/reader-agentic-orchestration/agent-brief.md`](../../../initiatives/reader-agentic-orchestration/agent-brief.md) §"渲染层与 Plate 不可违反规则"

---

## 1. Executive Summary

Claread 当前正在把 learning 解析从"固定 AI Workflow"重构为 bounded agentic Reader orchestration。**前端如何承载和呈现"长生命周期 + 增强层渐进到达 + 用户资产可编辑 + Ask 可改文档"**这份长期对象，D1 RFC 没有给出明确答案。当前用自创的 `ReaderPlateDocument`（Claread 领域命名，**与 Plate.js 同名巧合**）从后端 `render_scene_json` 投影得到一份**不可编辑的虚拟文档**，再用 `Plate readOnly`（Plate.js 真实库）渲染——后端 produce UI JSON 的路径是已知痛点。

**本报告结论**：

1. **明确推荐引入 Plate.js 作为 Reader 解析页底层文档模型、渲染层与交互引擎**（替换向，非渐进）。
2. **不重做渲染层**：Claread 已经在用 Plate.js 跑只读渲染（`PlateReaderSurface` / `ImmersiveReaderSurface` 都 `import { Plate, usePlateEditor } from "platejs/react"`）。"引入"的真实工作是：把只读升级为 partial editable、把后端 `render_scene` 替换为可被 patch 的 event 流、把 Ask 改为 document tools。
3. **后端 truth 保持不变**：Plate document 不是 truth，是 `reading_bases` / `reading_units` / `enhancement_layers` / `user_annotations` / `reader_notes` / `ask_supplements` 这六类 domain fact 的 projection。`enhancement_layers` 等表结构不改为 patch sequence——否则 RAG citation、eval、重放、非 Web 客户端都会被 Plate 绑死。
4. **`reader_events` 新增 `plate_patches` 子类型**，与原 `layer_published` / `layer_failed` 等 domain event 并存。Layer Publisher 在 publish 末尾同事务 emit 两条事件；非 Web 客户端继续 polling 全量 snapshot。
5. **D4 不接 plate_patches**：article_ready 路径继续走 `renderSceneToPlateDocument` 强制 setValue；D5+ 增强层（vocab / grammar / translation 渐进到达）才接 plate_patches 增量路径。
6. **Ask Sidecar 改 document tools 模式**，主路径工具集：`read_range` / `create_highlight` / `write_note` / `write_ai_supplement` / `revise_ai_annotation`。`response_cards` 字段保留作 D5 兼容。

**风险与 spike 计划**（详见 §11 / §12）：
- D2-S0 Plate-first Projection Spike（4-5 天）：1000 paragraph 性能 / owner 权限 1000 次随机 / sentenceId ↔ path round-trip / plate_patches 100 个 replay / Ask tool 端到端
- D2-S1 Streaming Insert 反模式验证（1 天）
- D2-S2 render_scene 与 plate_patches 共存（1 天）

---

## 2. Key Recommendation

**一句话**：把 Plate.js 升级为 Reader Article Body 的"projection 顶层 + AI 写入受控通道"——不是替换真理源，而是替换"后端直出 UI JSON"这条死板路径。

**5 条决策**（D1-012 ~ D1-017）：

| 编号 | 决策 |
|---|---|
| D1-012 | Reader 渲染层走 Plate.js（`platejs/react`），作为 long-lived Article Body 文档模型、渲染层和交互引擎。`apps/web/src/lib/reader-plate/` 的"plate"是 Claread 自创领域命名，与 Plate.js 同名巧合。 |
| D1-013 | Plate document 不是 truth，是 domain fact 的 projection。`enhancement_layers` / `user_annotations` / `reader_notes` 等表结构**不改为 patch sequence**；plate_patches 写在 `reader_events.projection_meta` JSONB 字段。刷新恢复从 domain truth 重建。 |
| D1-014 | `reader_events.event_type` 新增 `plate_patches` 子类型（与 domain event 并存）。Layer Publisher 同事务 emit `layer_published` + `plate_patches`。非 Web 客户端继续 polling snapshot。 |
| D1-015 | Ask Sidecar 改 document tools 模式：`read_range` / `create_highlight` / `write_note` / `write_ai_supplement` / `revise_ai_annotation`。`response_cards` 保留作兼容。 |
| D1-016 | `renderSceneToPlateDocument` 与 `reader_scene.py` 保留作 D4 路径，D5+ 不再扩展。 |
| D1-017 | owner 权限层覆盖 stable / ai / user / ask citation 四类，校验双层：后端权威拒绝 + 前端 `renderLeaf` 镜像。 |

---

## 3. Plate.js Capability Review

### 3.1 数据模型

[official: /udecode/plate/docs/research/decisions/arthrod.md]

Plate.js 基于 Slate v2。`definePlateDocumentModel` 提供强 schema 声明：

```typescript
const model = definePlateDocumentModel({
  nodes: {
    paragraph: blockNode({ children: 'inline*', fields: { align: ... } }),
    commentAnchor: inlineMarker({ fields: { threadId: stringValue() } }),
  },
  features: {
    comments: strictFeature(),
    insertions: strictFeature(),
    deletions: strictFeature(),
  },
});
```

Claread 需要的能力（block + inline + features）全部能表达。`blockNode` / `inlineMarker` 是原子化的节点声明，比 Claread 当前自创的 `ReaderParagraphNode` / `ReaderSentenceNode` 类型更结构化。

### 3.2 编辑操作 kernel

[official: /udecode/plate]

所有编辑操作走 `editor.tf.*`（transform kernel）：
- `editor.tf.insertNodes` / `editor.tf.removeNodes` / `editor.tf.setNodes`
- `editor.tf.insertText` / `editor.tf.deleteText`
- `editor.tf.addMark` / `editor.tf.removeMark`
- `editor.tf.apply(ops)` 直接消费 Slate.js standard operation
- `editor.tf.setValue(value)` 全量替换（用于 snapshot reload）
- `editor.tf.withoutSaving(() => ...)` / `editor.tf.withScrolling(() => ...)` 上下文包装
- `editor.tf.withoutNormalizing(() => ...)` 批量操作（**Yjs CRDT 协作扩展点**）

`editor.tf.apply(ops)` 是 plate_patches 事件的核心消费接口——后端 emit Slate ops，前端 `apply` 一次到位，不需要逐 patch 解析。

### 3.3 官方 AI 集成

[official: /udecode/plate/content/docs/(plugins)/(ai)/ai.mdx, ai.cn.mdx]

| API | 作用 |
|---|---|
| `AIChatPlugin` / `AIPlugin` | AI 集成入口 |
| `tf.ai.beginPreview({originalBlocks})` | 标记 rollback 切片，AI 预览开始；返回 boolean 表示是否新建了 rollback point |
| `streamInsertChunk(editor, chunk, {textProps})` | 流式写入 AI 生成文本；与 `tf.ai.beginPreview` 配对使用 |
| `applyAISuggestions(editor, content)` | diff AI 输出（markdown string），写入 transient suggestion node（需要 `@platejs/suggestion`） |
| `acceptAISuggestions(editor)` / `rejectAISuggestions(editor)` | 接受 / 拒绝 AI 建议 |
| `withAIBatch(editor, fn, {split})` | 批操作原子化；AI undo 时整批回滚；`split: true` 开始新 history batch |
| `tf.aiChat.accept()` | insert 模式：移除 AI marks，光标放到流式内容末尾；chat 模式：应用 pending suggestions |

**关键设计**：Plate 官方明确**不**让 LLM 输出整段 Slate JSON——LLM 输出 markdown fragment 或 operation list，由 `editor.tf.*` kernel 执行。`@platejs/ai` 提供的 `streamInsertChunk` / `applyAISuggestions` 是 community 已经验证的反 dancing-cursor 反 flicker-of-death 模式。

### 3.4 Markdown 互转

[official: /udecode/plate/content/docs/(plugins)/(serializing)/markdown.mdx]

`@platejs/markdown` 提供：
- `api.markdown.serialize({value})`: Plate Value → Markdown string
- `api.markdown.deserialize(markdown)`: Markdown string → Plate Value
- `withBlockId: true` 保留 block id（`# <block id="...">content</block>` 语法），便于 AI 在 markdown 中追踪 block 引用

AI worker 输出 markdown 后，front-end 直接 `deserialize` 转 Plate fragment，零 schema 推断成本。

### 3.5 渲染层

Plate 提供 `platejs/react` 的 `Plate` / `usePlateEditor` / `Editor` 组件。`renderElement` / `renderLeaf` 回调是注入自定义 UI 的标准入口——Claread 现有 `PlateReaderSurface.tsx:282-334` 已经在用这一对回调渲染 `ReaderParagraphNode` / `ReaderSentenceNode` / `ReaderPlateTextLeaf`。

### 3.6 性能

[inference: 基于 community 博客 + Plate 文档片段]

Plate.js 在 1000 paragraph / 1000+ mark / 50+ suggestion 场景下的性能**没有官方公开基准**。社区博客（如 Liveblocks 2025-11）报告 ProseMirror 类编辑器在 1000+ node 下仍能保持 60fps 滚动，**前提是 mark / decoration 不放在叶子节点上**。Claread 的多 mark 重叠（vocab + grammar + user highlight + ask citation）需要做性能 spike 验证。

`@platejs/ai` 的 `streamInsertChunk` 走 batch commit（每个 chunk 一次性应用），避免 token-by-token DOM mutation。D2-S1 spike 必须验证这一假设在 1k token translation 流式注入下不掉帧。

### 3.7 SSR / Next.js

[inference: 社区实践]

Plate 在 Next.js App Router 下需要 `dynamic(() => import('...'), { ssr: false })` 包裹——`usePlateEditor` 依赖浏览器 API。Claread 当前 `apps/web/src/components/reader/plate/PlateReaderSurface.tsx` 的 `use client` 边界已经规避了这一点。

### 3.8 License 风险

[inference: D2-S0 spike 必须验证]

- Plate 主体（`@udecode/plate` / `platejs`）：**MIT**（需 spike 验证当前版本）
- `@platejs/ai`：Plate 商业版组件（**推断**，需查证；如商业需评估 Tiptap 替代或自实现 streaming wrapper）
- `@platejs/suggestion` / `@platejs/markdown`：通常与主体同 license

**D2-S0 spike 必须查证** `@platejs/ai` 是否商业 / 是否能自实现等价 API（参考 Tiptap `streamContent` + `beginPreview` 的简化版）。

### 3.9 Claread 已经在用 Plate.js

**核心发现**：`apps/web/src/components/reader/plate/PlateReaderSurface.tsx` 与 `ImmersiveReaderSurface.tsx` 都 `import { Plate, usePlateEditor } from "platejs/react"`，**readOnly 模式**。`apps/web/src/lib/reader-plate/` 目录下的"plate"是 **Claread 自创的领域命名**（`ReaderPlateDocument` / `ReaderParagraphNode` / `ReaderSentenceNode`），与 Plate.js 库同名巧合。Projection 函数 `renderSceneToPlateDocument` 把后端 scene 投影成 Slate-like children，再喂给 Plate.js。

**含义**：迁移到 Plate-first 不需要替换渲染层。**真正要做的是：把不可编辑升级为 partial editable、把后端 scene 替换为可被 patch 的 event 流、把 Ask 改为 document tools。**

---

## 4. AI + Rich-text Editor Best Practices

### 4.1 主流共识（2025-2026）

**反模式**：
- LLM 直接 `setContent(fullDoc)` 端到端重写：[Notion 1310 block 调 update_content 超时反面教材](https://zenn.dev/hideakitamai/articles/144f217cb32d73) 表明 1310 block 的页面调用 MCP `update_content` 持续超时，**解决是改用 REST API 逐块 DELETE**。
- token-by-token 直接插入 editor："dancing cursor issue" / "flicker of death"，每个 token 触发一次 DOM 重渲染。

**推荐模式**：
- **Tool Call + Review**：[Tiptap `@tiptap-pro/ai-toolkit`](https://tiptap.dev/docs/content-ai/capabilities/ai-toolkit/agents/review-changes/) 官方选择 tool call 路线：LLM 调 `tiptapRead`（带 range）/ `tiptapEdit`（带 reviewOptions），`reviewOptions: { mode: 'preview' }` 强制用户 accept 才 commit。
- **Block-level Operation + Diff Patch**：[EMNLP 2025 JSON Whisperer](https://arxiv.org/html/2510.04717v1) 实证 LLM 应输出 RFC 6902 diff patch 而非完整 JSON：token 节省 31%，编辑质量保持全量重写的 5% 内。
- **Streaming 走 hidden buffer + diff**：[Tiptap `streamContent`](https://tiptap.dev/docs/content-ai/capabilities/generation/text-generation/stream) 走 buffer 累积 + transform 验证 + 一次性 commit。
- **AI Agent 作为 CRDT Peer**：[Electric 2026-04](https://electric-sql.com/blog) 的方案是 AI agent 作为服务端 Yjs CRDT peer，tool call → tool runtime → Yjs operation，CRDT 自然处理冲突。**Claread 短期不做多人协作**，但留出 `editor.history` / `editor.tf.withoutNormalizing` 入口给未来。

### 4.2 Plate.js 的具体落地路径

Claread 的落地路径与 Tiptap 同构：

| Tiptap 模式 | Plate 等价 | Claread 场景 |
|---|---|---|
| `tiptapRead(from, to)` | `editor.api.getFragment()` + `api.markdown.serialize` 配合 `sentenceIdToPath` adapter | Ask `read_range` tool |
| `tiptapEdit(operations)` | `editor.tf.apply(ops)` 或 `editor.tf.insertNodes` | Ask `write_note` / `create_highlight` |
| `reviewOptions: {mode:'preview'}` | `editor.tf.ai.beginPreview()` + `applyAISuggestions` + 用户 `acceptAISuggestions` / `rejectAISuggestions` | 任何 AI 写入（vocab / grammar / supplement） |
| `streamContent` | `streamInsertChunk` + `withAIBatch` | 流式 translation 注入 |

**关键差异**：Claread 不引入 tool call LLM API——后端 worker 才是 LLM 调用的发起方，前端只消费 `plate_patches` 事件。LLM 的 tool call 在后端，editor 的 tool call 在前端，二者通过 plate_patches 桥接。

### 4.3 AI 写入作用域

[inference: 综合 Control Zero scoping / AgentScope / OpenClaw 实践]

作用域控制模式：
- **Path-based scoping**：glob pattern 定义允许 read/write path
- **Tool-level FLS**：每个 tool 独立授权
- **Editor node path 限制**：Plate 通过自定义 plugin 拦截 `editor.tf.*` 调用的 path

Claread 采用**双层校验**：
- **后端 Layer Publisher 拒绝**（权威）：domain fact 写入路径校验 sentenceId 是否属于 record；ops 的 target path 必须落在 owner 允许的 node 上
- **前端 `renderLeaf` / `onKeyDown` 镜像**（UX）：不允许 stable node 接收 selection；AI node 屏蔽 `Backspace` 之外的修改

### 4.4 Suggestion / Accept-Reject

[official: Tiptap preview mode / Plate `applyAISuggestions`]

| 方案 | 优点 | 缺点 |
|---|---|---|
| Suggestion（review mode） | 用户有最终控制；AI 错误可还原 | 延迟感；多次交互后疲劳 |
| Direct write（disabled mode） | 实时反馈；AI 感觉"更聪明" | 错误难以还原；用户失控感 |
| Hybrid（streaming + review） | 流式预览 + 最终审阅 | 实现复杂度高 |

Claread 默认 **Hybrid 模式**：translation layer 走 streaming + `streamInsertChunk`（用户看到流式过程但仍可 reject）；vocab / grammar 走 suggestion + `acceptAISuggestions`（review 模式）。

---

## 5. Fit For Claread

### 5.1 Stable Reading Base 不可变

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| 不可编辑 | readOnly 编辑器 | `readOnly` 已对齐 | ✅ 已就绪 |
| 节点级 lock | 无 | 自定义 `isSystemLocked(node)` 在 `editor.tf.*` 拦截 | ✅ 自定义可达 |
| 后端强制 | 不需要（后端 truth） | — | ✅ 后端无风险 |

### 5.2 Reading Units 不可变

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| sentence 节点 | `ReaderSentenceNode` | inline node | ✅ 已就绪 |
| offset 锚 | `sentenceId + UTF-16 + fnv1a32-utf16` | node path `[a, b, c]` | ⚠️ 需 adapter |
| 跨节点选择 | `MultiRangeAnchor` | `Multirange` decoration plugin | ⚠️ 需 adapter |

### 5.3 AI 增强层（partial editable）

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| AI owned 节点 | `origin: "ask_ai"` 字段 | 无原生 owner | ⚠️ 需 owner 权限层 |
| partial editable | 全部 readOnly | `readOnly=false` 实例 + 节点级 lock | ⚠️ 需 editable 实例 |
| 流式注入 | 无 | `streamInsertChunk` | ✅ 直接复用 |
| accept/reject | 无 | `applyAISuggestions` + `acceptAISuggestions` / `rejectAISuggestions` | ✅ 直接复用 |
| Markdown 互转 | 无 | `@platejs/markdown` | ✅ 直接复用 |

### 5.4 用户资产可编辑

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| 高亮 | `user_annotations` 持久化 | `mark` 附加在 text leaf | ⚠️ 需 owner + 回写路径 |
| 笔记 | `reader_notes` 持久化 | inline / block 节点 | ⚠️ 需 owner + 回写路径 |
| AI 不可覆盖 | 无强制 | 后端 Layer Publisher 拒绝 + 前端 `renderLeaf` | ⚠️ 需 owner 权限层 |

### 5.5 Ask 改文档

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| 受控 tool | 无（response_cards） | `editor.tf.*` command + 自定义 plugin | ⚠️ 需 Ask Document Tools 实现 |
| 作用域 | 无 | 节点级 lock + path 校验 | ✅ 与 owner 权限共用 |
| 工具撤销 | 无 | `withAIBatch` | ✅ 直接复用 |
| 流式响应 | 已有 | `streamInsertChunk` | ✅ 直接复用 |

### 5.6 渐进式渲染

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| 原文先到 | article_ready 路径 | `setValue(value)` 一次性 | ✅ 已就绪 |
| 译文渐进 | response_cards 覆盖 | `editor.tf.insertNodes` 增量 | ✅ 可用 |
| 多 mark 重叠 | 脆弱 | mark 附加在 leaf，天然支持 | ✅ 强于现状 |
| snapshot reload | `setValue(value)` | 同 | ✅ 已就绪 |

### 5.7 刷新恢复 / 断线 / 事件回放

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| snapshot 重建 | `reader_snapshots` 投影 | `editor.tf.setValue(value)` | ✅ 已就绪 |
| event patch 回放 | 无 | `editor.tf.apply(ops[])` 批量 | ✅ 直接复用 |
| 锚点稳定 | sentenceId 锚 | sentenceId ↔ path adapter | ⚠️ 需 adapter |
| 幂等 | D1 RFC 已要求 | `editor.tf.withoutSaving` 包 ops | ✅ 可达 |

### 5.8 RAG citation 回源

| 维度 | 现状 | Plate.js 能力 | 评估 |
|---|---|---|---|
| cite unit ref | `AnchorSegmentLike` | 派生 path | ✅ 已有 |
| 校验 | 后端 anchor validation | 后端权威 | ✅ 已就绪 |
| inline citation 渲染 | ask chat 内 | `inline mark` 注入 citation | ✅ 可用 |

### 5.9 Claread 需求 vs Plate.js 能力总结

| Claread 需求 | Plate.js | 评估 |
|---|---|---|
| Stable Base 不可变 | `readOnly` + 节点 lock | ✅ 完整支持 |
| Reading Units 不可变 | sentenceNode + 节点 lock | ✅ 完整支持 |
| AI 译文渐进到达 | `streamInsertChunk` + `setValue` fallback | ✅ 完整支持 |
| AI 批注 inline mark | mark 体系 | ✅ 完整支持 |
| AI 讲解支持 Markdown / table / code | `@platejs/markdown` + custom block | ✅ 完整支持（**强于现状**） |
| 用户高亮 / 笔记 | inline mark + block | ✅ 完整支持 |
| Ask 改文档 | `editor.tf.*` + owner 权限 | ✅ 完整支持（需 owner 实现） |
| 渐进式渲染 | setValue / insertNodes | ✅ 完整支持 |
| owner 权限分层 | 需自定义 | ⚠️ **Plate.js 不能直接解决**——需 owner 权限层 |
| sentenceId ↔ path adapter | 需自定义 | ⚠️ **Plate.js 不能直接解决**——需 adapter |
| 后端 truth 分离 | N/A（前端问题） | ⚠️ **Plate.js 不能直接解决**——需后端 domain fact 设计 |
| 长文性能（>10k block） | 无官方基准 | ⚠️ **Plate.js 不能直接解决**——D2-S0 spike 必须验证 |
| 多人协作 | `@platejs/yjs` 插件（**inference**） | ⚠️ **Plate.js 不能直接解决**——短期不做 |

**关键结论**：
- **Plate.js 直接支持的**：渲染、流式注入、Markdown 互转、accept/reject、snapshot reload、event patch 应用
- **Plate.js 不能直接解决**的：owner 权限层（需自定义 plugin）、sentenceId ↔ path adapter（需自定义）、后端 truth 分离（需后端架构配合）
- **未验证**的：1000+ paragraph 性能（**D2-S0 spike 必须跑**）、`@platejs/ai` License（**D2-S0 spike 必须查证**）

---

## 6. Proposed Architecture

### 6.1 形态

```text
Backend domain truth (unchanged):
  Stable Reading Base / Reading Units / Anchor Segments
  Enhancement Layers (structured, NOT patch sequence)
  User Editorial Assets
  Ask Supplements

新增 Layer Publisher 流程:
  worker → typed enhancement result
  → Layer Publisher (schema/anchor/source-grounded validate)
  → emit domain event (layer_published)
  → emit projection event (plate_patches: SlateOperation[])
  → atomic publish + 同一事务写 reader_events 两条

Reader Events 双轨:
  layer_published / layer_failed / parsed_decision_updated    → 业务事件，UI 触发层切换
  plate_patches / snapshot_rebuilt / plate_reset              → projection 事件，editor.tf 增量

Frontend:
  Plate.js (readOnly 默认, D5+ partial editable)
    nodes:
      reader_paragraph  (block)
      reader_sentence   (inline, 锚定 sentenceId)
      reader_translation (block, owner=ai)
      reader_grammar_note (block, owner=ai)
      reader_vocab_highlight (inline mark, owner=ai)
      reader_ai_supplement (block, owner=ai)
      reader_user_highlight (inline mark, owner=user)
      reader_user_note (block, owner=user)
      reader_ask_citation (inline mark, owner=ask, ephemeral)
    marks:
      ai_emphasis / user_emphasis / ask_citation / suggestion_pending
    adapters:
      sentenceIdToNodePath(editor, sentenceId) → Path
      pathToSentenceId(editor, path) → { sentenceId, startOffset, endOffset }

Ask Sidecar (D5+):
  tools:
    read_range(anchor, scope) → snippet
    create_highlight(anchor, color, scope) → user_highlight
    write_note(anchor, body_md, scope) → reader_note
    write_ai_supplement(anchor, body_md, parent_layer_type) → ai_supplement layer
    revise_ai_annotation(target_layer_id, body_md) → revision
  transport:
    tool call → tool executor (validation + Authorization Envelope check)
    → domain write (transactional)
    → reader_events emit (layer_published + plate_patches)
    → SSE/polling 投递
```

### 6.2 关键 Contract

```typescript
// packages/contracts/index.d.ts 新增

export interface PlatePatchEventDto {
  base_id: string
  base_version: number
  ops: SlateOperation[]  // Slate.js standard operations
  source_event_id: string  // 关联触发的 domain event
  source_layer_id: string | null  // 关联 enhancement_layer.id
}

export type AskToolName =
  | "read_range"
  | "create_highlight"
  | "write_note"
  | "write_ai_supplement"
  | "revise_ai_annotation"

export interface AskToolCallDto {
  tool_name: AskToolName
  input: Record<string, unknown>  // tool-specific
  // read_range: { anchor, scope }
  // create_highlight: { anchor, color, scope }
  // write_note: { anchor, body_markdown, scope }
  // write_ai_supplement: { anchor, body_markdown, parent_layer_type }
  // revise_ai_annotation: { target_layer_id, new_body_markdown }
}

export interface AskToolResultDto {
  tool_name: AskToolName
  output: Record<string, unknown>  // tool-specific
  plate_patches: PlatePatchEventDto[]  // 客户端应用
}
```

### 6.3 Backend 改动（最小化）

| 文件 | 改动 |
|---|---|
| `infra/migrations/0001_initial_schema.sql` | `reader_events.event_type` 加 CHECK 取值或转 TEXT；新增 `projection_meta` JSONB 字段（`base_version` / `ops_count` / `source_event_id`） |
| `services/api/app/services/reader_events/projection_emitter.py`（新） | `emit_plate_patches(record_id, base_version, ops, source_event_id)` |
| `services/api/app/services/enhancement/layer_publisher.py` | publish 末尾调用 `projection_emitter`（同事务） |
| `services/api/app/services/ask/document_tools.py`（D6 新） | Ask tools 执行器 |
| `services/api/app/llm/prompts/asksidecar/tools.py`（D6 新） | Ask tool schema |
| `services/api/app/services/reader_scene.py` | 保留，不扩展 |

### 6.4 Frontend 改动

| 文件 | 改动 |
|---|---|
| `apps/web/src/lib/reader-plate/model/types.ts` | `ReaderPlateDocument` → `ReaderPlateValue`（= Slate Value）；新增 owner 字段约定 |
| `apps/web/src/lib/reader-plate/plugins/`（新） | 9 个 Plate plugin：paragraph / sentence / translation / grammar / vocab / user-highlight / user-note / ai-supplement / ask-citation |
| `apps/web/src/lib/reader-plate/adapters/sentenceId.ts`（新） | `sentenceIdToPath` / `pathToSentenceId` |
| `apps/web/src/lib/reader-plate/permissions/owner.ts`（新） | `canEdit(node, owner)` / `canDelete(node, owner)` / `isSystemLocked(node)` |
| `apps/web/src/components/reader/plate/PlateReaderSurface.tsx` | 接入 plate_patches；保留 readOnly；D5+ partial editable |
| `apps/web/src/components/reader/plate/ImmersiveReaderSurface.tsx` | 同上 |
| `apps/web/src/lib/reader-plate/projection/render-scene-to-plate-document.ts` | **保留**（D4 path） |
| `apps/web/src/services/api/reader-events.ts`（新） | 订阅 plate_patches，`editor.tf.apply(ops)` |
| `apps/web/src/services/ask/document-tools.ts`（D6 新） | Ask tool call 客户端 |

### 6.5 D1-D6 阶段落点

| 阶段 | Plate-first 落点 |
|---|---|
| D1 | 决策 D1-012 ~ D1-017 写入 `target-architecture.md`；本章已落地 |
| D2 | 跑 D2-S0 / S1 / S2 spike（详见 §12） |
| D3 | `reader_events.projection_meta` JSONB + `projection_emitter.py` 骨架；D3 任务包验收已补 |
| D4 | 走 `renderSceneToPlateDocument`；D4 任务包验收已补；**不接 plate_patches** |
| D5 | Layer Publisher emit plate_patches + frontend `editor.tf.apply(ops)` + owner 权限层 + Ask `read_range` / `create_highlight` / `write_note`；D5 任务包验收已补 |
| D6 | Ask `write_ai_supplement` / `revise_ai_annotation` + Candidate Base preview/edit/confirm |

---

## 7. Backend Truth vs Plate Projection

### 7.1 核心原则

- **Plate document 不是 truth**——它是 domain fact 的视图。Claread 必须能在不打开 Plate 的情况下回答："这篇 record 有多少个 translation layer、覆盖率多少、用户做了几个高亮"。
- **Domain fact 是异步真相**：刷新、断线、移动端降级都能从 `enhancement_layers` / `user_annotations` / `reader_notes` / `ask_supplements` 这四张表重建 Plate Value。
- **plate_patches 是副产物**：写在 `reader_events.projection_meta` JSONB 字段。客户端订阅时直接 `editor.tf.apply(ops)`；客户端不订阅时 polling snapshot 也能从 domain fact 重建。

### 7.2 反模式

❌ **不**让 `enhancement_layers` 表存 `ops: SlateOperation[]`——这会让 Layer 表绑定 Slate 数据结构，破坏：
- RAG citation 回源（需要 structured anchor + source + version + worker info）
- eval 重放（需要 structured layer content + reasoning）
- 非 Web 客户端（需要从 structured layer 重建自己的 UI）
- 未来模型升级（如果 Plate.js 改名 / 换 API，op 数据全废）

❌ **不**让 `user_annotations` / `reader_notes` 存 Plate node path——node path 在 patch apply 后会变。必须存 `sentenceId + UTF-16 offset + fnv1a32-utf16 hash`（D1-005 强约束）。

❌ **不**让 Plate Value 落库——Plate Value 是 client-only 状态。`reader_snapshots` 存的是 domain fact 的 snapshot（如 `enhancement_layers` 列表），不是 Plate Value。

### 7.3 真相-投影双轨

```text
Domain truth (PostgreSQL):
  reading_records / reading_bases / reading_units
  enhancement_layers (typed content + anchor + version + producer)
  user_annotations (anchor + body)
  reader_notes (anchor + body)
  ask_supplements (anchor + body + parent_layer_type + version)
  parsed_decisions (unit_id + status + rationale)

Projection:
  reader_events.projection_meta (ops + base_version + source_event_id)
  reader_snapshots (domain fact snapshot, NOT Plate Value)

Frontend projection (transient):
  Plate Value (descendant tree) — 由 reader-events / reader-snapshots 投影得到
  sentenceId ↔ path map — 缓存，patch apply 后失效
```

---

## 8. Progressive Rendering / Patch Model

### 8.1 D4 article_ready 路径

```text
Web Reader GET /reader/records/{id}
  → server 返回 reader_scene + reader_snapshots.last_event_sequence
  → frontend renderSceneToPlateDocument(reader_scene)
  → editor.tf.setValue(value)
  → 渲染 article body
  → 订阅 SSE /reader/records/{id}/stream
```

**D4 不接 plate_patches**——只订阅 domain event。translation layer 到达时强制 setValue 重投影。D4 任务包验收已补。

### 8.2 D5+ 增强层路径

```text
Server Layer Publisher:
  worker → typed enhancement result
  → schema/anchor/source-grounded validate
  → INSERT enhancement_layers
  → INSERT reader_events (event_type='layer_published', payload_json={...domain...})
  → INSERT reader_events (event_type='plate_patches', payload_json={...ops...}, projection_meta={base_version, source_event_id})
  → 全部同事务

Frontend SSE 订阅:
  on event_type='layer_published':
    → 更新 UI 状态（translation unit progress bar）
  on event_type='plate_patches':
    → editor.tf.withoutNormalizing(() => {
        for (const op of event.payload_json.ops) {
          editor.tf.apply(op)  // or apply all in batch
        }
      })
    → 流式翻译走 streamInsertChunk (持续收到 patch)
    → 静态标注走 applyAISuggestions + 用户 accept
```

### 8.3 Snapshot reload + Patch replay

```text
Web Reader disconnect → reconnect:
  GET /reader/records/{id}?after={last_event_sequence}
  → server 返回 {snapshot, missed_events[]}
  → if missed_events.length > 0:
      editor.tf.setValue(renderSceneToPlateDocument(snapshot))
      for (const event of missed_events) {
        if (event.event_type === 'plate_patches') {
          editor.tf.apply(event.payload_json.ops)
        } else {
          // domain event — handled in normal flow
        }
      }
  → if missed_events.length === 0:
      continue from last_event_sequence
```

**关键**：snapshot 是从 domain fact 重建（`renderSceneToPlateDocument`），不是从 plate_patches 重放。重放只用于断线期间的增量事件。

### 8.4 Streaming Insert 反模式规避

D2-S1 spike 验证两条路径：

| 路径 | 行为 | 预期 |
|---|---|---|
| 反模式：token-by-token `editor.tf.insertText` | 每个 SSE chunk 触发一次 `insertText` | 帧率 < 30fps，cursor 抖动 |
| 推荐：`streamInsertChunk` + `withAIBatch` | SSE chunk 累积到 batch（如 50ms / 256 token）后 commit | 帧率 ≥ 60fps，cursor 稳定 |

---

## 9. Permission Model

### 9.1 Owner 类别

| Owner | 节点类型 | 用户权限 | AI 权限 |
|---|---|---|---|
| **stable** | `reader_paragraph` / `reader_sentence` | 不可编辑、不可删除 | 不可编辑、不可删除 |
| **ai** | `reader_translation` / `reader_grammar_note` / `reader_vocab_highlight` / `reader_ai_supplement` | 不可编辑、可删除、可 accept/reject suggestion | 可更新（走 worker） |
| **user** | `reader_user_highlight` / `reader_user_note` | 可编辑、可删除 | 不可覆盖 |
| **ask** | `reader_ask_citation` | 可关闭 | ephemeral（session 结束清除） |

### 9.2 校验双层

**后端权威**（`Layer Publisher` + `Ask document_tools`）：
- Layer Publisher reject ops.target.path 落在 stable 节点上
- Ask tool reject 写入 stable 节点 / 覆盖 user 节点
- 写入 user 节点必须经用户显式操作（API 校验 `Authorization Envelope.user_asset_write_permission`）

**前端 UX 镜像**（`apps/web/src/lib/reader-plate/permissions/owner.ts`）：
- `renderLeaf` 读 `owner`，stable 节点禁用 `Backspace` / `Delete` / 文本修改
- `editor.tf.*` 自定义 plugin 拦截 owner 不允许的操作（在执行前 throw）
- `onKeyDown` 屏蔽 `Cmd+I` 等格式化快捷键对 stable 节点的影响

### 9.3 Ask Document Tools 作用域

D5+ 工具集（D1-015 决策）：

| 工具 | owner=stable | owner=ai | owner=user | owner=ask |
|---|---|---|---|---|
| `read_range` | ✅ | ✅ | ✅ | ✅ |
| `create_highlight` | ❌ | ❌ | ❌（用户手动） | ❌ |
| `write_note` | ❌ | ❌ | ❌（用户手动） | ❌ |
| `write_ai_supplement` | ❌（追加） | ✅（追加） | ❌ | ❌ |
| `revise_ai_annotation` | ❌ | ✅（修订） | ❌ | ❌ |

**说明**：
- `create_highlight` / `write_note` 是用户手动操作（不是 Ask tool），不计入 Ask 工具集作用域
- `write_ai_supplement` 在 stable / ai 节点"追加"补充（不修改原节点）
- `revise_ai_annotation` 仅修订 ai 节点（生成新 version，旧 version 保留）

---

## 10. Anchor / Citation Strategy

### 10.1 现状

Claread 的 anchor 系统基于 `sentenceId + UTF-16 offset + fnv1a32-utf16 hash`（D1-005 强约束）。`apps/web/src/lib/reader-plate/bridges/selection/read-plate-reader-selection.ts` 已实现 DOM selection → anchor 的桥接。

### 10.2 Adapter 要求

`apps/web/src/lib/reader-plate/adapters/sentenceId.ts`（D5 新）：

```typescript
export function sentenceIdToPath(editor: PlateEditor, sentenceId: string): Path | null
export function pathToSentenceId(editor: PlateEditor, path: Path): { sentenceId: string; startOffset: number; endOffset: number } | null
```

**缓存**：sentenceId → path 的 WeakMap，patch apply 后失效重建（通过 `editor.onChange` hook 触发）。

### 10.3 RAG Citation

- 检索结果返回 `{unit_id, offset, snippet, score, source_scope}`（D1-004 决策）
- 前端用 `pathToSentenceId(path)` 转 sentenceId + offset，作为 `reader_ask_citation` 节点的 `data-citation` 属性
- 渲染：inline mark 标记引用起点，hover 显示 snippet + link to unit

### 10.4 一致性保证

- 后端 `text_anchors.py:validate_segment_against_sentence` 校验 offset + hash
- 前端 `apps/web/src/lib/reader-plate/primitives/text-anchor.ts:hashAnchorText` 同样算法（`fnv1a32-utf16`）
- 每次 patch apply 后**不重建** hash——`reader_sentence` 节点的 `data-sentence-id` 是 stable ID，不会变

---

## 11. Risks

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Plate node path 在 patch apply 后失效（layer 反复 publish 改变 sentenceNode 索引） | 中 | 高 | adapter layer 必须以 `sentenceId` 为锚，path 仅作缓存；patch emit 时校验 sentenceId 仍存在 |
| AI 写入越权（写 stable_base 节点 / 覆盖 user asset） | 中 | 高 | owner 权限层 + Layer Publisher schema 校验 + `tf.ai.beginPreview` rollback + ask sidecar Authorization Envelope |
| streaming 抖动（"dancing cursor"） | 低 | 中 | D2-S1 spike 验证，反模式即回退到 batch 推送（每 50ms / 256 token 一次 commit） |
| sentenceId ↔ path 转换成本 | 中 | 中 | 缓存 map（sentenceId → path），patch 之后失效重建 |
| `@platejs/ai` 商业 License 风险 | 低 | 中 | D2-S0 spike 查证；如商业需评估 Tiptap 替代或自实现 streaming wrapper（参考 `streamInsertChunk` + `withAIBatch` 简化版） |
| Plate 版本升级 breaking change | 中 | 中 | D2 spike 后定 major version 锁；`@platejs/*` 包尽量 pin 到 minor |
| Owner 权限层与 SSR 冲突（SSR 渲染时无用户上下文） | 低 | 中 | owner 决策在 client-only 路径；SSR 渲染走 readOnly 默认；owner 校验在 client `useEffect` 后才生效 |
| Yjs CRDT 协作后续扩展 | 低 | 低 | 设计时留出 `editor.history` 与 `editor.tf.withoutNormalizing` 入口（D2-S0 spike 验证） |
| 长文性能（>10k block） | 中 | 高 | **D2-S0 spike 必须跑 1000 paragraph 性能基准** |
| Markdown fragment 反序列化安全（XSS） | 中 | 中 | `@platejs/markdown` 默认 sanitize，clamp 长度，禁止 script tag；`write_ai_supplement` 强制 Markdown AST 校验 |
| `enhancement_layers` 表被错误改成 patch sequence | 低 | 高 | D1-013 决策 + agent-brief 不可违反规则 + D3 任务包验收测试 |
| 小程序端使用 plate_patches | 低 | 中 | D1-014 决策明确"非 Web 客户端继续 polling snapshot"；agent-brief 不可违反规则 |

---

## 12. Spike Plan

### 12.1 D2-S0 Plate-first Projection Spike（4-5 天）

**目标**：验证 Plate.js 能否承载 Claread 完整场景，包括 AI 写入路径与权限分层。

**最小样例**：

1. **Plain Plate 渲染**
   - 1 个 Reading Record，20 paragraphs
   - 原文 + 译文 + 5 个 vocab mark + 3 个 grammar note + 2 个 user highlight + 1 个 user note
   - 用 `@platejs/ai` 的 `applyAISuggestions` 流式注入 vocab mark（模拟 D5 layer arrival）

2. **permission 验证**
   - Stable Base node 试图 edit → reject
   - AI translation node 用户试图 edit → reject（只允许 delete）
   - User note 用户 edit → accept

3. **anchor 互转**
   - sentenceId 选区 → Plate node path
   - Plate node path 选区 → sentenceId + UTF-16 offset
   - 与后端 `text_anchors.py` round-trip 一致

4. **plate_patches 事件流**
   - 模拟 SSE 投递：layer_published 事件携带 1 个 SlateOperation
   - 前端 `editor.tf.apply(ops)` 增量更新
   - 验证 undo/redo 在 patch 应用后仍正确

5. **snapshot reload + patch replay**
   - 提交时 snapshot
   - 模拟断线
   - 重连从 `last_event_sequence + 1` 拉 plate_patches
   - 验证 editor state 与重连前一致

6. **Ask tool 调用链路**
   - 用户选中文本 → 工具栏出现 "Ask"
   - 调 `read_range` tool → 显示 snippet
   - 调 `write_note` tool → 生成 user_note + emit plate_patches

**性能基准**：
- 200 paragraphs / 1000 sentences 渲染时间 < 2s
- 100 个 inline mark / 50 个 user note / 30 个 suggestion 滚动 60fps
- streamInsertChunk 流式注入 1k token 不掉帧

**权限指标**：
- Stable Base 节点 edit attempt 100% reject
- AI owned 节点 user delete 100% allowed
- User owned 节点 AI overwrite 100% reject

**anchor 指标**：
- sentenceId ↔ path round-trip 100% 一致
- 后端 anchor validation 100% 通过

**代码复杂度对比**：
- 当前 `renderSceneToPlateDocument` 行数 vs 新 `renderSceneFromDomain` 行数
- 当前 `readPlateReaderSelection` 复用度

**License 验证**：
- 查证 `@platejs/ai` / `@platejs/suggestion` / `@platejs/markdown` 的 License
- 如商业需列出替代方案

### 12.2 D2-S1 Streaming Insert 反模式验证（1 天）

**目标**：验证 `@platejs/ai` 的 `streamInsertChunk` + `tf.ai.beginPreview` 不出现 "dancing cursor issue"。

**样例**：
- 后端流式推送 1k token translation
- 前端应用 plate_patches（每次 SSE 推 1 patch）
- 与 token-by-token 直接 `editor.tf.insertText` 对比
- 监控：帧率、DOM mutation count、cursor 抖动

### 12.3 D2-S2 render_scene 与 plate_patches 共存（1 天）

**目标**：验证 D4 文本路径沿用 `renderSceneToPlateDocument`、D5+ 走 `plate_patches` 时，两条路径不冲突。

**样例**：
- D4 article_ready 用 renderSceneToPlateDocument
- 同一 record 进入 D5，第一条 vocab layer 用 plate_patches
- 验证：editor state 正确、anchor 不变、undo/redo 正常

### 12.4 Spike 验收总览

| 维度 | 验收 |
|---|---|
| 性能 | 1000 paragraph 渲染 < 2s，60fps 滚动 |
| 权限 | Stable / AI / User owner 校验 100% 正确（1000 次随机 attempt） |
| anchor | sentenceId ↔ path round-trip 100% 一致 |
| patch replay | snapshot reload + 100 个 plate_patches 应用后 editor state 完全一致 |
| streaming | 1k token 流式注入不掉帧，cursor 不抖动 |
| 共存 | render_scene 与 plate_patches 共存无冲突 |
| License | 商业 / 免费的边界明确，必要时列替代方案 |
| 复杂度 | 新增代码 < `renderSceneToPlateDocument` 现有行数 × 1.5 |

---

## 13. Open Questions

1. **D-P6**：是否在 Plate 投影层预留 Yjs CRDT 入口？短期不实现，但 plugin 体系是否需要暴露 `editor.history` 与 `editor.tf.withoutNormalizing` 给未来协作用？**建议**：预留入口，不实际接入。
2. **D-P7**：owner 权限层是 Plate plugin 内部校验（`renderLeaf` 读 owner）还是后端 schema 校验（拒绝 publish）？**建议**：双层——后端权威 + 前端 UX 镜像。
3. **D-P8**：Ask 工具调用走 SSE 同步回包还是 polling？tool call 比 layer publish 延迟敏感，**建议** SSE 同步 + 客户端 optimistic UI。
4. **D-P9**：`@platejs/ai` 的 License 边界？需 D2-S0 spike 查证。
5. **D-P10**：`applyAISuggestions` 输出的 transient suggestion node 与现有 `reader_grammar_note` / `reader_vocab_highlight` 节点如何区分？是新节点类型还是用 mark 区分？**建议**：新节点类型 `reader_ai_suggestion_node`，用户 accept 后转为 `reader_grammar_note`。
6. **D-P11**：是否需要为 `renderSceneToPlateDocument` 写"反向"函数（Plate Value → domain fact）？snapshot reload 走"domain → Plate"路径即可，反向只在调试时用。**建议**：先不写反向函数，D5+ 按需。
7. **D-P12**：owner 字段存哪里？Plate node 的 `data-owner` 属性 vs `data.fields.owner`？**建议**：`data.fields.owner`（与 `definePlateDocumentModel` 的 fields 声明一致）。
8. **D-P13**：sentenceId ↔ path adapter 在多人 / 多次 patch apply 后如何失效？是否需要 `editor.onChange` hook 监听 + WeakMap 重建？**建议**：先实现 WeakMap + onChange 重建，D2-S0 spike 验证性能。
9. **D-P14**：plate_patches 走 SSE 还是走 polling？**建议**：SSE 主、polling 兜底（与 D1-014 决策一致）。
10. **D-P15**：candidate_base_ready 路径下，Plate 如何处理高影响适配的可编辑态？用户编辑后再 confirm，Plate editor state 如何与 supersede 协同？**建议**：编辑态走独立 `editor_scratch`（local state），confirm 时整段 Plate Value 转 domain fact 创建新 record。

---

## 14. Sources

### Plate.js 官方一手

- [Plate 主仓库](https://github.com/udecode/plate)（40k+ snippets，benchmark 84.44）— `/udecode/plate`
- [Plate 文档站](https://platejs.org)（3487 snippets，benchmark 83.71）— `/websites/platejs`
- [Plate Playground Template](https://github.com/udecode/plate-playground-template) — Next.js + Plate AI 完整参考
- Plate AI 插件文档 — `/udecode/plate/content/docs/(plugins)/(ai)/ai.mdx, ai.cn.mdx`
- Plate Markdown 插件 — `/udecode/plate/content/docs/(plugins)/(serializing)/markdown.mdx`
- Plate Document Model ADR — `/udecode/plate/docs/research/decisions/arthrod.md`

### Tiptap AI Toolkit（对照参考）

- [Tiptap AI Toolkit 概览](https://tiptap.dev/docs/content-ai/capabilities/ai-toolkit/overview) — `/ueberdosis/tiptap-docs`
- [Tiptap `tiptapRead` / `tiptapEdit` / `streamContent`](https://tiptap.dev/docs/content-ai/capabilities/ai-toolkit/agents/review-changes/suggestions.mdx) — reviewOptions: 'preview' 强制 accept
- [Tiptap Stream API](https://tiptap.dev/docs/content-ai/capabilities/generation/text-generation/stream) — streaming 模式
- [Tiptap Server AI Toolkit REST API](https://tiptap.dev/docs/content-ai/capabilities/server-ai-toolkit/api-reference/rest-api.mdx) — 服务端 tool call 协议

### AI 编辑器最佳实践

- [Notion API 文档](https://developers.notion.com/reference/update-a-block) — block-level PATCH 范式
- [Notion 大文档性能反面教材](https://zenn.dev/hideakitamai/articles/144f217cb32d73) — 1310 block MCP `update_content` 超时
- [JSON Whisperer（EMNLP 2025）](https://arxiv.org/html/2510.04717v1) — LLM 输出 RFC 6902 diff patch，token 节省 31%
- [Electric 2026-04](https://electric-sql.com/blog) — AI agent 作为服务端 Yjs CRDT peer
- [Yjs 文档](https://docs.yjs.dev) — CRDT 协作基础

### Claread 内部

- [`docs/initiatives/reader-agentic-orchestration/target-architecture.md`](../initiatives/reader-agentic-orchestration/target-architecture.md) §"Reader Projection 与 Plate Document"、决策 D1-005 / D1-012 ~ D1-017
- [`docs/initiatives/reader-agentic-orchestration/agent-brief.md`](../initiatives/reader-agentic-orchestration/agent-brief.md) §"渲染层与 Plate 不可违反规则"
- [`docs/initiatives/reader-agentic-orchestration/implementation-plan.md`](../initiatives/reader-agentic-orchestration/implementation-plan.md) D2-S0 / S1 / S2 spike
- [`apps/web/src/components/reader/plate/PlateReaderSurface.tsx`](../../apps/web/src/components/reader/plate/PlateReaderSurface.tsx) — 现有 Plate.js 只读渲染
- [`apps/web/src/components/reader/plate/ImmersiveReaderSurface.tsx`](../../apps/web/src/components/reader/plate/ImmersiveReaderSurface.tsx) — 现有 Plate.js 只读渲染
- [`apps/web/src/lib/reader-plate/model/types.ts`](../../apps/web/src/lib/reader-plate/model/types.ts) — Claread 自创 `ReaderPlateDocument` 命名（与 Plate.js 同名巧合）
- [`apps/web/src/lib/reader-plate/projection/render-scene-to-plate-document.ts`](../../apps/web/src/lib/reader-plate/projection/render-scene-to-plate-document.ts) — D4 文本路径 projection（保留）
- [`apps/web/src/lib/reader-plate/bridges/selection/read-plate-reader-selection.ts`](../../apps/web/src/lib/reader-plate/bridges/selection/read-plate-reader-selection.ts) — 选区 → anchor 桥接
- [`services/api/app/services/text_anchors.py`](../../services/api/app/services/text_anchors.py) — UTF-16 offset + fnv1a32-utf16
- [`services/api/app/services/reader_scene.py`](../../services/api/app/services/reader_scene.py) — D4 reader_scene 来源（保留）
- [`packages/contracts/index.d.ts`](../../packages/contracts/index.d.ts) — AnchorSegmentLike / computeUtf16FNV1a（D1-005 强约束）

---

## 15. 实施时序（建议）

| 阶段 | 任务 | 产出 | 风险 |
|---|---|---|---|
| **D2-S0** | Plate-first Projection Spike | 1000 paragraph 性能 / owner 权限 / sentenceId adapter / patch replay / Ask tool 端到端 / License 验证 | 高 |
| **D2-S1** | Streaming Insert 反模式验证 | 1k token 流式不掉帧 | 中 |
| **D2-S2** | render_scene 与 plate_patches 共存 | 双轨运行无冲突 | 中 |
| **D3** | 后端 `projection_emitter.py` + `reader_events.projection_meta` JSONB | 后端骨架可 emit plate_patches，无前端消费者 | 低 |
| **D4** | 走 `renderSceneToPlateDocument` 路径 article_ready | D4 纵切走通 | 低 |
| **D5** | Layer Publisher emit plate_patches + frontend `editor.tf.apply` + owner 权限 + Ask `read_range` / `create_highlight` / `write_note` | 增强层增量到达 | 高 |
| **D6** | Ask `write_ai_supplement` / `revise_ai_annotation` + Candidate Base preview/edit | Ask 改文档 + 候选正文编辑 | 高 |
| **未来** | Yjs CRDT 协作 / 移动端 Plate.js / 长文 virtualization | 按需 | 低 |

**关键风险节点**：D2-S0 spike（性能 + License 验证）必须通过才能进入 D3；D5 是 Plate-first 的主战场（增强层增量到达 + owner 权限），失败则回退到 D4 setValue 重投影路径。
