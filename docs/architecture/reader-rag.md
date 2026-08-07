# Reader RAG（Grammar few-shot RAG）运行时契约

> **状态**: `CURRENT` | **最后验证**: 2026-08-08（旧 eval-center 集成图相关运行时已按当前代码重核：API 侧 `eval_adapter` 已删除，校验与派生字段生成收口到 Directus hook、seed 脚本与审计脚本）

本文记录 Reader 主链 grammar few-shot RAG 的当前运行时契约：运行时材料来源、检索结果进入 prompt 的 payload 结构、检索文本与标签归一化，以及改动契约时必须联动更新的位置。Example Lab 的 Directus authoring 控制面定位见 `docs/architecture/directus-console.md`。

## 数据流

### Authoring 面（未接入 Zilliz）

```text
Directus Example Lab (authoring, eval_example_lab_entries)
  ↓ hooks-bundle: validateFragmentShape + syncLabelFromFragment
     + normalizeGrammarTagsField + extractGeneratedRagFields
PostgreSQL eval_example_lab_entries（仅 authoring / 校验 / 派生字段复核）
```

Example Lab 当前是 few-shot example 的 authoring、validation、derived-field 管理界面；条目只保存在 PostgreSQL，**当前不进入 Zilliz**。Directus → seed → Zilliz 的导出 / promotion / 发布链路尚未实现，是未来能力。

### Seed → Zilliz（当前唯一可执行入库路径）

```text
services/api/prompts/examples/grammar.yaml
  ↓ services/api/scripts/generate_grammar_seed.py（extract_grammar_tags + build_retrieval_text）
services/api/data/seed/grammar_seed_v1.jsonl
  ↓ services/api/scripts/ingest_grammar_seed.py（embedding on retrieval_text）
Zilliz collection
```

### 运行时检索

```text
输入句子
  ↓ extract_grammar_tags_from_sentence（grammar_retrieval_hints.py，英文结构信号 → query_tags）
  ↓ build_query_text（canonical colon 格式）
  ↓ embedding（RAG embedding route）
  ↓ Zilliz ANN（filter: approved + 同 variant + output_type）
  ↓ 从 output_fragment 提取 explanation 构建 rerank doc
  ↓ rerank（tag boost + quality boost + diversity dedup）
  ↓ INJECTION_BUDGET 裁剪（grammar_rag_service.py，默认每类 2 条）
prompt 注入（sentence_text + output_fragment），经 strategy_builder（`query_grammar_rag`）→ example_strategy → prompt_composer 进入 Reader worker prompt
```

`reading_variant` 是硬边界：只在同 variant 池内检索，不回退 `default`；无结果或低置信度时走非 RAG fallback。

## output_fragment 契约

`output_fragment` 是 few-shot 检索结果进入 prompt 时使用的规范字段与 payload 结构：Zilliz 中保存的 JSON、各层 schema 校验的结构、进入 prompt 注入的结构必须同构。它是数据结构合同，不表示 Directus 是当前运行时数据源——当前进入 Zilliz 的材料只来自上方 seed 链路。

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
- `text` 是短语标题 / 教学短语名，不要求本身是原文连续子串
- `spans` 是前端原文高亮证据；推荐 1-4 个 span，且 `span.text` 必须逐字复制原句中的连续真实片段
- `phrase_type`、`zh` 为必填

### 校验位置

| 校验层 | 位置 | 校验内容 |
|--------|------|----------|
| Directus hook | `apps/directus/extensions/hooks-bundle/src/index.js` — `validateFragmentShape()` | type 匹配、必填字段、spans/chunks 元素结构 |
| 审计脚本 | `services/api/scripts/audit_grammar_examples.py` | example_type ↔ fragment.type 一致性、必填字段、label 同步 |

旧 API 侧 `eval_adapter` 校验已随 eval adapter 物理删除；当前权威校验是 hook + 审计脚本。

## retrieval_text 格式

`retrieval_text` 是 embedding 主文本，使用稳定的 colon 格式。

Example 侧（6 行，seed 由 `generate_grammar_seed.py` 的 `build_retrieval_text` 生成，Directus 条目由 hook `extractGeneratedRagFields` 生成）：

```text
variant: <variant>
output_type: <type>
grammar_tags: <comma-separated tags>
label: <label>
source_sentence: <sentence>
explanation: <note_zh 或 analysis_zh>
```

Query 侧（4 行，不含 label/explanation，由 `grammar_retrieval_hints.py` 的 `build_query_text` 生成）：

```text
variant: <variant>
output_type: <type>
grammar_tags: <comma-separated tags>
source_sentence: <sentence>
```

`explanation` 来源：`grammar_note` → `note_zh`，`sentence_analysis` → `analysis_zh`。

## grammar_tags 归一化

开放词表，不做闭枚举限制。归一化规则：

1. trim + lowercase + 连字符/空格 → `_`
2. alias merge（Directus hook 与 services/api 两侧各自维护，必须保持一致）
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
| Directus hook | `apps/directus/extensions/hooks-bundle/src/index.js` — `normalizeGrammarTag()` / `normalizeGrammarTagsField()` |
| services/api | `services/api/app/services/prompting/rag/grammar_tag_normalization.py` — `_TAG_ALIASES` / `_normalize_tag()` / `normalize_grammar_tags()` |

两侧规则必须保持一致。改一侧时必须同步另一侧。

## Zilliz schema

当前字段（11 个，定义在 `services/api/app/infra/zilliz_client.py`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `example_id` | VARCHAR | PK |
| `vector` | FLOAT_VECTOR | embedding 向量（AUTOINDEX，COSINE） |
| `reading_variant` | VARCHAR | 硬过滤 |
| `output_type` | VARCHAR | 硬过滤 |
| `grammar_tags` | VARCHAR | JSON 数组字符串 |
| `label` | VARCHAR | 展示/检索辅助 |
| `source_sentence` | VARCHAR | 原句 |
| `output_fragment` | VARCHAR | few-shot JSON |
| `retrieval_text` | VARCHAR | embedding 主文本 |
| `quality_score` | FLOAT | 排序辅助 |
| `approved` | BOOL | 准入过滤 |

注意：Example Lab / grammar RAG 路径中的 `grammar_granularity` 已删除；planner / prompt strategy 层自己的 `grammar_granularity` 不属于这条契约。

## 联动更新清单

### 改 output_fragment 契约时

| 必须改 | 位置 |
|--------|------|
| Directus hook 校验 | `apps/directus/extensions/hooks-bundle/src/index.js` — `validateFragmentShape()` |
| 审计脚本 | `services/api/scripts/audit_grammar_examples.py` |
| prompt 注入 | `services/api/app/services/prompting/strategy_builder.py`（`query_grammar_rag` 调用点）、`prompt_composer.py` 与 `example_strategy.py` |
| seed 数据 | `services/api/prompts/examples/grammar.yaml` → `generate_grammar_seed.py` → `data/seed/grammar_seed_v1.jsonl` |
| RAG rerank doc 构造 | `services/api/app/services/prompting/rag/grammar_rag_service.py` — `_retrieve_from_backend` 中 explanation 提取 |
| retrieval_text 生成 | Directus hook `extractGeneratedRagFields` 与 `generate_grammar_seed.py` — `build_retrieval_text()` |

### 改 grammar_tags 归一化规则时

| 必须改 | 位置 |
|--------|------|
| Directus hook 归一化 | `hooks-bundle/src/index.js` — `GRAMMAR_TAG_ALIASES` / `normalizeGrammarTag()` |
| API 侧归一化 | `grammar_tag_normalization.py` — `_TAG_ALIASES` / `_normalize_tag()` |
| query 侧 tag 提取 | `grammar_retrieval_hints.py` — `extract_grammar_tags_from_sentence()` |
| seed 生成 | `generate_grammar_seed.py` — `extract_grammar_tags()` |

两侧 alias merge 规则必须同步。

### 改 retrieval_text 格式时

| 必须改 | 位置 |
|--------|------|
| Example 侧生成 | Directus hook `extractGeneratedRagFields` |
| Query 侧生成 | `grammar_retrieval_hints.py` — `build_query_text()` |
| Seed 生成 | `generate_grammar_seed.py` — `build_retrieval_text()` |
| RAG rerank doc | `grammar_rag_service.py` — rerank doc 构造 |
| Zilliz collection | 需重建（embedding 变化） |

### 改 Zilliz schema 时

| 必须改 | 位置 |
|--------|------|
| schema 定义 | `services/api/app/infra/zilliz_client.py` — `zilliz_create_collection()` |
| ingest 映射 | `services/api/scripts/ingest_grammar_seed.py` |
| RAG 查询 output_fields | `grammar_rag_service.py` — `_retrieve_from_backend()` |
| schema 测试 | `services/api/tests/test_rag_infra.py` |

改后必须重建 Zilliz collection 并重新 ingest。

## 最小回归建议

以下 pytest 命令需在 `services/api/` 目录下运行（仓库根没有 Python project 配置）：

```powershell
# cwd: services/api
uv run pytest tests/test_grammar_retrieval_hints.py -q
uv run pytest tests/test_rag_infra.py -q
uv run pytest tests/test_rag_integration.py -q
uv run pytest tests/test_rag_readiness.py -q
```

```powershell
# cwd: 仓库根
pnpm directus:extensions:build
```

人工 smoke（两个独立检查，互不代表对方通过）：

- Example Lab authoring 面：条目可保存，`output_fragment` 契约校验有效，派生字段（`grammar_tags` / `retrieval_text` / `derived_*`）按 hook 正确生成。该检查只覆盖 PostgreSQL 内 authoring / validation / derived-field 管理，不触发、也不证明任何 Zilliz 或 Reader runtime 行为。
- seed / Zilliz runtime 面：grammar seed 或 RAG 契约变更后，重建 Zilliz collection → 重新 ingest → Reader 侧 RAG smoke。该检查的输入只来自 `prompts/examples/grammar.yaml` seed 链路，与 Directus 条目无关。
