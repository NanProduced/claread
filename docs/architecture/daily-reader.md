# Daily Reader 架构

Daily Reader 是 Claread 的公开英文精读刊物：独立固定 workflow，不接入用户提交内容的 Reader orchestration。当前唯一生产链是 v2 教学会话；v1 逐段问答与固定讨论题已退役。公开面 `/daily`、`/daily/{articleId}` 只读 `published` 行。

## 生产链结构

```text
discovery（BBC RSS / Guardian API / NPR RSS）
  → extraction（trafilatura + 脏数据清洗；transcript 拒收）
  → scoring（五维含 learning_fit，阈值 7.0）
  → LangGraph 4+1
       light_normalize
       → blueprint → language_support → translation → semantic_review
       →（仅 FAIL）refinement，每篇至多一次
       → daily_projection
  → lesson_v2 JSONB 落库（status=draft）
  → 人工发布
```

落点：`app/services/daily_reader/discovery.py`、`extraction.py`、`scoring.py`（`SCORE_THRESHOLD = 7.0`）、`workflow.py`、`pipeline.py`。`learning_fit` 判据是学习适配（可迁移语言密度、篇幅、过易/低适配不入选），不是单纯难度。

`lesson_v2` 是教学包唯一结构化真相源。公开 API `DailyReaderArticleResponse`（`app/schemas/daily_reader.py`）三载荷：`lesson_blueprint`、`learning_package`、`reading_units`（`body_json` 段落，unit id 与教学锚点同源）。`NULL lesson_v2` 视为前 v2 行，载荷为空。

分层处置（`pipeline.stores_quality_abort_as_draft`）：

| 结局 | 条件 | 落库 |
|---|---|---|
| draft + 质量标记 | 硬闸未过、review 后仍 FAIL、冻结派生字段被改 | `status=draft`，`run_meta.outcome=draft_with_verdict` |
| 硬 abort | schema 违规、传输/节点错误、其它 fail-closed | 不写入 `daily_readers`；证据进 `pipeline_runs.errors` |
| published | 仅人工发布 | 公开端点过滤 `status='published'`（`service.py`） |

## 教学合同核心

一篇真实英文，约 10–15 分钟：读前定位 → 首读原文 → 证据自测 → 精读词句 → 微型迁移。稀疏脚手架；原文不改写。中文图说默认不生成（来源 caption 优先，可空）。

`lesson_blueprint`：`article_type`、`effective_difficulty`、`title_zh` / `subtitle_zh` / `tags_zh`、`reading_mission`、`learning_objectives[1..2]`、`structure_map[2..6]`、`selected_paragraph_ids`、`comprehension_checkpoints[2..4]`、`transfer_task`。

`learning_package`：`language_targets[3..5]`、`sentence_maps[1..2]`、`translations_by_paragraph_id`、`post_read_summary`。词句、答案、结构节点、译文必须锚定 reading unit id（`uNN`）。

四种 `article_type`（混合取主类型，不设 mixed/unknown）：`news_report` / `opinion_commentary` / `explainer` / `narrative_profile`。类型改变蓝图与任务，不改变清洗、锚点、存储或 workflow 拓扑。

难度决定脚手架，不改变原文；真实文章从 B1 起：

| 难度 | 译文 |
|---|---|
| B1 | 全部 reading unit 预生成段译，默认折叠 |
| B2 / C1 | 只为检查点、语言目标、句子地图关联单元预生成；普通单元无每用户即时翻译 |

理解检查是全篇 2–4 个 `comprehension_checkpoints`（`prompt` + `skill` + 原文锚点 + 参考答案），不是逐段问答。每篇恰好 1 个 `transfer_task`（`retell` / `rewrite` / `counter` / `explain`）。字段合同：`app/services/daily_reader/teaching/schema.py`、`app/schemas/internal/daily_lesson_v2.py`。

## 五道防线

单一事实源 `app/services/daily_reader/teaching/`（stdlib-only：无 pydantic / 网络 / DB）。evals 经 `sys.path` 注入 `services/api` 后 import，Gold / Judge / 报告留在 `evals/claread_eval/daily_reader/teaching_v2/`。

1. **DTO 硬边界**：阶段输出走 pydantic（计数、`UnitId`、必填、标题合同）；畸形输出烧掉机内 output retry，不把不可解析产物交给硬闸。落点：`app/schemas/internal/daily_lesson_v2.py`。
2. **确定性合同检查**：锚点/结构/声明关系进 semantic_review 输入，refinement 后 fail-closed 重放。落点：`teaching/prototype.py`（`validate_teaching_contract`、`derive_translation_unit_ids`）、`teaching/gates.py`（`run_hard_gates`）。
3. **post-patch 复核**：refinement patch 再过 DTO；拒绝则 pre-image 恢复、记 FAIL、**不中止批次**。落点：`workflow.py` `refinement_node`、`teaching/refinement_addressing.py`。
4. **stop / abort 证据**：每次 fail-closed 记录 `abort_reason` + `abort_diagnostics`；质量 draft 另盖 `run_meta.quality`。落点：`pipeline.py` `build_abort_error_evidence`。
5. **usage 守恒 + 预算门禁**：分阶段 ledger 经 `_aggregate_usage` 汇总；超冻结篇级上限停。落点：`workflow.py`。

## 运维注意

- **模型路由**：Daily 走独立 preset `daily_reader`（`services/api/config/model-presets.json`，gitignored；示例见 `model-presets.example.json`）。DashScope 免费额度与官方付费之间切换：改 preset 的 route→profile 映射（或 `preset` 继承后再覆盖），不要改 workflow 代码。封面选择确定性，不经模型。
- **BBC / 出口**：多数授权 Host 需系统代理；`HTTPS_PROXY` 由 httpx `trust_env` 读取（`discovery.py`）。
- **Guardian**：正文走 API；key 在本地 env，会过期需续期，密钥不入库。封面 CDN（guim）实测最大 1000px，该源 `min_cover_width=1000`（`discovery.py`），其它源仍走更高像素门禁。
- **运行身份**：启动 API / evals 时 `PYTHONPATH` 必须指向本 worktree 的 `services/api`。evals teaching_v2 会把 `services/api` 插入 `sys.path`；editable 串到别的 worktree 会静默跑错树。
