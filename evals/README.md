# Claread Evals

Claread 评测项目：LLM-as-a-Judge、样本集、确定性 grader 与评测流。目录约定与边界见 [AGENTS.md](./AGENTS.md)。

## 目录约定

| 目录 | 内容 |
|------|------|
| `datasets/<dataset-id>/` | 金标数据集（`dataset.yaml` + `cases/` 或等价布局） |
| `rubrics/` | rubric YAML：确定性检查契约 + judge 维度/prompt 契约（单一来源） |
| `runs/<run-id>/` | 每次评测落盘：`run.json` + `report.md` + `artifacts/` |
| `claread_eval/` | 评测代码包（graders / checks / judge 客户端） |
| `scripts/` | 一条命令入口脚本 |
| `tmp/` | TMP 过程文件，gitignore，不作长期事实来源 |

## 每日精读回归评测（daily-reader-regression-v1，任务包 A-6）

金标集 5 篇：三篇已落库评审文（`daily_2026_08_15_001/002/003` 逐字快照）+ 构造 B1 简单文 + 脏数据陷阱文。rubric = 4 项确定性检查（无 boilerplate / 高亮去重 / 长难句译文与段译逐字一致 / 金标表达覆盖）+ 4 维 LLM judge（选词难度匹配、长难句复杂度、中文标题质量、整体学习价值）。

所有命令在 `evals/` 下执行（用 `evals/.venv`，经 uv）：

```powershell
# 基线模式：直接评 daily_readers 已落库数据（不重跑 workflow，需 docker postgres 在跑）
uv run python scripts/run_daily_reader_eval.py --mode baseline

# 回归模式：对金标集重跑生产 daily_reader workflow 后出分
# （真实 LLM 调用；workflow 本身零 DB 写入，不污染 daily_readers）
$env:CLAREAD_ALLOW_REAL_LLM_TESTS='1'
uv run python scripts/run_daily_reader_eval.py --mode workflow

# 单篇 + judge 凭据（judge 走 OpenAI 兼容接口，默认 deepseek；不设则 judge 跳过）
uv run python scripts/run_daily_reader_eval.py --mode baseline `
    --case bbc-manifestos-002 --judge-env-file ../services/api/.env
```

要点：

- 付费调用门控：`CLAREAD_ALLOW_REAL_LLM_TESTS=1`（workflow 模式必需；judge 同时需要 key，优先 `CLAREAD_EVAL_JUDGE_*` 三件套，回退 `DEEPSEEK_API_KEY`）。
- judge 配置见 `rubrics/daily-reader-regression-v1.yaml` 的 `judge:` 段（env 名、回退端点、温度）。
- workflow 模式用 `services/api/.venv` 子进程跑 `daily_reader_workflow_harness.py`，复用 `build_daily_reader_graph()` 调用链，只 dump final state 为 artifact，不写库。
- 综合分 = 0.5×确定性通过率 + 0.5×judge 均分/5；judge 缺位时只算确定性并在报告标注。
- 基线分：`runs/daily-reader-baseline-20260819/`。

## 每日精读教学合同 v2 评测（daily-reader-teaching-v2，任务包 P-2）

独立 v2 模块组（`claread_eval/daily_reader/teaching_v2/`）+ 最小 artifact runner，v1 文件零改动（仅 import 复用 `normalize_text`/`normalize_expression`）。金标集 10 篇冻结真实文章（BBC x8 + NPR x2；三篇自 v1 回归快照逐字重铸，七篇 2026-08-21 只读抓取）。难度配额只计 `cleaned_publish`：B1×3、B2×3、C1×3；`bbc-iphone-motion-sickness-006` 用已有冻结快照收成 B2 explainer。迁移任务按 P-1 四种 `retell | rewrite | counter | explain` 拉开，B2/C1 opinion 用 `counter`、explainer 用 `explain`。Guardian 新增全文未获明确提交许可，标记 `OWNER_INPUT_REQUIRED`，不再抓取。gold 全部 `annotation_status: DRAFT_PM_REVIEW`，人工批准不由本包写入。

合同构成：

- 12 条硬门禁（`teaching_v2/gates.py` 的 `HARD_GATES` 有序注册表）：确定性层只验锚点/结构/声明关系，不做语义启发式；翻译覆盖按 gold policy 分派（B1 all_units 全部实质 units 恰一份共享译文；B2 关联单元必须有译文；C1 只要求明确选中的高难单元）。
- 八维 Judge（`teaching_v2/judge.py`）：一次调用评 source_fidelity / pedagogical_focus / difficulty_fit / article_type_fit / evidence_retrieval / transfer_value / chinese_quality / learning_sequence；`parse_judge_output` fail-closed（恰好 8 维、整数 1-5、禁 clamp、空 rationale 即 error）；未跑状态 `SEMANTIC_NOT_RUN`；本包不含任何网络调用路径。
- 人工审阅验收（`teaching_v2/review.py`）：逐教学点 keep/minor_edit/major_edit/delete；事实性 major error=0、keep+minor_edit≥85%、单篇 major_edit+delete≤15%，type/difficulty 分层独立判定；未审阅 → `HUMAN_REVIEW_PENDING`。
- 成本占位/透传（`teaching_v2/report.py` 的 `cost_block`）：artifact 携带真实非负 usage/latency 时逐字段原样透传，完全缺失才输出 `NOT_RUN_OWNER_REQUIRED` 占位。

运行（结构性零 DB 零网络，artifact 从目录读 JSON；judge 未接入，`--no-judge`）：

```powershell
uv run python scripts/run_daily_reader_teaching_eval.py `
    --dataset-dir datasets/daily-reader-teaching-v2 `
    --artifacts-dir <artifacts 目录> `
    --runs-dir <输出目录> --run-id <id> --no-judge
```

verdict 合同：全部硬门禁通过 ∧ 八维每项≥4 ∧ overall≥0.90 ∧ 人工门禁完成 → 质量 `PASS`；gold reject 且按预期拒绝 → `EXPECTED_REJECT`（与质量 PASS 分列）；judge 缺位 → `SEMANTIC_NOT_RUN`；人工缺位 → `HUMAN_REVIEW_PENDING`；非法/缺维 Judge 或 1:1 审阅不完整 → `FAIL`。overall = 0.5×确定性 + 0.5×judge 均分/5（judge 未跑不给 PASS；`overall_mean` 只统计完成八维 Judge 的 cleaned_publish，全部 `SEMANTIC_NOT_RUN` 时为 `null`）。rubric 单一来源：`rubrics/daily-reader-teaching-v2.yaml`。
