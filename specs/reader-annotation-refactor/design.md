# Design Document

## Overview

本设计文档定义 `analysis record Reader` 标注体系重构的技术方案。目标不是在旧“用户学习资产”链路上做兼容层，而是以一次彻底收口为前提，重建为以下四条清晰能力：

- 文章收藏
- 用户高亮
- 用户笔记
- Ask Claread 围绕当前文章与显式稳定引用工作的读写能力

本轮明确采用“删旧建新”策略：

- 删除文本收藏
- 删除用户学习资产聚合页与聚合接口
- 将 `user_annotations` 收口为纯高亮
- 新增独立 `reader_notes`
- 重构 Reader 与 Ask Claread，使代码库只保留一套新逻辑

本设计仅覆盖 `analysis record Reader`。`Daily Reader` 不在本轮范围内。

## Goals

- 建立单一一致的 Reader 交互模型，消除“高亮/笔记/收藏/资产页/Ask 并行语义”
- 让 Web Reader 成为用户阅读痕迹的唯一主场
- 将 Ask Claread 从“资产中心”模式收口为“当前文章 + 显式引用”模式
- 用可删除旧逻辑的方式重构，而不是通过兼容分支保留历史链路

## Non-Goals

- 不改造 `Daily Reader`
- 不引入新的用户高亮样式族
- 不保留旧 `/excerpt-assets` 或“文本收藏”兼容路径
- 不支持编辑 note quote identity
- 不在本轮为小程序新增完整 note authoring

## Architecture Summary

重构后 Reader 领域拆成三个独立子系统：

1. `article_favorite`
   - 仅处理文章收藏
   - 不再理解 `sentence / text_range / multi_text`
2. `highlight_annotation`
   - 仅处理用户高亮
   - 支持 `sentence / text_range / multi_text`
   - 负责高亮冲突解析与合并
3. `reader_note`
   - 仅处理用户笔记
   - 使用 quote identity 精确命中
   - 不参与高亮冲突解析

Ask Claread 改为围绕上述三个系统读取或写入，但不再通过“用户学习资产聚合层”间接操作。

## Module Boundaries

### Backend

- `services/api/app/services/user_assets/favorites.py`
  - 重构为纯文章收藏服务
- `services/api/app/services/user_annotations.py`
  - 重构为纯高亮服务
- `services/api/app/services/reader_notes.py`
  - 新增，承载 note CRUD 与 quote identity 解析
- `services/api/app/services/excerpt_assets.py`
  - 删除
- `services/api/app/services/reader_ask/service.py`
  - 重写 attachment / citation / write action 适配逻辑
- `services/api/app/api/routes/excerpt_assets.py`
  - 删除
- `services/api/app/api/routes/reader_notes.py`
  - 新增

### Web

- `apps/web/src/app/(app)/reader/[recordId]/ReaderWorkbench.tsx`
  - 保留为 orchestration hub，但状态机要按双模型重组
- `apps/web/src/components/reader/SelectionToolbar.tsx`
  - 删掉文本收藏与内嵌 note input 旧心智
- `apps/web/src/components/reader/AnnotationGutter.tsx`
  - 从“句侧资产 marker”改为“句侧 note/highlight marker”
- `apps/web/src/components/reader/AnnotationSlip.tsx`
  - 删除或替换为新的 note rail/card 体系
- `apps/web/src/components/reader/AiWorkspacePanel.tsx`
  - Ask attachment、trace、action confirm 改为新模型
- `apps/web/src/app/(app)/library/assets/page.tsx`
  - 删除

### Mini-Program

- `apps/miniprogram/src/pages/result/index.tsx`
  - 收口旧选区写状态机
- `apps/miniprogram/src/components/ReadingSelectionToolbar`
  - 删除旧文本收藏/旧 note authoring 路径
- `apps/miniprogram/src/components/UserNoteSheet`
  - 删除
- `apps/miniprogram/src/packageA/excerpts`
  - 删除
- 小程序 Reader 仅保留新模型下的高亮/笔记查看态

## Data Model

## 1. Favorites

`favorite_records` 收口为文章收藏唯一模型。

### Resulting Semantics

- `target_type` 仅允许文章级目标
- 对 analysis record，继续使用 `analysis_record`
- 保留 `daily_reader_article` 作为共享基础模型能力，但本轮不改 Daily Reader 产品层

### Required Changes

- 删除 `sentence / text_range / multi_text` 相关校验与分支
- 删除按 `target_key` 删除文本收藏路径
- 删除任何以 favorite 作为 Reader 文本锚点资产的 contract

## 2. User Highlights

继续使用 `user_annotations`，但收口为纯高亮表。

### Resulting Fields

- `id`
- `user_id`
- `analysis_record_id`
- `anchor_type`
- `target_key`
- `paragraph_id`
- `sentence_id`
- `selected_text`
- `start_offset`
- `end_offset`
- `text_hash`
- `color`
- `payload_json`
- `created_at`
- `updated_at`
- `deleted_at`
- `deleted_by`

### Removed Semantics

- `annotation_type='note'`
- `note` 字段的业务语义
- `highlight + note` 混合 upsert 规则
- `PATCH` 修改 note 的语义

### Anchor Types

- `sentence`
- `text_range`
- `multi_text`

`paragraph` 不是本轮长期产品对象，设计上不再作为用户 authoring 目标。若数据库列暂时保留，也不得再暴露到产品 contract。

## 3. Reader Notes

新增 `reader_notes` 表，独立于 `favorites` 与 `user_annotations`。

### Proposed Fields

- `id UUID PRIMARY KEY`
- `user_id UUID NOT NULL`
- `analysis_record_id UUID NOT NULL`
- `anchor_sentence_id TEXT NOT NULL`
- `quote_mode TEXT NOT NULL`
  - `sentence`
  - `text_range`
  - `multi_text`
- `target_key TEXT NOT NULL`
- `paragraph_id TEXT NULL`
- `sentence_id TEXT NULL`
- `selected_text TEXT NOT NULL`
- `start_offset INTEGER NULL`
- `end_offset INTEGER NULL`
- `text_hash TEXT NULL`
- `payload_json JSONB NOT NULL DEFAULT '{}'::jsonb`
- `note_text TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`
- `deleted_at TIMESTAMPTZ NULL`
- `deleted_by UUID NULL`

### Quote Payload

`payload_json` 至少包含：

- `segments[]`
  - `paragraph_id?`
  - `sentence_id`
  - `selected_text`
  - `start_offset`
  - `end_offset`
  - `text_hash`
- `quoted_text`
- `source_surface`
- `quote_mode`

### Identity Rules

- `sentence`: `record_id + sentence_id + whole_sentence`
- `text_range`: `record_id + sentence_id + start_offset + end_offset + text_hash`
- `multi_text`: `record_id + ordered segments[]`

`target_key` 为 note identity 的 canonical string，负责精确命中与去重。

## Target Key Strategy

高亮与笔记都保留 canonical `target_key`，但二者不共享对象空间。

### Highlight Target Keys

- `record:{record_id}:sentence:{sentence_id}`
- `record:{record_id}:range:{sentence_id}:{start_offset}:{end_offset}:{text_hash}`
- `record:{record_id}:multi:{...segments-hash...}`

### Note Target Keys

采用同一编码策略，但存在于 `reader_notes` 独立表中。

这样可以：

- 复用现有文本锚点验证能力
- 保持 exact-hit 行为稳定
- 避免 note 身份依赖 annotation id

## Backend API Design

## 1. Favorites API

保留文章收藏相关接口，但请求/响应 contract 必须收口为文章语义。

### Keep

- 文章收藏创建
- 文章收藏删除
- 文章收藏查询

### Remove

- 文本收藏 target create/delete
- 文本 favorite 状态查询
- `target_type + target_key` 文本锚点接口

## 2. User Annotations API

`/user-annotations` 改为纯高亮接口。

### POST `/user-annotations`

- 创建或更新高亮
- 输入仍支持 `sentence / text_range / multi_text`
- 不再接受 `annotation_type='note'`
- 不再接受 `note`

### PATCH `/user-annotations/{id}`

- 仅允许变更 `color`

### DELETE `/user-annotations/{id}`

- 删除高亮

### List

- 返回当前 record 下的用户高亮

## 3. Reader Notes API

新增 `/reader-notes`。

### POST `/reader-notes`

输入：

- `analysis_record_id`
- `quote_mode`
- `anchor_sentence_id`
- `selected_text`
- `sentence_id?`
- `paragraph_id?`
- `start_offset?`
- `end_offset?`
- `text_hash?`
- `segments[]?`
- `note_text`
- `payload_json`

行为：

- 先 canonicalize quote
- 生成 `target_key`
- 若该 user + record + `target_key` 已存在，则返回已有 note 并进入“编辑对象”语义
- 否则创建新 note

### GET `/reader-notes?record_id=...`

返回当前文章全部 notes，供 Web note rail 与小程序查看态消费。

### PATCH `/reader-notes/{id}`

仅允许更新：

- `note_text`

禁止更新：

- `target_key`
- `quote_mode`
- `segments`
- `start_offset/end_offset`
- `selected_text`

### DELETE `/reader-notes/{id}`

软删除 note。删除后用户可重新选 quote 创建新 note。

## Validation Strategy

高亮与笔记都必须复用 render-scene 校验，不允许只靠前端传值。

### Highlight Validation

- `sentence` 校验句子存在
- `text_range` 校验 UTF-16 offsets、`selected_text`、`text_hash`
- `multi_text` 校验 segments 顺序、范围与 render scene 一致

### Note Validation

- 使用与高亮相同的 text anchor validators
- `anchor_sentence_id` 必须等于：
  - `sentence` -> 当前句
  - `text_range` -> 当前句
  - `multi_text` -> 第一段所在句

## Highlight Conflict Resolver

高亮保留“合并/扩展”逻辑，但只对高亮生效。

### Resolution Rules

1. exact match
   - 命中已有高亮，更新该高亮
2. subset
   - 命中已有高亮，不创建第二条
3. superset
   - 扩展已有高亮到新范围
4. overlap with multiple highlights
   - Phase 1 以 deterministic resolver 处理
   - 若无法无歧义归并，则返回 409，要求前端刷新后重试

### Important Boundary

note 不参与 resolver，reader note 重叠不影响高亮合并。

## Note Identity Resolver

note 不做 overlap merge，只做 exact-hit reopen。

### Rules

1. exact same quote
   - 打开已有 note 编辑
2. overlap but not exact
   - 允许创建新 note
3. edit existing note
   - 仅修改 `note_text`
4. change quote
   - 不支持原地修改
   - 必须 delete + recreate

## Web Reader Design

## 1. State Model

`ReaderWorkbench` 需拆出三类状态，而不是继续用“资产状态”混合管理。

### Highlight State

- current selection highlight hit
- active highlight color
- highlight list by record

### Note State

- notes grouped by sentence
- active focused note id
- active editing note id
- note rail expanded/collapsed state

### Ask State

- current attachments
- pending action proposals
- current citation/focus linkage

## 2. Selection Toolbar

`SelectionToolbar` 需要改成“动作入口”，不再承担 inline note editor。

### Keep

- Ask
- highlight color
- note
- lookup
- select current sentence

### Remove

- favorite
- 旧“保存后会直接绑定到原文锚点”的 inline note popover 心智

### Behavior

- 选中后点高亮：走高亮 resolver
- 选中后点笔记：
  - exact-hit note -> 打开现有 note 进入编辑
  - no hit -> 在句侧 rail 打开新 note composer
- toolbar 自身只负责触发，不负责承载 note 内容编辑

## 3. Note Rail

Web 使用 Plate comment 风格句侧 rail，但不采用 Plate 默认 comment data model。

### Rendering Model

- 每句一个 rail anchor slot
- rail 中显示该句 `note list`
- 支持折叠、展开、滚动
- 跨句 note 挂第一句 slot

### Card Variants

- whole sentence note
  - 仅显示 note 文本
- text range note
  - 显示 quote + note 文本
- multi text note
  - 显示压缩 quote + note 文本

### Focus Model

- 同一时刻仅一个 focused note
- focused note 将 quote 投影到正文为亮黄带下划线
- focus 切换时清空上一个投影

## 4. Reader Surface Projection

Reader surface 需要同时叠加三类 mark：

1. workflow inline marks
   - vocab / gloss / system analysis marks
2. user highlights
   - 三色高亮，黑字
3. focused note quote projection
   - 亮黄 + 下划线，仅 transient

渲染顺序需明确：

- workflow marks 是基础解释层
- user highlight 是持久个人标记层
- focused note projection 是 transient top overlay

## 5. Gutter / Marker

`AnnotationGutter` 从“资产指示器”改为“当前句是否有高亮/笔记”的轻量 marker。

### Marker Semantics

- 有高亮
- 有笔记
- 二者兼有

marker 点击行为：

- 若句子有 note，则展开该句 rail
- 不再跳到独立资产页

## Ask Claread Design

## 1. Context Model

Ask Claread 从“资产中心上下文”改为“当前文章上下文 + 显式引用上下文”。

### Allowed Inputs

- text selection attachment
- highlight reference
- note reference
- external record reference
- external stable analysis/supplement asset

### Removed Inputs

- historical excerpt asset lookup
- favorite text anchor attachment
- `record_excerpt_asset`
- `user_excerpt_asset`

## 2. Action Model

### Keep

- AI write highlight
- AI write note

### Remove

- text favorite action
- `save_answer_note`

### New Mapping

- `save_excerpt` -> create/update highlight annotation
- `save_note` -> create/update reader note by exact quote identity

如果 proposal 命中已有 note identity，执行结果应为“打开/更新已有 note”，不是再造第二条 note。

## 3. Trace / UI

`AiWorkspacePanel` 与相关 trace contract 必须去掉历史资产语义：

- 不展示 `history_lookup`
- 不展示 `record_excerpt_assets`
- 不展示 `has_user_assets`

保留：

- 当前文章引用
- 高亮引用
- note 引用
- external stable asset disambiguation

## Mini-Program Design

## 1. Phase 1 Positioning

小程序这轮只做新模型的查看适配，不做完整 note authoring。

### Keep

- 文章收藏
- 用户高亮显示
- note focused quote projection

### Remove

- 文本收藏
- 旧选区 note write flow
- 旧摘录页

## 2. Result Page

`pages/result/index.tsx` 需要：

- 保留高亮展示
- 改为请求新 `/reader-notes`
- 查看 note 时将对应 quote 投影回正文
- 删除旧 `showNoteSheet`、`selectionContext` 等旧状态路径

## 3. Notes UI

不实现 Web rail。

改为：

- 句子展开层 / bottom sheet 查看该句 notes
- 点击 note -> focused quote projection
- 不支持改 quote
- 如保留编辑，仅允许编辑已存在 note 文本；若当前小程序无法稳定支持，则 Phase 1 直接只读

## Cleanup Plan

## 1. Must Delete

### Product Surface

- Web `/library/assets`
- mini-program `packageA/excerpts`
- `/excerpt-assets`

### Backend Semantics

- `favorite_records` 文本锚点收藏
- `user_annotations.note`
- `annotation_type='note'`
- Ask 历史资产 lookup / action

### Frontend Semantics

- selection favorite state
- asset center route focus
- asset-bridge 命名与业务语义
-旧 `AnnotationSlip` 产品心智

## 2. Documentation Cleanup

以下文档需要在实施完成后同步清理或重写：

- `docs/development/mainline.md`
- `docs/product/current-state.md`
- `docs/product/ask-claread.md`
- `docs/architecture/ask-claread.md`
- `apps/web/docs/annotation-toolbar-text-range-plan.md`
- `apps/web/docs/reader-ia.md`

## Database / Migration Strategy

本轮不做旧数据兼容迁移，允许重置测试数据。

### Strategy

1. 新建 `reader_notes`
2. 通过 superseding migration 或 fresh baseline：
   - 收紧 `favorite_records` 语义
   - 收紧 `user_annotations` 语义
3. 删除依赖旧 `/excerpt-assets` 的 objects/tests/contracts

### Schema Constraints

- `reader_notes` 增加 `(user_id, analysis_record_id, target_key)` 唯一约束
- `user_annotations` 保留 `(user_id, target_key)` 唯一约束
- records 删除级联需覆盖 `reader_notes`

## Testing Strategy

## Backend

- `favorites`
  - 文章收藏 create/delete/list
  - 不再接受文本 target
- `user_annotations`
  - sentence / text_range / multi_text highlight create
  - exact/subset/superset resolver
  - no note semantics
- `reader_notes`
  - exact-hit reopen
  - overlap-but-not-exact create
  - patch note text only
  - delete and recreate
- `reader_ask`
  - highlight action writes `user_annotations`
  - note action writes `reader_notes`
  - no text favorite action
  - no history asset context

## Web

- toolbar action state
- note rail grouping / sorting / collapse
- single focused note projection
- current selection exact-hit note editing
- Ask attachment / action confirm under new model

## Mini-Program

- no excerpts page
- article favorite still works
- result page can read notes and project focused quote
- no old note-writing toolbar paths remain

## Rollout Notes

这是一次产品内核替换，不是增量能力发布。实施顺序应优先保证“删旧逻辑”与“新模型可闭环”同步落地，避免出现任一阶段代码库中同时存在：

- 旧资产中心语义
- 新双模型语义

建议按以下顺序实施：

1. 后端 schema / API / Ask contract 收口
2. Web Reader 新状态与 UI 落地
3. 小程序查看态收口
4. 删除旧页面、旧接口、旧测试、旧文档

## Risks And Mitigations

## 1. Ask Claread 改动面最大

风险：

- `reader_ask/service.py` 目前深度耦合 favorites / annotations / excerpt-assets

缓解：

- 先定义新的 attachment kinds 与 write action mapping
- 再逐条删除旧 asset-center 能力，而不是局部替换函数名

## 2. ReaderWorkbench 状态过重

风险：

- Web Reader 当前把 selection、annotation、favorite、ask、jump 混在一个 orchestration hub

缓解：

- 在实现阶段先抽 `highlights state`、`notes state`、`ask attachment state`
- 避免继续在同一状态块打补丁

## 3. Mini-Program 现有选区状态机残留

风险：

- 旧 toolbar / sheet / excerpt page 残留会造成双逻辑并行

缓解：

- 明确将“小程序 note write flow”视为删除范围，而不是临时隐藏

## Design Outcome

实施完成后，`analysis record Reader` 将只保留以下产品事实：

- 文章可收藏
- 文本可高亮
- 文本可写 note
- note 在 Reader 句侧或句级查看层中管理
- Ask Claread 可读写 highlight 与 note
- 不再存在“用户学习资产”作为独立产品面
