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
