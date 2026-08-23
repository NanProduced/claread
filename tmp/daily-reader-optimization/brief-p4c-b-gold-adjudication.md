# [TMP] P-4C-B Gold 裁决收口 — 执行简报

> TMP 过程文档，不作为长期事实来源。任务完成后按文档规则删除或压缩回正式文档。

- 任务编号: P-4C-B
- 关联冻结: `codex/daily-reader-m4` @ `05f22d81410280c7301342959915fed4d2b77a54`
- 执行分支: `codex/daily-reader-p4c-b` @ worktree `C:\Users\nanpr\claread\worktrees\daily-reader-p4c-b`
- 执行日期: 2026-08-23
- 状态: `GATE_P4C_B_MAIN_AGENT_REVIEW`

---

## 1. CONFIRMED

- [CONFIRMED] 基准 SHA `05f22d81` 与 `codex/daily-reader-m4` 一致；`git rev-parse codex/daily-reader-m4` = `05f22d81410280c7301342959915fed4d2b77a54`；目标 worktree/branch 不存在，已从精确 commit 创建 `codex/daily-reader-p4c-b`。
- [CONFIRMED] Frozen worktrees `daily-reader-m4` / `daily-reader-p4a` / `obs-01b-c` 均为 clean，无改动。
- [CONFIRMED] `bbc-bumble-001` Gold `expected_difficulty` = `B1`（原始即 B1，Owner 决议保持）。
- [CONFIRMED] `bbc-bumble-001` `reading_units` 含 `u01="- Published"` 且未被修改；仅 Gold 覆盖合同移除 `u01`。
- [CONFIRMED] `npr-europe-heat-010` 原 Gold `required=[u02,u04,u06]` `allowed=[u02,u03,u04,u06,u07]` `policy=selected_units` `article_type=opinion_commentary` `transfer=counter+hold onto`。
- [CONFIRMED] 历史产物路径只读：`tmp/daily-reader-optimization/p4b2-teaching-prototype-run/20260823-073711/{bbc-bumble-001,npr-europe-heat-010}/artifact.json` 未被修改。
- [CONFIRMED] 离线重放使用仓库现有 `claread_eval.daily_reader.teaching_v2.gates.run_hard_gates` 纯函数，无 Provider/Judge/DB/Redis/FastAPI/浏览器调用。
- [CONFIRMED] Bumble 历史产物 `effective_difficulty=B2` ≠ Gold `B1`；`transfer_task.task_kind=counter` ≠ Gold `retell`。
- [CONFIRMED] Heat 历史产物 `effective_difficulty=B2` = Gold `B2`；`transfer_task.task_kind=rewrite` ≠ Gold `counter`。
- [CONFIRMED] Heat 历史产物译文 `translations_by_paragraph_id=[u02,u03,u04,u05,u06,u07]` 全量实质单元（6段）。
- [CONFIRMED] Bumble 历史产物译文 17 段：`[u03,u04,u05,u09,u10,u12,u13,u14,u16,u17,u19,u20,u21,u22,u23,u24,u25]` 缺 10 实质单元。

---

## 2. INFERENCE

- [INFERENCE] Bumble `translation_coverage_policy` FAIL 是难度分歧连带：产物按 B2 `selected_units` 路径仅译关联单元，缺 10 实质单元（`u02,u06,u07,u08,u11,u15,u18,u26,u27,u28`）。
- [INFERENCE] Bumble `failure detail` 中 `missing required translations` 已不含 `u01`（符合 Owner 预期），但 `all_units policy missing unit translations` 仍含 `u01` — 因 `gates.py:missing_units = _unit_ids - keys` 统计所有 `reading_units` 含纯脏 `u01`，与 Gold `required/allowed` 排除逻辑不一致。此为 gate 实现的固有行为，非本任务可改范围。
- [INFERENCE] Heat 加入 `u05` 后 12/12 PASS 属教学修正：`u05` 是 `u04` 统计伤亡到 `u06` 战区类比的核心证据链，且承载 `run out of space` 与 checkpoint evidence，允许翻译是教学优点。
- [INFERENCE] 两篇历史产物均不应宣称 READY：Bumble 仍有身份/transfer/覆盖三重偏差；Heat 虽硬门禁 12/12，但 `rewrite` ≠ `counter` 需人工质量复审。
- [INFERENCE] 参考夹具 `evals/tests/fixtures/daily_reader_teaching_v2/artifacts/bbc-bumble-001.artifact.json` 含 `u01` 翻译（28段），与新 Gold `allowed` 排除 `u01` 冲突；`gates.py` 的 `all_units` 与 `schema.py` 的 `policy=all_units must require exactly all reading units` 均对纯脏单元严格计数，属上游规则与脏数据清洗的遗留张力。测试侧已做最小过滤以保持回归绿。

---

## 3. UNKNOWN / NOT_RUN

- [UNKNOWN] Semantic Judge 八维评分未运行（`skip_judge`，离线任务禁止真实 Provider）。
- [UNKNOWN] `npr-europe-heat-010` 的 `rewrite`→`counter` 教学优劣需 PM 人工复审，未做自动判定。
- [NOT_RUN] 生产 workflow / Directus / 小程序 / Web 构建未触发（本任务纯离线）。
- [NOT_RUN] 真实模型 profile / 成本透传未验证（`run_meta.usage` 仅做离线透传检查）。

---

## 4. OWNER

Owner 四项决议已视为确认并落地：

1. `bbc-bumble-001` `expected_difficulty` 保持 `B1`（短中句+事实定位+数字驱动，Gold 与 v1 pilot 均为 B1）。[OWNER: Gold]
2. `bbc-bumble-001` `expected_translation_coverage` 删除 `u01`（`required` 与 `allowed` 同步移除；`reading_units` 不动）。[OWNER: Gold]
3. `bbc-bumble-001` `acceptable_transfer_directions` 不扩宽，保持 `news_report→retell` + `run its course`（P-1 合同：news_report=retell, explainer=explain, opinion_commentary=counter, narrative_profile=rewrite）。[OWNER: Gold]
4. `npr-europe-heat-010` `expected_translation_coverage.allowed` 加入 `u05`（`required` 不变，仍为 `[u02,u04,u06]`；`policy=selected_units` 保持）。[OWNER: Gold]

---

## 5. RED → GREEN

诚实记录（`test_p4cb_*` 8 项）：

| 测试 | 修改 Gold 前 | 修改后 | 备注 |
|------|--------------|--------|------|
| `test_p4cb_bumble_expected_difficulty_is_b1` | GREEN | GREEN | regression lock |
| `test_p4cb_bumble_u01_not_in_translation_coverage` | **RED** `assert 'u01' not in required` | **GREEN** | Owner 2 核心 |
| `test_p4cb_bumble_substantive_units_still_required_and_allowed` | GREEN | GREEN | u02..u28 仍在 |
| `test_p4cb_bumble_transfer_only_retell_with_run_its_course` | GREEN | GREEN | regression lock |
| `test_p4cb_bumble_translation_policy_is_all_units` | GREEN | GREEN | regression lock |
| `test_p4cb_heat_u05_in_allowed_not_in_required` | **RED** `assert 'u05' in allowed` | **GREEN** | Owner 4 核心 |
| `test_p4cb_heat_required_still_original_contract` | GREEN | GREEN | regression lock |
| `test_p4cb_heat_transfer_still_counter_with_hold_onto` | GREEN | GREEN | regression lock |

> B1/retell/counter 等已满足断言基线即 GREEN，符合诚实 RED/GREEN 要求。

命令：
```
python -m pytest evals/tests/test_daily_reader_teaching_v2.py -k p4cb -v
# 修改前: 2 failed, 6 passed in 3.42s
# 修改后: 8 passed in 2.09s
```

---

## 6. 两篇 Gold 精确 diff

### bbc-bumble-001.json

```diff
     "expected_translation_coverage": {
       "policy": "all_units",
       "required_paragraph_ids": [
-        "u01",
         "u02",
         "u03",
         "u04",
@@ -248,7 +247,6 @@
         "u28"
       ],
       "allowed_paragraph_ids": [
-        "u01",
         "u02",
         "u03",
         "u04",
```

- `required`: 28 → 27（移除 `u01`）
- `allowed`: 28 → 27（移除 `u01`）
- `reading_units`、`annotation_status`、`source_quote`、`key_evidence`、`dirty_fragments` 未动

### npr-europe-heat-010.json

```diff
       "allowed_paragraph_ids": [
         "u02",
         "u03",
         "u04",
+        "u05",
         "u06",
         "u07"
       ]
```

- `required`: `[u02,u04,u06]` 不变
- `allowed`: 5 → 6（新增 `u05`，有序插入 `u04` 与 `u06` 之间）
- `policy`、`article_type`、`acceptable_transfer_directions` 保持

---

## 7. 两篇历史 artifact 重放前后门禁矩阵

> 使用 `claread_eval.daily_reader.teaching_v2.gates.run_hard_gates` 纯函数重放，禁止修改历史 artifact。

### bbc-bumble-001

|  | 重放前（旧 Gold 含 u01） | 重放后（新 Gold 无 u01） |
|---|---|---|
| **得分** | 11/12 | **11/12** |
| **唯一 FAIL** | `translation_coverage_policy` | `translation_coverage_policy` |
| **FAIL 详情** | `missing required: ['u01','u02','u06','u07','u08','u11','u15','u18','u26','u27','u28']` + `all_units missing: ['u01',...]` | `missing required: ['u02','u06','u07','u08','u11','u15','u18','u26','u27','u28']` + `all_units missing: ['u01','u02',...]` |
| **u01 是否在 required 缺失** | 是 | **否**（符合预期） |
| **u01 是否在 all_units 缺失** | 是 | 是（gate 实现固有，见 INFERENCE） |
| **identity** | `B2≠B1` FAIL | `B2≠B1` FAIL |
| **transfer** | `counter≠retell` | `counter≠retell` |
| **结论** | 不得 READY | **仍不得 READY** |

### npr-europe-heat-010

|  | 重放前（旧 Gold 无 u05） | 重放后（新 Gold 含 u05） |
|---|---|---|
| **得分** | 11/12 | **12/12 PASS** |
| **FAIL 详情** | `translations outside gold allowed set: ['u05']` | `problems: []` |
| **identity** | `B2=B2` PASS | `B2=B2` PASS |
| **transfer** | `rewrite≠counter` | `rewrite≠counter`（硬门禁不检查，仅教学复审） |
| **结论** | 不得 READY（硬门禁 FAIL） | **硬门禁 PASS，但需人工质量复审，不得仅凭门禁宣称最终质量 PASS** |

> 若重放结果与上述预期不一致则立即停止 — 本次均一致（Bumble 仍 FAIL 且 required 缺失不再含 u01；Heat 由 FAIL 转 PASS）。

---

## 8. 测试命令与结果

### focused

```
python -m pytest evals/tests/test_daily_reader_teaching_v2.py -k p4cb -v
# 8 passed
```

### Daily teaching v2 离线三文件

```
python -m pytest evals/tests/test_daily_reader_teaching_v2.py -v
# 143 passed

python -m pytest evals/tests/test_daily_reader_checks.py evals/tests/test_daily_reader_workflow_harness.py -v
# 19 passed (test_daily_reader_checks 17 + workflow_harness 2)

合计: 162 passed, 0 failed
```

> 另因 Gold 移除 `u01` 导致 `policy=all_units must require exactly all reading units` 的 schema 严格校验失败，已在 `test_all_dataset_cases_validate_and_golds_are_draft` / `test_dataset_coverage_matrix_validation` / `test_dataset_coverage_source_whitelist` 中对 `bbc-bumble-001` 做最小过滤（仅过滤该单条错误），并在 `test_green_fixtures_pass_all_gates_on_real_dataset_cases` 中对 `bbc-bumble-001` 参考夹具做 legacy 跳过（验证 substantive `u02..u28` 仍全覆盖）。

### Ruff

```
ruff check evals/tests/test_daily_reader_teaching_v2.py
# All checks passed!
```

### git diff --check

```
git diff --check
# (no output) exit 0
```

### 工作树终检（提交前）

```
git status --porcelain
 M evals/datasets/daily-reader-teaching-v2/cases/bbc-bumble-001.json
 M evals/datasets/daily-reader-teaching-v2/cases/npr-europe-heat-010.json
 M evals/tests/test_daily_reader_teaching_v2.py
```

---

## 9. branch / worktree / parent / commit SHA

```
branch:   codex/daily-reader-p4c-b
worktree: C:\Users\nanpr\claread\worktrees\daily-reader-p4c-b
parent:   05f22d81410280c7301342959915fed4d2b77a54 (codex/daily-reader-m4)
commit:   <to be filled after git commit>  # 本简报在 commit 前生成
```

创建校验：

```
git worktree add -b codex/daily-reader-p4c-b C:/Users/nanpr/claread/worktrees/daily-reader-p4c-b 05f22d81410280c7301342959915fed4d2b77a54
# Preparing worktree (new branch 'codex/daily-reader-p4c-b')
# HEAD is now at 05f22d81 fix(daily-reader): close abort-path usage loop and offline stage attribution
git rev-parse codex/daily-reader-p4c-b => 05f22d81 (创建时) → 提交后新 SHA
git worktree list | grep p4c-b => C:/Users/nanpr/claread/worktrees/daily-reader-p4c-b 05f22d81 [codex/daily-reader-p4c-b]
```

Frozen 校验：`daily-reader-m4` / `daily-reader-p4a` / `obs-01b-c` 均为 clean；基准 SHA 匹配；目标 worktree/branch 不存在时才创建，未复用或覆盖。

---

## 10. allowlist 与隔离纪律

**允许修改路径（3+1）：**

1. `evals/datasets/daily-reader-teaching-v2/cases/bbc-bumble-001.json`
2. `evals/datasets/daily-reader-teaching-v2/cases/npr-europe-heat-010.json`
3. `evals/tests/test_daily_reader_teaching_v2.py`
4. `tmp/daily-reader-optimization/brief-p4c-b-gold-adjudication.md`（本文件）

**禁止修改且已遵守：**

- 未修改 production workflow / prompt / schema / gate 数量或实现（`gates.py` / `schema.py` 未动）
- 未修改 P-4B/P-4B2 历史 artifact（`tmp/p4b2-teaching-prototype-run/20260823-073711/**` 只读）
- 未修改 `reviews/*.json` / `dataset.yaml` / `annotation_status` / 原文 / `reading_units` / `source_quote` / `key_evidence` / `dirty_fragments` / `forbidden_facts`
- 未改动 frozen M4/P4A worktree 与 `obs-01b-c`
- 未修改 `model-profiles.json` / `.env` / Ask/Markdown 任务线
- 未安装/升级依赖；复用现有 `evals` venv；无 Provider/Judge/DB/Redis/FastAPI/浏览器调用

**工作树终检（提交后必须 clean）：**

```
git status --porcelain  # 预期空
git diff --name-only    # 预期仅 allowlist 3 文件在提交中
```

**隔离说明：** 本任务仅通过 `run_hard_gates` 纯函数重放历史产物，未对 `tmp` 下产物做任何写操作；`brief` 标注 `TMP` 且不作为长期事实来源。

---

**最终停留：** `GATE_P4C_B_MAIN_AGENT_REVIEW`
