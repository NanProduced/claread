# Claread Console

> **状态**: `CURRENT` | **最后验证**: 2026-08-03（Architectural Cutover Complete；旧 Eval Center / Workflow Lab / Node Lab / Render Scene Inspector / Parse Run Observability module 已物理删除，按新 orchestration 重建属于 post-cutover backlog）

## 定位

`Claread Console` 是 Claread 的内部控制面，底层承载为 Directus Data Studio。

它负责：

- 数据观察与诊断
- 评测与实验控制面（按新 orchestration 重建属于 post-cutover backlog）
- few-shot / RAG 示例治理
- 轻量运维触发与审核

它不负责：

- 核心 workflow 执行
- 长任务调度
- judge / replay / batch eval 实际运行
- 向量写入与重型 ingestion

这些执行职责仍保留在 `services/api/` 与后续 worker。

## 当前已实现能力

Claread Console 在 cutover 后只保留通用 metadata 展示 module 和 LLM Config 控制面。旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已在 cutover 中物理删除，按新 orchestration 重建属于 post-cutover backlog。

### 通用 metadata 展示 module

cutover 后保留的 Directus custom module：

- `claread-enum-label-display` / `claread-enum-label-interface` — 枚举标签展示
- `claread-event-type-display` — 事件类型展示
- `claread-json-summary` / `claread-json-summary-interface` — JSON 摘要展示
- `claread-record-context-display` / `claread-record-context-interface` — 记录上下文展示
- `claread-relational-events-display` — 关联事件展示
- `claread-status-badge` — 状态徽章
- `claread-text-preview-interface` — 文本预览
- `claread-usage-summary` — usage 摘要

这些 module 服务数据观察与诊断，不承担核心执行面。

### LLM Config Control Plane

`LLM Config` 是 Directus 中的 LLM 配置 authoring 控制面，通过 6 个 collection 覆盖 provider / model / profile / preset / ask option / ask config 的 CRUD 管理。

它负责：

- LLM 配置的可视化 authoring 与生命周期管理（draft → active → deprecated）
- 配置 bundle 导出（与 services/api JSON schema 对齐）
- 导出前校验（与后端 Pydantic schema 规则一致）

它不负责：

- services/api 运行时直接读取（导出 bundle 需手动/脚本复制到 services/api/config/）
- 运行时模型选择逻辑
- API key 实际鉴权

6 个 collection（源码、metadata sync、当前 UI 可见状态区分如下）：

| Collection | 说明 | 源码保留 | metadata sync (`directus:llm-config:sync-metadata`) | 当前 UI 可见 |
|------------|------|----------|-----------------------------------------------------|--------------|
| `llm_providers` | 供应商连接配置（adapter / base_url / api_key_env） | ✅ | ✅ | ✅ |
| `llm_models` | 远端模型定义（FK → provider，model_name） | ✅ | ✅ | ✅ |
| `llm_profiles` | 场景级配置（FK → model，model_settings override） | ✅ | ✅ | ✅ |
| `llm_presets` | route→profile 映射集合（可选继承 base_preset） | ✅ | ✅ | ✅ |
| `llm_ask_options` | Ask Claread 用户可选模型档位 | ✅ | ✅ | ✅ |
| `llm_ask_config` | Ask Claread 全局运行档位（单条配置） | ✅ | ✅ | ✅ |

源码与脚本位置：collection 定义与 sync 逻辑在 `apps/directus/scripts/sync-llm-config-metadata.mjs`、`apps/directus/scripts/export-llm-config-bundle.mjs`、`apps/directus/scripts/import-llm-config-bundle.mjs`、`apps/directus/scripts/validate-llm-config-bundle.mjs`；UI module 在 `apps/directus/extensions/modules-bundle/src/claread-llm-config/`。数据源真源是 `services/api/config/` 下的 JSON 配置文件（`model-profiles.json`、`model-presets.json`、`reader-ask-model-options.json`），Directus authoring 后通过 `export-llm-config-bundle.mjs` 导出 bundle 再复制回 `services/api/config/`。

数据流：Directus authoring → `export-llm-config-bundle.mjs` → JSON bundle → `services/api/config/`

### reader-orch endpoints bundle

`apps/directus/extensions/endpoints-bundle/src/reader-orch/` 提供 Reader orchestration 相关的只读 endpoint，服务 Console 诊断与数据观察。当前源码入口为 `apps/directus/extensions/endpoints-bundle/src/reader-orch/index.js`，共 4 个只读 GET 路由（snake_case 路径参数）：

- `GET /reader-orch/trace/:trace_id`
- `GET /reader-orch/run/:run_id`
- `GET /reader-orch/record/:record_id/summary`
- `GET /reader-orch/dashboard`

四个路由都从 `reader_runtime_spans` 表读取数据，`run` / `record/:record_id/summary` / `dashboard` 额外 LEFT JOIN `ai_usage_events` 取 `billed_points` / `billing_policy_version`。所有路由要求登录（`accountability.user` 或 `admin`），否则返回 403。

**当前没有 Console heatmap / span-tree / trace 树可视化 UI 组件**。reader-orch 只提供 JSON API，未来按新 orchestration 重建 Console 诊断界面属于 post-cutover backlog。

### Example Lab（Directus Collection）

`Example Lab` 当前按 Directus 原生 Collection `eval_example_lab_entries` 实现，不在已删除的 `Eval Center` module 导航内。

它负责：

- few-shot example 的人工维护
- grammar RAG 辅助字段生成与复核

它不直接负责：

- 向量入库
- 线上 RAG promotion
- ingestion 状态编排

字段分层：

- **人工维护字段**：`sentence_text`、`output_fragment`（few-shot JSON，结构须与运行时 prompt 注入使用的 payload 合同兼容，合同见 `docs/architecture/reader-rag.md`）、`example_type`、`reading_variant`（硬过滤）、`quality_score`、`approved`（发布门槛）
- **自动同步派生**：`label`（自 `output_fragment.label`）、`target_node`（自 `example_type`）、`output_fragment.type`
- **Machine-derived RAG 字段**：`grammar_tags`、`retrieval_text`、`derived_at`、`derived_by`

派生与校验都由 `apps/directus/extensions/hooks-bundle/src/index.js` 的 validation hook 完成（`validateFragmentShape` / `syncLabelFromFragment` / `normalizeGrammarTagsField` / `extractGeneratedRagFields`）。Example Lab 当前只负责 PostgreSQL 内的 authoring、validation 和 derived-field 管理：条目不进入 Zilliz，保存条目不会触发 Zilliz rebuild，也不影响 Reader runtime。当前唯一入库路径是 `services/api/prompts/examples/grammar.yaml` → `generate_grammar_seed.py` → `data/seed/grammar_seed_v1.jsonl` → `ingest_grammar_seed.py` → Zilliz → Reader runtime retrieval。Directus → seed → Zilliz 的 promotion 链路尚未实现，属于未来能力。

`output_fragment` 契约、retrieval_text 格式、grammar_tags 归一化、Zilliz schema 和联动更新清单等运行时契约见 `docs/architecture/reader-rag.md`。

## Example Lab / Grammar RAG 当前契约

### Authoring 字段

Example Lab 中人工维护的字段是：

- `sentence_text`
- `output_fragment`

其中：

- `grammar` 固定写成 `grammar_note`
- `sentence_analysis` 固定写成 `sentence_analysis`
- 外层 `label` 从 `output_fragment.label` 自动同步

### Variant 边界

`reading_variant` 是 grammar RAG 的硬边界。

当前规则：

- 只在同 `variant` 池内检索
- 不回退到 `default`
- 无结果或低置信度时直接走非 RAG fallback

### Derived 字段

当前 machine-derived 字段为：

- `grammar_tags`
- `retrieval_text`
- `derived_at`
- `derived_by`

当前已移除旧字段：

- `teaching_goal`
- `structure_signals`
- Example Lab / Zilliz 路径中的 `grammar_granularity`
- `retrieval_version`

### retrieval_text

`retrieval_text` 是 grammar example 的 embedding 主文本，使用稳定的 colon 格式（完整契约与 query 侧格式见 `docs/architecture/reader-rag.md`）：

```text
variant: ...
output_type: ...
grammar_tags: ...
label: ...
source_sentence: ...
explanation: ...
```

其中 `explanation` 来自：

- `grammar_note.note_zh`
- `sentence_analysis.analysis_zh`

## Directus 与后端分工

Directus / Claread Console 负责：

- 可视化管理样本、草稿、审核与控制面状态
- 承载通用 metadata 展示与 LLM Config authoring
- Console / Eval 按新 orchestration 重建属于 post-cutover backlog

后端 / worker 负责：

- 实际 Reader orchestration 运行
- model invocation
- RAG 检索
- judge / replay / batch eval（重建后）
- Zilliz collection 重建与 ingestion

### 数据分层

| 层 | 表前缀 | 说明 |
|----|--------|------|
| 业务层 | 无前缀 | 现有业务表（Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events` 等），Directus 默认只读 |
| 控制层 | `eval_example_lab_entries` | Example Lab Collection（Directus 可读写）；旧 `eval_node_lab_*` / `eval_workflow_*` 控制面表已退出 baseline schema，残留本地库清理属于 post-cutover 数据清理 backlog |
| 配置层 | `llm_*` | LLM Config 控制面表，Directus 可读写 |
| 系统层 | `directus_*` | Directus 系统表，不手动干预 |

### 固定约束

- 平台只展示 prompt version，不承接 prompt 在线编辑
- 单一 admin 账号，不做多角色权限管理
- LangSmith 只负责 trace / run inspection，不替代自建 eval
- 详细 trace 行为见 `docs/operations/langsmith.md`。

## Post-cutover backlog

以下事项属于 post-cutover backlog，不在本文写成已完成：

- Console / Eval 按新 orchestration 重建（治理化控制面）
- 旧 Eval 控制面表与 `analysis_*` 残留本地库清理（这些表已不在 baseline schema 中）
- 统一监测与计费适配

## 当前事实源

与 Claread Console 当前状态最相关的正式文档：

- `docs/operations/directus-local-dev.md` — 本地开发与热更新
- `docs/operations/testing.md` — 测试与验证
- `docs/product/current-state.md` — 产品当前状态
docs/README.md

## 历史能力（已物理删除，仅供回看）

docs/architecture/workflow-history.md + docs/development/mainline.md + docs/product/current-state.md

- Parse Run Observability（原 `analysis_records` 观察台）
- Render Scene Inspector（原 `analysis_results.render_scene_json` 检查器）
- Eval Center / Node Lab / Workflow Lab / Run History（原 eval 实验控制面）

这些 module 依赖的旧 `analysis_*` 数据层、`render_scene_json` 事实源、旧 `/analyze` workflow 已在 cutover 中物理删除。
