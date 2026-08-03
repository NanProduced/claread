# Eval Center / Example Lab / Grammar RAG 联动说明（历史文档）

> **状态**: `HISTORICAL` | **最后验证**: 2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：Architectural Cutover Complete；旧 Eval Center / Node Lab / Workflow Lab / Run History / Parse Run Observability / Render Scene Inspector module 已物理删除。本文档保留作历史证据，不再代表当前控制面状态。当前控制面状态见 `docs/architecture/directus-console.md`。）

本文档说明 cutover 前 Directus / Eval Center / Example Lab / services/api / PostgreSQL / Zilliz 之间的耦合关系与联动更新点。cutover 后旧 Eval Center module 已物理删除，按新 orchestration 重建属于 post-cutover backlog；Example Lab 作为 Directus Collection 保留。当前控制面状态以 `docs/architecture/directus-console.md` 为准。

> LangSmith trace 行为（每条子路径是否写、`trace_scope` 取值、`surface` tag 约定）以 `docs/operations/langsmith.md` 为准，本文档不重复维护。简而言之：eval-center 默认 `trace_scope="off"` 不写 trace；唯一可被显式 `inherit` 进入主 LangSmith project 的入口是 Workflow Lab compare（它复用 `/analyze` 主链）。

## 1. 控制面模块与依赖

### Eval Center learning-only 边界

Eval Center 当前 v1 是 learning-only，所有 eval adapter 入口（article-analysis eval workflow `/eval/article-analysis/workflow`、node-lab、node-probe、workflow-lab baseline bundle）均在 Pydantic schema 层拒绝 `reading_goal="academic"`，返回 422。Directus endpoint 侧同样拒绝 academic 请求。

academic graph 在后端主 workflow `/analyze` 中继续保留，服务主产品和未来独立 academic lab 的可能性，但不属于当前 eval-center 公开评测面。这是有意的产品边界，不是偶然缺失。

| 模块 | Directus 承载 | Directus endpoint / hook | PostgreSQL 表 | runtime artifact | Claread API / 其他后端依赖 |
|------|--------------|--------------------------|---------------|-----------------|-----------------------------|
| Parse Run Observability | 原生 collection / views / panels | `sync-parse-run-observability-metadata.mjs` 同步 metadata；少量只读 endpoint | `analysis_records` 等业务表 | — | 业务数据由 `services/api` workflow 写入 |
| Render Scene Inspector | custom module | Inspector module + 只读数据装配 | 业务表（只读） | — | 读取 `analysis_results.render_scene_json`、`analysis_debug_snapshots` 等后端产物 |
| Eval Center / Node Lab | custom module | `apps/directus/extensions/endpoints-bundle/src/eval-center/node-lab.js` | `eval_node_lab_*` 控制面表 | `apps/directus/.runtime/evals/node-lab/` | 运行时依赖 Claread API workflow / model / judge 链路 |
| Eval Center / Workflow Lab | custom module | `apps/directus/extensions/endpoints-bundle/src/eval-center/index.js` | `eval_workflow_*` 与 `eval_review_notes` | `apps/directus/.runtime/evals/workflow-compares/` | 运行时依赖 Claread API workflow / compare / judge 链路 |
| Eval Center / Run History | custom module | 同上 | 同上 | 同上 | 同上 |
| Example Lab | 原生 Collection + 自定义 interface | `example-lab.js` + `hooks-bundle/src/index.js` | `eval_example_lab_entries` | — | `services/api/app/eval_adapter/example_lab.py`、grammar RAG、Zilliz / seed / ingest |

### Example Lab 字段分层

**人工维护真源**：

| 字段 | 说明 |
|------|------|
| `sentence_text` | 英文原句 |
| `output_fragment` | few-shot JSON（唯一 prompt 注入真源） |
| `example_type` | grammar / sentence_analysis / vocab / phrase / context / translation |
| `reading_variant` | 硬过滤字段 |
| `quality_score` | curator 评分 |
| `approved` | 发布门槛 |

**自动同步派生字段**：

| 字段 | 来源 | 同步位置 |
|------|------|----------|
| `label` | `output_fragment.label` | Directus hook |
| `target_node` | `example_type` | Directus hook |
| `output_fragment.type` | `example_type` | Directus hook |

**Machine-derived RAG 字段**：

| 字段 | 生成方 | 说明 |
|------|--------|------|
| `grammar_tags` | LLM / rule engine | 开放词表，归一化后落库 |
| `retrieval_text` | LLM / rule engine | embedding 主文本，canonical colon 格式 |
| `derived_at` | Directus hook | 派生时间戳 |
| `derived_by` | LLM / rule engine | 派生来源标识 |

**已移除字段**：`teaching_goal`、`structure_signals`、`grammar_granularity`、`retrieval_version`、`rag_eligible`

## 2. output_fragment 契约

`output_fragment` 是 few-shot 注入 prompt 的唯一真源。Directus 中保存的 JSON、API schema 校验的结构、进入 prompt 注入的结构必须同构。

### grammar_note

```json
{
  "type": "grammar_note",
  "spans": [{ "text": "Not only" }],
  "label": "not only...but also 倒装结构",
  "note_zh": "Not only 放在句首时触发部分倒装..."
}
```

约束：`type` 固定 `grammar_note`；`label` 非空；`note_zh` 非空；`spans` 可为空数组，若存在则每项必须有 string `text`。

### sentence_analysis

```json
{
  "type": "sentence_analysis",
  "label": "过去分词后置定语 + 宾语从句",
  "analysis_zh": "主干是 ...",
  "chunks": [{ "order": 1, "label": "主干主语", "text": "The research" }]
}
```

约束：`type` 固定 `sentence_analysis`；`label` 非空；`analysis_zh` 非空；`chunks` 可为空数组，若存在则每项必须有 int `order`、string `label`、string `text`。

### phrase_gloss

```json
{
  "type": "phrase_gloss",
  "text": "turn ... into",
  "spans": [{ "text": "turn" }, { "text": "into" }],
  "phrase_type": "phrasal_verb",
  "zh": "把……变成……"
}
```

约束：

- `type` 固定 `phrase_gloss`
- `text` 是短语标题 / `lookup_text` / 教学短语名，不再要求它本身必须是原文连续子串
- `spans` 是前端原文高亮证据；推荐提供 1-4 个 span。连续短语通常 1 个 span，不连续短语通常 2-4 个 spans
- 若存在 `spans`，则每项必须有 string `text`，且 `span.text` 必须逐字复制原句中的连续真实片段
- `phrase_type`、`zh` 为必填

### 校验位置

| 校验层 | 文件 | 校验内容 |
|--------|------|----------|
| Directus hook | `apps/directus/extensions/hooks-bundle/src/index.js` | `validateFragmentShape()` — type 匹配、必填字段、spans/chunks 元素结构 |
| API schema | `services/api/app/eval_adapter/schemas.py` | `_validate_output_fragment_structure` — grammar example 结构校验，拒绝未知 type |
| 审计脚本 | `services/api/scripts/audit_grammar_examples.py` | example_type ↔ fragment.type 一致性、必填字段、label 同步 |

## 3. retrieval_text 格式

Example 侧（6 行）：

```text
variant: <variant>
output_type: <type>
grammar_tags: <comma-separated tags>
label: <label>
source_sentence: <sentence>
explanation: <note_zh 或 analysis_zh>
```

Query 侧（4 行，不含 label/explanation）：

```text
variant: <variant>
output_type: <type>
grammar_tags: <comma-separated tags>
source_sentence: <sentence>
```

`explanation` 来源：`grammar_note` → `note_zh`，`sentence_analysis` → `analysis_zh`。

## 4. grammar_tags 归一化

开放词表，不做闭枚举限制。归一化规则：

1. trim + lowercase + 连字符/空格 → `_`
2. alias merge（两侧各自维护，需保持一致）
3. 去重
4. 拒绝泛词（`general`、`complex`、`other`、`misc`）

### alias merge 对照

| 别名 | 归一化到 |
|------|----------|
| `defining_relative_clause` | `restrictive_relative_clause` |
| `limiting_relative_clause` | `restrictive_relative_clause` |
| `non_defining_relative_clause` | `nonrestrictive_relative_clause` |
| `non-defining_relative_clause` | `nonrestrictive_relative_clause` |
| `participle_adverbial` | `past_participle_adverbial` |
| `participle_attribute` | `past_participle_attribute` |
| `fronting` | `subject_clause_fronting` |

注意：`relative_clause` 保留为 generic tag，不强制归到 restrictive/nonrestrictive。

### 归一化实现位置

| 位置 | 文件 |
|------|------|
| Directus hook | `hooks-bundle/src/index.js` — `normalizeGrammarTag()` / `normalizeGrammarTagsField()` |
| services/api | `eval_adapter/example_lab.py` — `_normalize_tag()` / `normalize_grammar_tags()` |

两侧规则必须保持一致。改一侧时必须同步另一侧。

## 5. Zilliz schema

当前字段（11 个）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `example_id` | VARCHAR(128) | PK |
| `vector` | FLOAT_VECTOR | embedding 向量 |
| `reading_variant` | VARCHAR(64) | 硬过滤 |
| `output_type` | VARCHAR(32) | 硬过滤 |
| `grammar_tags` | VARCHAR(512) | JSON 数组字符串 |
| `label` | VARCHAR(256) | 展示/检索辅助 |
| `source_sentence` | VARCHAR(2048) | 原句 |
| `output_fragment` | VARCHAR(8192) | few-shot JSON |
| `retrieval_text` | VARCHAR(4096) | embedding 主文本 |
| `quality_score` | FLOAT | 排序辅助 |
| `approved` | BOOL | 准入过滤 |

embedding 来源：`retrieval_text` 字段。

注意：

- Example Lab / grammar RAG 路径中的 `grammar_granularity` 已删除。
- planner / prompt strategy 层自己的 `grammar_granularity` 仍然存在，不属于这条 Example Lab / Zilliz 契约。

## 6. 数据流

### Authoring → PostgreSQL → Zilliz

```
Directus Example Lab (authoring)
  ↓ hook: validateFragmentShape + syncLabel + syncTargetNode + normalizeGrammarTags + extractGeneratedRagFields
PostgreSQL eval_example_lab_entries
  ↓ ingest_grammar_seed.py / 后续 promotion workflow
Zilliz collection (grammar_note_examples / sentence_analysis_examples)
```

### Seed → Zilliz

```
prompts/examples/grammar.yaml
  ↓ generate_grammar_seed.py
data/seed/grammar_seed_v1.jsonl
  ↓ ingest_grammar_seed.py (embedding on retrieval_text)
Zilliz collection
```

### Runtime RAG 检索

```
输入句子
  ↓ extract_grammar_tags_from_sentence (英文结构信号 → query_tags)
  ↓ build_query_text (canonical colon 格式)
  ↓ embed_single_with_metadata
  ↓ Zilliz ANN (filter: approved + variant + output_type)
  ↓ 从 output_fragment 提取 explanation 构建 rerank doc
  ↓ rerank_with_metadata
  ↓ tag boost + quality boost + diversity dedup
  ↓ INJECTION_BUDGET 裁剪
prompt 注入 (sentence_text + output_fragment)
```

## 7. 联动更新清单

### 改 output_fragment 契约时

| 必须改 | 文件 |
|--------|------|
| Directus hook 校验 | `apps/directus/extensions/hooks-bundle/src/index.js` — `validateFragmentShape()` |
| API schema 校验 | `services/api/app/eval_adapter/schemas.py` — `_validate_output_fragment_structure` |
| 审计脚本 | `services/api/scripts/audit_grammar_examples.py` |
| prompt 注入 | `services/api/app/services/analysis/prompting/prompt_composer.py` 与 `example_strategy.py` |
| seed 数据 | `services/api/prompts/examples/grammar.yaml` → `services/api/scripts/generate_grammar_seed.py` → `services/api/data/seed/grammar_seed_v1.jsonl` |
| RAG rerank doc 构造 | `services/api/app/services/analysis/prompting/rag/grammar_rag_service.py` — `_retrieve_from_backend` 中 explanation 提取 |
| retrieval_text 生成 | `services/api/app/eval_adapter/example_lab.py` — `_extract_explanation()` / `_build_retrieval_text()` |

### 改 grammar_tags 归一化规则时

| 必须改 | 文件 |
|--------|------|
| Directus hook 归一化 | `apps/directus/extensions/hooks-bundle/src/index.js` — `GRAMMAR_TAG_ALIASES` / `normalizeGrammarTag()` |
| API 侧归一化 | `services/api/app/eval_adapter/example_lab.py` — `_TAG_ALIASES` / `_normalize_tag()` |
| query 侧 tag 提取 | `services/api/app/services/analysis/prompting/rag/grammar_retrieval_hints.py` — `extract_grammar_tags_from_sentence()` |
| seed 生成 | `services/api/scripts/generate_grammar_seed.py` — `extract_grammar_tags()` |
| 推荐标签列表 | `services/api/app/eval_adapter/example_lab.py` — `RECOMMENDED_GRAMMAR_TAGS` |

两侧 alias merge 规则必须同步。

### 改 retrieval_text 格式时

| 必须改 | 文件 |
|--------|------|
| Example 侧生成 | `services/api/app/eval_adapter/example_lab.py` — `_build_retrieval_text()` / `_validate_retrieval_text()` |
| Query 侧生成 | `services/api/app/services/analysis/prompting/rag/grammar_retrieval_hints.py` — `build_query_text()` |
| Seed 生成 | `services/api/scripts/generate_grammar_seed.py` — `build_retrieval_text()` |
| RAG rerank doc | `services/api/app/services/analysis/prompting/rag/grammar_rag_service.py` — rerank doc 构造 |
| Zilliz collection | 需重建（embedding 变化） |

### 改 Zilliz schema 时

| 必须改 | 文件 |
|--------|------|
| schema 定义 | `services/api/app/infra/zilliz_client.py` — `zilliz_create_collection()` |
| ingest 映射 | `services/api/scripts/ingest_grammar_seed.py` — `_map_record_to_zilliz()` |
| RAG 查询 output_fields | `services/api/app/services/analysis/prompting/rag/grammar_rag_service.py` — `_retrieve_from_backend()` |
| schema 测试 | `services/api/tests/test_rag_infra.py` — `TestZillizSchemaContract` |

改后必须重建 Zilliz collection 并重新 ingest。

### 改 PostgreSQL / Directus Collection 字段时

| 必须改 | 文件 |
|--------|------|
| SQL migration | `infra/migrations/eval-center/` 新增 migration |
| Directus hook | `apps/directus/extensions/hooks-bundle/src/index.js` |
| metadata sync | `apps/directus/scripts/sync-eval-center-metadata.mjs` — `EXAMPLE_LAB_FIELD_METADATA` |
| API schema | `services/api/app/eval_adapter/schemas.py` |
| API 生成逻辑 | `services/api/app/eval_adapter/example_lab.py` |

改后必须执行 migration + `pnpm directus:eval-center:sync-metadata`。

### 改 workflow compare / node-lab / run-history / judge 契约时

| 必须改 | 文件 |
|--------|------|
| Directus endpoint | `apps/directus/extensions/endpoints-bundle/src/eval-center/index.js` / `node-lab.js` |
| Directus module | `apps/directus/extensions/modules-bundle/src/claread-eval-center/` |
| runtime artifact 格式 | `apps/directus/.runtime/evals/` 对应子目录 |
| PostgreSQL 表 | `infra/migrations/eval-center/` 新增 migration |
| metadata sync | `apps/directus/scripts/sync-eval-center-metadata.mjs` |

## 8. 最小回归建议

### pytest

```powershell
# Example Lab / grammar RAG 核心测试
uv run pytest services/api/tests/test_example_lab.py -q
uv run pytest services/api/tests/test_grammar_retrieval_hints.py -q
uv run pytest services/api/tests/test_rag_infra.py -q
uv run pytest services/api/tests/test_rag_integration.py -q
uv run pytest services/api/tests/test_rag_readiness.py -q
```

### Directus build / test / metadata sync

```powershell
pnpm directus:extensions:build
pnpm --filter @claread/directus-endpoints test
pnpm directus:eval-center:sync-metadata
```

### 人工 smoke

- Example Lab 条目可保存，`output_fragment` 契约校验有效
- Example Lab AI RAG Generator 可生成 `grammar_tags` / `retrieval_text` / `derived_*`
- Eval Center 的 `node-lab` / `workflow-lab` / `run-history` 可进入
- Render Scene Inspector 能读取 learning / academic 样本
- grammar RAG 变更后：重建 Zilliz collection → 重新 ingest → workflow 侧 RAG smoke
