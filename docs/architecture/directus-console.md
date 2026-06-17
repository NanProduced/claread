# Claread Console

> **状态**: `CURRENT` | **最后验证**: 2026-06-06

## 定位

`Claread Console` 是 Claread 的内部控制面，底层承载为 Directus Data Studio。

它负责：

- 数据观察与诊断
- 评测与实验控制面
- few-shot / RAG 示例治理
- 轻量运维触发与审核

它不负责：

- 核心 workflow 执行
- 长任务调度
- judge / replay / batch eval 实际运行
- 向量写入与重型 ingestion

这些执行职责仍保留在 `services/api/` 与后续 worker。

## 当前已实现能力

Claread Console 已进入可用控制面阶段，当前承载以下能力：

### Parse Run Observability

第一阶段已落地，当前以 `analysis_records` 为主入口，按业务表、字段、关系、saved views 和 dashboard panels 组织解析链路观察。

当前正式方向：

- 直接读取现有业务表
- 优先使用 Directus 原生 collections / relations / displays / panels
- 仅在原生内容页不够承载时补最小自定义

自定义只读 endpoint（仅用于跨表聚合和 dashboard 汇总）：

- `GET /parse-run-observability/recent-failures?limit=5`
- `GET /parse-run-observability/summary?days=7`

第二阶段可补充的关联表：`user_annotations`、`reader_notes`、`reader_ask_threads`、`reader_ask_supplements`、`feedback`。

JSON 展示策略：详情页直接展示原始 JSON → 用 Displays 做局部摘要 → 必要时补标量字段或只读 endpoint。

### Render Scene Inspector

`Render Scene Inspector` 已从"详情页内附属面板"演进为独立的 Directus custom module。

它面向单次解析结果的深度诊断，重点服务：

- render scene 结构核查
- RAG 检索调试
- 调用成本与降级分析
- learning / academic 双模式结果检查

当前实现口径：

- 采用独立 module，而不是继续堆在 `analysis_results` 字段详情页
- RAG 检索拆为专用调试面板，命中样例正文优先显示
- `few_shot_debug_json` 已移除，固定 baseline few-shot 不再单独持久化
- `analysis_debug_snapshots.rag_debug_json` 存详细检索快照（selected_examples 存命中样例正文、ann_hits/rerank_hits/dropped_examples 存紧凑链路证据）

academic policy 下 `content_summary = null` 不能直接判为失败。

### Eval Center

`Eval Center` 已形成可用基线，采用 `module-first` 承载。

当前用户可见 mode：

- `node-lab`
- `workflow-lab`
- `run-history`

当前边界：

- `workflow-lab` 已收口为 compare-only 主链
- Workflow 公开历史对象只保留 `workflow_compare`
- judge / review 继续锚定 compare 或 trial，不把 Directus 变成执行面

#### Node Lab 边界

- Judge 已并回 Node Lab 主路径，不再是独立 mode
- Baseline Compare 是主实验入口
- Single Run 不进入 Session，也不持久化到 Run History
- **learning-only**：node-lab v1 只支持 learning topology，所有入口（baseline config / run / compare / judge）均拒绝 `reading_goal="academic"`

#### Workflow Lab 边界

- 主链：候选版本 → 双跑单篇 compare → compare-level judge → compare-level review
- 单篇主路径以双跑 compare 为核心，前端只消费 persisted compare
- `compare_id` 每次铸新值 `workflow-compare-{ts}-{rand}`，不按 baseline/candidate run pair 复用
- `experiment_fingerprint`（16hex）是跨实验归类 key，不可用于去重
- Compare review note 固定锚定 `target_type='workflow_compare'`
- 底层 workflow run artifact 仍存在于 `workflow-runs/`，但仅作为 compare 私有依赖
- Dataset endpoint `/workflow-runs/datasets*` 暂时保留但已退出主路径
- 后端 `/workflow-lab/run-history/single-run` 仍保留单篇 run artifact 持久化能力，但不是用户可见主工作区
- **learning-only**：workflow-lab v1 只支持 learning topology，baseline bundle 和 single run 均拒绝 `reading_goal="academic"`

#### Eval Center 整体 learning-only 边界

Eval Center 当前 v1 是 learning-only，所有 eval adapter 入口（article-analysis eval workflow、node-lab、node-probe、workflow-lab）均在 schema 层拒绝 `reading_goal="academic"`。academic graph 在后端主 workflow `/analyze` 中继续保留，服务主产品和未来独立 academic lab 的可能性，但不属于当前 eval-center 公开评测面。这是有意的产品边界，不是偶然缺失。

#### Run History 边界

- 定位是统一只读回看，不是统一实验操作台
- Workflow 侧只按 `workflow_compare` 过滤
- Node Lab single run 不进入统一历史页

### Example Lab

`Example Lab` 当前按 Directus 原生 Collection `eval_example_lab_entries` 实现，不在 `Eval Center` module 导航内。

它负责：

- few-shot example 的人工维护
- grammar RAG 辅助字段生成与复核

它不直接负责：

- 向量入库
- 线上 RAG promotion
- ingestion 状态编排

### LLM Config Control Plane

`LLM Config` 是 Directus 中的 LLM 配置 authoring 控制面，通过 5 个 collection 覆盖 provider / model / profile / preset / ask option 的 CRUD 管理。

它负责：

- LLM 配置的可视化 authoring 与生命周期管理（draft → active → deprecated）
- 配置 bundle 导出（与 services/api JSON schema 对齐）
- 导出前校验（与后端 Pydantic schema 规则一致）

它不负责：

- services/api 运行时直接读取（导出 bundle 需手动/脚本复制到 services/api/config/）
- 运行时模型选择逻辑
- API key 实际鉴权

5 个 collection：

| Collection | 说明 |
|------------|------|
| `llm_providers` | 供应商连接配置（adapter / base_url / api_key_env） |
| `llm_models` | 远端模型定义（FK → provider，model_name） |
| `llm_profiles` | 场景级配置（FK → model，model_settings override） |
| `llm_presets` | route→profile 映射集合（可选继承 base_preset） |
| `llm_ask_options` | Ask Claread 用户可选模型档位 |

数据流：Directus authoring → `export-llm-config-bundle.mjs` → JSON bundle → `services/api/config/`

## Example Lab / Grammar RAG 当前契约

### Authoring 真源

人工维护的 few-shot 真源是：

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

`retrieval_text` 是 grammar example 的 embedding 主文本，使用稳定的 colon 格式：

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
- 管理 Eval Center 实验对象与回看入口
- 承载解析观察台与检查器

后端 / worker 负责：

- 实际 workflow 运行
- model invocation
- RAG 检索
- judge / replay / batch eval
- Zilliz collection 重建与 ingestion

### 数据分层

| 层 | 表前缀 | 说明 |
|----|--------|------|
| 业务层 | 无前缀 | 现有业务表（`analysis_records` 等），Directus 默认只读 |
| 控制层 | `eval_*` / `eval_node_lab_*` / `eval_workflow_*` | Eval Center 控制面表，Directus 可读写 |
| 配置层 | `llm_*` | LLM Config 控制面表，Directus 可读写 |
| 系统层 | `directus_*` | Directus 系统表，不手动干预 |

### 能力边界矩阵

| 能力 | Directus 原生 | 自定义扩展 | Claread API / worker |
|------|:---:|:---:|:---:|
| 数据浏览与搜索 | 主 | — | — |
| 关系跳转与详情 | 主 | — | — |
| Saved views / dashboards | 主 | — | — |
| Eval experiment runner | — | 主（module） | 辅（执行） |
| Judge / replay | — | 触发入口 | 主（执行） |
| RAG 检索 / ingestion | — | — | 主 |
| Example Lab authoring | 主（Collection） | 辅（AI generator） | 辅（LLM 代理） |
| 运维触发 | — | 辅 | 主 |

### 固定约束

- 平台只展示 prompt version，不承接 prompt 在线编辑
- 单一 admin 账号，不做多角色权限管理
- LangSmith 只负责 trace / run inspection，不替代自建 eval
- eval-center 各子路径默认 **不写 LangSmith trace**（`trace_scope="off"`）。只有 Workflow Lab compare 复用 `/analyze` 主链路时可显式 `trace_scope="inherit"` 写入，届时会带 `surface:eval_workflow_lab` tag。Node Lab / Node Lab Judge / Example Lab AI generator / Workflow Lab compare-judge 等子路径均不写 LangSmith。详细 trace 行为见 `docs/operations/langsmith.md`。

## 当前事实源

与 Claread Console 当前状态最相关的正式文档：

- `docs/architecture/eval-center-integration-map.md` — 联动更新清单与数据流
- `docs/operations/directus-local-dev.md` — 本地开发与热更新
- `docs/operations/testing.md` — 测试与验证
- `docs/product/current-state.md` — 产品当前状态
