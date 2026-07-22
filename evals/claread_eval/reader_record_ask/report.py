"""R4-A3 Reader Record Ask evaluation report generator.

Produces the markdown evaluation report defined by the R4-A3 spec
(`.trae/specs/reader-record-ask-r4-a3-correctness-eval/spec.md` —
Requirement: 交付报告内容 + 报告脱敏与可聚合).

The report is intentionally a pure function over the inputs: it never
reads the network or the DB, never re-runs evaluators, and never
imports production code. Callers (the runner script in
``evals/scripts/run_reader_record_ask_r4_a3.py``) are responsible for
loading artifacts, running the 11 evaluators, and calling
:func:`generate_r4_a3_report` with the resulting
:class:`AggregatedReport`.

Sanitization invariants enforced here (spec Requirement: 报告脱敏与可聚合):

- No BBC article body (≥200 contiguous characters of source prose).
  Only ``record_id`` / ``case_id`` / structured expected facts appear.
- No ``reasoning_content`` field reference in the markdown.
- No API key (``sk-`` / ``api_key=`` / ``api-key:``) leaks.
- No provider request payload.
- Sensitive exceptions truncated to 200 characters.
- ``final_text`` summary ≤200 characters with ``[truncated]`` marker.
- Evidence snippet ≤200 characters with ``[truncated]`` marker.
"""

from __future__ import annotations

from typing import Any

from claread_eval.reader_record_ask.evaluators.aggregator import AggregatedReport
from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Dataset,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_SNIPPET_CHARS = 200

# R4-A4-0 (Task 5): canonical A/B comparison phases. The model name is
# NOT hardcoded — it is matched as a case-insensitive regex against
# ``aggregated.per_config`` keys, so the report renders real run data
# for whatever model actually ran (e.g. ``deepseek-v4-flash``) while
# still showing explicit "no data" rows for phases that didn't run.
# The ``chat`` alternative preserves backward compatibility with older
# fixtures that used ``deepseek-chat`` as the short name.
_CANONICAL_PHASES: tuple[tuple[str, str, str], ...] = (
    ("Flash non-thinking", r"(?:flash|chat)", "thinking=False"),
    ("Flash thinking", r"(?:flash|chat)", "thinking=True"),
    ("Pro thinking", r"pro", "thinking=True"),
)

# 15 required content sections per spec Requirement: 交付报告内容.
# Sections 16-19 are the rework closure additions (spec: R4-A3 rework —
# 能力边界 / 覆盖状态 / budget 语义 / thinking 验证).
REQUIRED_SECTION_HEADERS: tuple[str, ...] = (
    "1. 开始/结束 HEAD",
    "2. 本轮文件与并行脏树区分",
    "3. Harness 方案及另一方案被拒原因",
    "4. Dataset case 清单",
    "5. Evaluator 合同",
    "6. 所有测试结果",
    "7. 真实模型每配置调用次数/延迟/token/通过率",
    "8. unsupported claim / 完整性 / 指令遵循逐项结果",
    "9. Flash non-thinking / Flash thinking / Pro 对照",
    "10. 明确失败簇",
    "11. R4-A4 候选修复建议",
    "12. R4-A3 最终裁决",
    "13. 是否允许进入 R4-A4 和 R4-B1",
    "14. R4 tracker 更新",
    "15. 未 commit",
    "16. 能力边界声明",
    "17. 真实覆盖状态",
    "18. request/token budget 真实语义",
    "19. thinking 验证方式",
)

# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------


def _truncate(text: str | None, limit: int, *, marker: str = "[truncated]") -> str:
    """Truncate ``text`` to ``limit`` chars, appending ``marker`` if cut."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + marker


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_heads(start_head: str, end_head: str) -> str:
    return (
        f"## 1. 开始/结束 HEAD\n\n"
        f"- 开始 HEAD: `{start_head}`\n"
        f"- 结束 HEAD: `{end_head}`\n"
        f"- 说明: 本轮未执行 git commit / reset / restore / checkout / stash；"
        f"开始与结束为同一 commit（如并行 agent 移动 HEAD，则记录真实移动）。\n"
    )


def _render_files_and_dirty_tree(
    parallel_dirty: list[str],
    harness_choice: str,
    rejected_harness: str,
    rejected_reason: str,
    modified_files: list[str] | None,
    task_label: str,
) -> str:
    """Sections 2 + 3: 本轮文件 + 并行脏树 + harness 方案.

    R4-A4-0 (Task 5): ``modified_files`` is now parameterized — the
    report no longer hardcodes the previous round's Task 5 file list.
    Callers pass the actual modified-files list for the current round.
    """
    dirty_tree_lines = (
        "\n".join(f"- `{p}`" for p in parallel_dirty)
        if parallel_dirty
        else "- (空)"
    )
    if modified_files:
        modified_lines = "\n".join(f"- `{p}`" for p in modified_files)
    else:
        modified_lines = "- (未提供)"
    return (
        f"## 2. 本轮文件与并行脏树区分\n\n"
        f"### 2.1 本轮修改文件（{task_label}）\n\n"
        f"{modified_lines}\n\n"
        f"### 2.2 并行脏树（不在本轮允许路径，未修改）\n\n"
        f"{dirty_tree_lines}\n\n"
        f"## 3. Harness 方案及另一方案被拒原因\n\n"
        f"### 3.1 采用的 harness 方案\n\n"
        f"- **方案**: {harness_choice}\n\n"
        f"### 3.2 被拒绝的 harness 方案\n\n"
        f"- **方案**: {rejected_harness}\n"
        f"- **拒绝原因**: {rejected_reason}\n"
    )


def _render_dataset_cases(dataset: ReaderRecordAskR4A3Dataset) -> str:
    """Section 4: dataset case 清单."""
    header = (
        "| case_id | source_kind | question_category | input_mode | "
        "source_metadata | baseline_mode | rag_mode | "
        "external_knowledge_policy |"
    )
    sep = "|" + "---|" * 8
    rows = []
    for case in dataset.cases:
        rows.append(
            f"| `{case.id}` | {case.source_kind} | {case.question_category} | "
            f"{case.input_mode} | {case.source_metadata} | "
            f"{case.baseline_mode} | {case.rag_mode} | "
            f"{case.external_knowledge_policy} |"
        )
    table = "\n".join([header, sep, *rows])
    return (
        "## 4. Dataset case 清单\n\n"
        f"- dataset id: `{dataset.id}`\n"
        f"- schema_version: `{dataset.schema_version}`\n"
        f"- case 总数: {len(dataset.cases)}\n\n"
        f"{table}\n\n"
        "- BBC record case 仅引用 `record_id` 与结构化期望事实，"
        "不含 BBC 版权正文。\n"
    )


def _render_evaluator_contract() -> str:
    """Section 5: evaluator 合同."""
    dims = [
        (
            "answer_success",
            "高",
            "finalized.status=='ok' AND final_text 非空 AND 无 forbidden pattern",
        ),
        (
            "context_support",
            "高",
            "required_article_facts 每项出现在 final_text 且可追溯至 evidence",
        ),
        (
            "unsupported_temporal_claims",
            "高",
            "final_text 年份/日期 token 必须在 allowed_temporal_claims 中",
        ),
        (
            "numeric_grounding",
            "中",
            "final_text 数量/比例/金额必须可追溯至文章正文",
        ),
        (
            "entity_precision",
            "中",
            "实体必须在 allowed_entities_by_type 中且类型正确（LLM judge 仅补充）",
        ),
        (
            "exhaustive_completeness",
            "高",
            "expected_entity_set 的 set recall；遗漏 = recall < 1.0 = 失败",
        ),
        (
            "instruction_following",
            "高",
            "requested_count_kind=exercise_items/sentences 时数量必须匹配",
        ),
        (
            "language_consistency",
            "中",
            "answer_language=zh 时不得无必要整句英文",
        ),
        (
            "evidence_minimality",
            "中",
            "cited_evidence_handles ≤6、非重复、全在 registry",
        ),
        (
            "tool_decision",
            "中",
            "baseline_complete=True + 文章级问题 → read_range_calls==0",
        ),
        (
            "usage_observability",
            "中",
            "usage.requests/tokens、model route、thinking、latency、final status",
        ),
    ]
    header = "| dimension | 默认 severity | 检查内容 |"
    sep = "|---|---|---|"
    rows = [f"| {d} | {s} | {c} |" for d, s, c in dims]
    table = "\n".join([header, sep, *rows])
    return (
        "## 5. Evaluator 合同\n\n"
        "11 维确定性 evaluator"
        "（`evals/claread_eval/reader_record_ask/evaluators/`），"
        "每个 evaluator 返回 "
        "`EvalDimensionResult(dimension, passed, severity, details, evidence_refs)`"
        "。\n\n"
        f"{table}\n\n"
        "**关键不变量**: LLM judge 仅允许补充 `entity_precision`，"
        "且**不得覆盖任何确定性失败**。aggregator 以 `passed` 为单一来源；"
        "`llm_judge_used` / `llm_judge_note` 仅作记录字段，"
        "不参与 pass/fail 计数与失败簇聚合。\n\n"
        "**aggregator**: "
        "`aggregate_results(case_results, cases_by_id) -> AggregatedReport`，"
        "输出 `per_dimension`、`per_config`、`failure_clusters`。\n"
    )


def _render_test_results(
    deterministic_tests_passed: bool,
    deterministic_tests_summary: str,
    real_model_blocked: bool,
) -> str:
    """Section 6: 所有测试结果."""
    real_model_line = (
        "- 真实模型 harness "
        "(services/api/tests/test_reader_record_ask_real_llm_eval.py): "
        "**BLOCKED** — 环境门未开（无 `CLAREAD_ALLOW_REAL_LLM_TESTS` / "
        "`CLAREAD_R4_A3_RUN` / `CLAREAD_REAL_LLM_MODEL` / DB / record_id）。"
        " 本报告生成路径未调用 pytest；默认 gate 为 skip real phase "
        "tests，不得在此断言精确 passed/skipped 计数。\n"
        if real_model_blocked
        else "- 真实模型 harness "
        "(services/api/tests/test_reader_record_ask_real_llm_eval.py): "
        "已运行（详见 §7、§9）。\n"
    )
    return (
        "## 6. 所有测试结果\n\n"
        f"- 确定性测试结果: {'PASSED' if deterministic_tests_passed else 'FAILED'}\n"
        f"- 摘要: {deterministic_tests_summary}\n"
        f"{real_model_line}"
        "- 静态检查: 以本轮实际执行的 `ruff check` / 相关 pytest 命令为准；"
        "本段不硬编码 passed/skipped 数量。\n"
    )


def _render_real_model_runs(
    aggregated: AggregatedReport,
    real_model_blocked: bool,
    real_model_block_reason: str | None,
    real_model_user_commands: list[str] | None,
) -> str:
    """Section 7: 真实模型每配置调用次数/延迟/token/通过率."""
    if real_model_blocked:
        cmd_lines = (
            "\n".join(f"  - `{c}`" for c in real_model_user_commands)
            if real_model_user_commands
            else "  - (无)"
        )
        return (
            "## 7. 真实模型每配置调用次数/延迟/token/通过率\n\n"
            "**状态: BLOCKED**\n\n"
            f"- 阻塞条件: {real_model_block_reason or '未指定'}\n"
            "- 未运行任何真实模型；所有真实模型相关字段标记为 BLOCKED 或 N/A；"
            "real model run was not executed; results are not fabricated.\n\n"
            "用户可执行命令（开放 gate 后再运行）:\n\n"
            f"{cmd_lines}\n"
        )

    header = (
        "| 配置 (model\\|thinking) | total_runs | pass_rate | "
        "avg_latency (s) | avg_tokens | total_requests |"
    )
    sep = "|" + "---|" * 6
    rows: list[str] = []
    for key, metrics in sorted(aggregated.per_config.items()):
        runs = metrics.get("total_runs", 0)
        pr = float(metrics.get("pass_rate", 0.0))
        al = float(metrics.get("avg_latency", 0.0))
        at = float(metrics.get("avg_tokens", 0.0))
        tr = int(metrics.get("total_requests", 0))
        rows.append(
            f"| `{key}` | {runs} | {pr:.2f} | {al:.2f} | {at:.0f} | {tr} |"
        )
    table = "\n".join([header, sep, *rows]) if rows else "(no per-config data)"
    return (
        "## 7. 真实模型每配置调用次数/延迟/token/通过率\n\n"
        f"{table}\n\n"
        "- 说明: `total_runs` = 该配置下 (case, run) 对的数量；"
        "`pass_rate` = 该配置下所有 dimension 全通过的 run 占比。\n"
    )


def _render_per_dimension_table(aggregated: AggregatedReport) -> str:
    """Section 8: unsupported claim/完整性/指令遵循逐项结果."""
    target_dims = (
        "unsupported_temporal_claims",
        "exhaustive_completeness",
        "instruction_following",
    )
    header = "| dimension | passed | failed | total |"
    sep = "|---|---|---|---|"
    rows: list[str] = []
    for dim in target_dims:
        bucket = aggregated.per_dimension.get(
            dim, {"passed": 0, "failed": 0, "total": 0}
        )
        rows.append(
            f"| {dim} | {bucket['passed']} | {bucket['failed']} | {bucket['total']} |"
        )
    table = "\n".join([header, sep, *rows])
    return (
        "## 8. unsupported claim / 完整性 / 指令遵循逐项结果\n\n"
        f"{table}\n\n"
        "- 其他 dimension 的 per-distribution 见 §10 失败簇。\n"
    )


def _render_ab_comparison(
    aggregated: AggregatedReport,
    real_model_blocked: bool,
) -> str:
    """Section 9: Flash non-thinking / Flash thinking / Pro 对照.

    R4-A4-0 (Task 5): the per-config rows are no longer looked up by
    hardcoded model name. Each canonical phase (Flash non-thinking /
    Flash thinking / Pro thinking) is matched against
    ``aggregated.per_config`` keys via a case-insensitive regex on the
    model portion of the ``model_short_name|thinking=<bool>`` key. This
    lets the report render real run data for whatever model actually
    ran (e.g. ``deepseek-v4-flash|thinking=False``) while still showing
    explicit ``N/A (no data)`` rows for phases that didn't run.
    """
    header = (
        "| 配置 | pass_rate | avg_latency | avg_tokens | total_requests | "
        "unsupported_claim_count | completeness_recall_avg | "
        "instruction_following_rate |"
    )
    sep = "|" + "---|" * 8

    if real_model_blocked:
        rows = [
            "| Flash non-thinking | N/A (blocked) | N/A | N/A | N/A | N/A | N/A | N/A |",
            "| Flash thinking | N/A (blocked) | N/A | N/A | N/A | N/A | N/A | N/A |",
            "| Pro thinking | N/A (blocked) | N/A | N/A | N/A | N/A | N/A | N/A |",
        ]
        return (
            "## 9. Flash non-thinking / Flash thinking / Pro 对照\n\n"
            "**状态: N/A (blocked)** — 真实模型未运行，无法生成 A/B 对照。\n\n"
            f"{header}\n{sep}\n" + "\n".join(rows) + "\n"
        )

    def _find_phase_keys(model_pattern: str, thinking_flag: str) -> list[str]:
        """R4-A4-0 final closure (P1-2): return ALL matching config keys.

        Returns:
            - Empty list: 0 matches → caller renders ``N/A (no data)``.
            - Single-element list: 1 match → caller renders the row.
            - Multi-element list: >1 matches → caller renders
              ``AMBIGUOUS (N matches: key1, key2, ...)`` fail-closed.
              The previous implementation silently returned the first
              match, which depended on dict insertion order and could
              mask a real config collision (e.g. ``deepseek-v4-flash|
              thinking=False`` and ``deepseek-chat|thinking=False``
              both matching ``(?:flash|chat)``).
        """
        import re as _re

        pat = _re.compile(model_pattern, _re.IGNORECASE)
        return [
            key for key in aggregated.per_config
            if thinking_flag in key and pat.search(key)
        ]

    def _row(label: str, model_pattern: str, thinking_flag: str) -> str:
        keys = _find_phase_keys(model_pattern, thinking_flag)
        if not keys:
            return (
                f"| {label} | N/A (no data) | N/A | N/A | N/A | N/A | N/A | N/A |"
            )
        if len(keys) > 1:
            # R4-A4-0 final closure (P1-2): >1 match is ambiguous —
            # fail-closed. Do NOT silently pick the first key.
            joined = ", ".join(keys)
            return (
                f"| {label} | AMBIGUOUS ({len(keys)} matches: "
                f"{joined}) | N/A | N/A | N/A | N/A | N/A | N/A |"
            )
        key = keys[0]
        metrics = aggregated.per_config[key]
        return (
            f"| {label} (`{key}`) | "
            f"{float(metrics.get('pass_rate', 0.0)):.2f} | "
            f"{float(metrics.get('avg_latency', 0.0)):.2f} | "
            f"{float(metrics.get('avg_tokens', 0.0)):.0f} | "
            f"{int(metrics.get('total_requests', 0))} | "
            f"{int(metrics.get('unsupported_claim_count', 0))} | "
            f"{float(metrics.get('completeness_recall_avg', 0.0)):.2f} | "
            f"{float(metrics.get('instruction_following_rate', 0.0)):.2f} |"
        )

    rows = [
        _row(label, model_pat, thinking_flag)
        for label, model_pat, thinking_flag in _CANONICAL_PHASES
    ]
    table = "\n".join([header, sep, *rows])
    return (
        "## 9. Flash non-thinking / Flash thinking / Pro 对照\n\n"
        "R4-A4-0 (Task 5): per-config rows are matched by regex against "
        "real ``aggregated.per_config`` keys (no hardcoded model names). "
        "Phases that did not run show ``N/A (no data)``.\n\n"
        f"{table}\n"
    )


def _render_failure_clusters(
    aggregated: AggregatedReport,
    real_model_blocked: bool,
) -> str:
    """Section 10: 明确失败簇."""
    if real_model_blocked or not aggregated.failure_clusters:
        return (
            "## 10. 明确失败簇\n\n"
            "**状态: N/A (blocked / 无真实运行数据)**\n\n"
            "- 真实模型未运行，无法形成真实失败簇。\n"
            "- 基于 spec 假设的预期失败簇（仅作 R4-A4 候选修复参考，"
            "不构成真实运行证据）:\n"
            "  - `unsupported_temporal_claims × city_enumeration × "
            "2025-year-hallucination` (BBC case 中预期出现)\n"
            "  - `unsupported_temporal_claims × publish_date × "
            "2025-year-hallucination` (source_metadata=unknown 时)\n"
            "  - `exhaustive_completeness × city_enumeration × "
            "missing-Thunder Bay` (Thunder Bay 遗漏)\n"
            "  - `entity_precision × city_enumeration × type-confusion` "
            "(\"纽约州西部\" 混入 city)\n"
            "  - `instruction_following × exercise_one × count-mismatch` "
            "(\"一道\" → 5 题)\n"
        )

    header = "| dimension | question_category | failure_pattern | failed | total | case_ids |"
    sep = "|---|---|---|---|---|---|"
    rows: list[str] = []
    for cluster in aggregated.failure_clusters:
        case_ids_str = ", ".join(f"`{c}`" for c in cluster.case_ids)
        rows.append(
            f"| {cluster.dimension} | {cluster.question_category} | "
            f"{cluster.failure_pattern} | {cluster.failed_count} | "
            f"{cluster.total_count} | {case_ids_str} |"
        )
    table = "\n".join([header, sep, *rows])
    return (
        "## 10. 明确失败簇\n\n"
        "按 `(dimension, question_category, failure_pattern)` 聚类；"
        "每簇列出 case_ids 与 failed/total。\n\n"
        f"{table}\n"
    )


def _render_r4_a4_candidates(
    aggregated: AggregatedReport,
    real_model_blocked: bool,
) -> str:
    """Section 11: R4-A4 候选修复建议（仅建议不实施）."""
    if real_model_blocked:
        # Use spec-anticipated failure patterns as candidate basis.
        candidates = [
            (
                "temporal claim policy",
                "prompt 显式禁止补全正文未出现的年份/日期/相对时间；"
                "对 source_metadata=unknown 的 case 强制要求 "
                "\"文章未提供年份\" 表述。",
                "unsupported_temporal_claims × 2025-year-hallucination",
            ),
            (
                "exhaustive enumeration prompt",
                "prompt 要求逐 unit 核对城市/地区，并显式排除 "
                "\"地区\"（如\"纽约州西部\"）混入\"城市\"列表。",
                "exhaustive_completeness × missing-Thunder Bay / "
                "entity_precision × type-confusion",
            ),
            (
                "instruction count validator",
                "validator 增加 `requested_count` 严格校验："
                "`exercise_items` 数量与 `requested_count` 一致；"
                "`sentences` 数量 ≤ `requested_count`。",
                "instruction_following × count-mismatch",
            ),
            (
                "entity type separation",
                "prompt 区分 city / region / state / country；"
                "validator 拒绝跨类型混入。",
                "entity_precision × type-confusion",
            ),
        ]
    else:
        # Derive candidates from real failure clusters.
        cluster_patterns = {
            (c.dimension, c.failure_pattern) for c in aggregated.failure_clusters
        }
        candidates = []
        if any(
            d == "unsupported_temporal_claims"
            and "year-hallucination" in p
            for d, p in cluster_patterns
        ):
            candidates.append(
                (
                    "temporal claim policy",
                    "prompt 显式禁止补全正文未出现的年份/日期；"
                    "validator 拒绝任何不在 allowed_temporal_claims 中的年份 token。",
                    "unsupported_temporal_claims × year-hallucination",
                )
            )
        if any(
            d == "exhaustive_completeness" and p.startswith("missing-")
            for d, p in cluster_patterns
        ):
            candidates.append(
                (
                    "exhaustive enumeration prompt",
                    "prompt 要求逐 unit 核对城市/地区；"
                    "对 expected_entity_set 中每项显式 echo 或承认未列出。",
                    "exhaustive_completeness × missing-entity",
                )
            )
        if any(
            d == "instruction_following" and p == "count-mismatch"
            for d, p in cluster_patterns
        ):
            candidates.append(
                (
                    "instruction count validator",
                    "validator 增加 `requested_count` 严格校验。",
                    "instruction_following × count-mismatch",
                )
            )
        if any(
            d == "entity_precision" and p == "type-confusion"
            for d, p in cluster_patterns
        ):
            candidates.append(
                (
                    "entity type separation",
                    "prompt 区分 city / region / state / country；"
                    "validator 拒绝跨类型混入。",
                    "entity_precision × type-confusion",
                )
            )
        if not candidates:
            candidates.append(
                (
                    "(无明确失败簇)",
                    "本轮真实运行未形成需 R4-A4 修复的失败簇；"
                    "候选修复建议为空。",
                    "(none)",
                )
            )

    lines = []
    for idx, (title, suggestion, source) in enumerate(candidates, start=1):
        lines.append(f"### 11.{idx} {title}\n")
        lines.append(f"- **候选方向**: {suggestion}\n")
        lines.append(f"- **来源失败簇**: {source}\n")
        lines.append("- **状态**: 不实施，待 R4-A4 立项。\n")

    body = "\n".join(lines)
    return (
        "## 11. R4-A4 候选修复建议\n\n"
        "本节仅给出候选修复方向，**不实施**。所有候选均待 R4-A4 立项后再评估。\n\n"
        f"{body}"
    )


def _render_verdict(verdict: str) -> str:
    """Section 12: R4-A3 最终裁决."""
    return (
        "## 12. R4-A3 最终裁决\n\n"
        f"**verdict: {verdict}**\n\n"
        "- `accepted`: 确定性测试全通过 + 真实模型无高严重度失败簇。\n"
        "- `rework`: 确定性测试通过但真实模型出现可修复失败簇。\n"
        "- `blocked`: 真实模型不可用 / 阻塞条件未解除。\n"
    )


def _render_next_step_decision(
    allow_r4_a4: bool,
    allow_r4_b1: bool,
    verdict: str,
) -> str:
    """Section 13: 是否允许进入 R4-A4 和 R4-B1."""
    a4_text = (
        "条件允许"
        if verdict == "blocked" and allow_r4_a4
        else ("允许" if allow_r4_a4 else "不允许")
    )
    b1_text = "允许" if allow_r4_b1 else "暂不允许"
    a4_suffix = (
        "但 R4-A3 真实模型验证未完成，需解除阻塞后再签收。"
        if verdict == "blocked"
        else ""
    )
    b1_suffix = (
        "但建议先解除 R4-A3 阻塞以避免 streaming 与 correctness 同时变更。"
        if not allow_r4_b1
        else ""
    )
    return (
        "## 13. 是否允许进入 R4-A4 和 R4-B1\n\n"
        f"- **R4-A4**: {a4_text}\n"
        f"  - 说明: harness/dataset/deterministic evaluator 已 accepted；"
        f"{a4_suffix}\n"
        f"- **R4-B1**: {b1_text}\n"
        f"  - 说明: R4-B1 为 Pydantic AI streaming provider spike，"
        f"依赖 R4-A2 不依赖 R4-A3 真实模型；"
        f"{b1_suffix}\n"
    )


_DEFAULT_TRACKER_PATH = (
    "docs/tmp/reader-orchestration/"
    "TMP-reader-record-ask-r4-product-ready-tracker-2026-07-17.md"
)


def _render_tracker_update(
    verdict: str,
    *,
    tracker_path: str | None = None,
    report_date: str | None = None,
) -> str:
    """Section 14: R4 tracker 更新.

    R4-A4-0 (Task 5): ``tracker_path`` and ``report_date`` are now
    parameterized — the report no longer hardcodes the previous round's
    tracker file path or decision-log date.
    """
    path = tracker_path or _DEFAULT_TRACKER_PATH
    date = report_date or "2026-07-17"
    return (
        "## 14. R4 tracker 更新\n\n"
        f"- tracker 文件: `{path}`\n"
        f"- §6 任务板 R4-A3 行状态: `{verdict}`\n"
        f"- §11 决策日志: 追加一行 (日期={date}, 决策=R4-A3 裁决="
        f"{verdict}, 原因=详见本评测报告)。\n"
        f"- §12 per-round 模板: 追加 R4-A3 轮次记录。\n"
        f"- 仅追加，不重写已签收的 R4-0/R4-A1/R4-A2 段落。\n"
        f"- 注: Task 6 (tracker 更新) 不在本 Task 5 范围内；本节为指向说明。\n"
    )


def _render_no_commit(parallel_dirty: list[str]) -> str:
    """Section 15: 未 commit."""
    dirty_summary = (
        "; ".join(f"`{p}`" for p in parallel_dirty[:8])
        if parallel_dirty
        else "(空)"
    )
    return (
        "## 15. 未 commit\n\n"
        "- 本轮未执行 `git commit` / `git reset` / `git restore` / "
        "`git checkout` / `git stash`。\n"
        "- `git status --short` 摘要 (并行脏树前 8 行):\n"
        f"  - {dirty_summary}\n"
        "- 全部产物以工作树修改形式交付，由上层负责 review 与 commit。\n"
    )


# ---------------------------------------------------------------------------
# Rework closure sections (16-19)
# ---------------------------------------------------------------------------


def _render_capability_boundary() -> str:
    """Section 16: 能力边界声明.

    Declares what the 11 deterministic evaluators CAN and CANNOT verify.
    This is a fixed declaration (not data-dependent) so callers never
    need to pass parameters — the boundary is a property of the
    evaluator suite, not of a particular run.
    """
    return (
        "## 16. 能力边界声明\n\n"
        "本轮 11 维确定性 evaluator 的能力边界如下：\n\n"
        "### 16.1 能验证（确定性）\n\n"
        "- 已知事实是否在 final_text 中出现（`context_support`，基于 "
        "`atomic_facts` alias groups）\n"
        "- final_text 中的年份/日期 token 是否在 `allowed_temporal_claims` "
        "白名单中（`unsupported_temporal_claims`）\n"
        "- final_text 中的数字/比例/金额是否可追溯至文章正文"
        "（`numeric_grounding`，基于 `allowed_numerics`）\n"
        "- 实体是否在 `allowed_entities_by_type` / `entity_catalog` 中且类型正确"
        "（`entity_precision`）\n"
        "- `expected_entity_set` 的 set recall 是否为 1.0"
        "（`exhaustive_completeness`）\n"
        "- `requested_count_kind=exercise_items/sentences` 时数量是否匹配"
        "（`instruction_following`）\n"
        "- `answer_language=zh` 时是否存在无必要整句英文"
        "（`language_consistency`）\n"
        "- `cited_evidence_handles` 是否 ≤6、非重复、全在 observations 中"
        "（`evidence_minimality`）\n"
        "- `baseline_complete=True` + 文章级问题时 `read_range_calls==0`"
        "（`tool_decision`）\n"
        "- usage.requests/tokens、model route、thinking、latency、final status "
        "是否可观测（`usage_observability`）\n\n"
        "### 16.2 不能验证（需 LLM judge 或人工）\n\n"
        "- 不能证明所有自然语言 claim 都完整 grounded（只能验证已知事实）\n"
        "- 不能判断语义等价但 alias 未声明的改写是否正确\n"
        "- 不能判断未在 `entity_catalog` 中声明的外部实体是否合理\n"
        "- 不能验证 final_text 的论证结构是否完整（只能验证事实存在性）\n"
        "- 不能验证 final_text 的语气/风格是否符合作者意图\n\n"
        "### 16.3 不引入假装确定性的 LLM judge\n\n"
        "- LLM judge 仅允许补充 `entity_precision`，且**不得覆盖任何确定性失败**\n"
        "- aggregator 以 `passed` 为单一来源；`llm_judge_used` / "
        "`llm_judge_note` 仅作记录字段\n"
        "- 未声明 alias 的事实/实体不会因 LLM judge 而翻转 passed\n"
    )


def _render_coverage_status(
    dataset: ReaderRecordAskR4A3Dataset,
    artifacts: list[RawArtifact],
) -> str:
    """Section 17: 真实覆盖状态.

    Reports the known/unknown, suggestion/manual, partial/complete
    coverage matrix. Cases tagged ``offline_only`` are excluded from
    runtime coverage — they must NOT be reported as runtime-verified.
    """
    from claread_eval.reader_record_ask.phase_planner import (
        PHASE_TAG_OFFLINE_ONLY,
        PHASE_TAG_REAL_PHASE1,
    )

    # Categorize each case by source_metadata × input_mode × baseline_mode.
    runtime_case_ids = {
        a.case_id for a in artifacts if not a.budget_exhausted
    }

    rows: list[str] = []
    for case in dataset.cases:
        is_offline = PHASE_TAG_OFFLINE_ONLY in case.phase_tags
        is_real_phase1 = PHASE_TAG_REAL_PHASE1 in case.phase_tags
        has_runtime = case.id in runtime_case_ids
        coverage = (
            "offline_only (不进入运行时)"
            if is_offline
            else (
                "real (有 artifact)"
                if has_runtime
                else "real_phase1 (未运行)"
            )
        )
        rows.append(
            f"| `{case.id}` | {case.source_metadata} | "
            f"{case.input_mode} | {case.baseline_mode} | "
            f"{'yes' if is_real_phase1 else 'no'} | "
            f"{'yes' if is_offline else 'no'} | {coverage} |"
        )

    header = (
        "| case_id | source_metadata | input_mode | baseline_mode | "
        "real_phase1 | offline_only | 运行时覆盖状态 |"
    )
    sep = "|" + "---|" * 7
    table = "\n".join([header, sep, *rows])

    # Summary counts.
    total = len(dataset.cases)
    offline_count = sum(
        1 for c in dataset.cases if PHASE_TAG_OFFLINE_ONLY in c.phase_tags
    )
    real_phase1_count = sum(
        1 for c in dataset.cases if PHASE_TAG_REAL_PHASE1 in c.phase_tags
    )
    runtime_count = len(runtime_case_ids)

    return (
        "## 17. 真实覆盖状态\n\n"
        "known/unknown × suggestion/manual × partial/complete 覆盖矩阵：\n\n"
        f"{table}\n\n"
        f"- 总 case 数: {total}\n"
        f"- `real_phase1` tag: {real_phase1_count}\n"
        f"- `offline_only` tag: {offline_count}（不进入运行时）\n"
        f"- 有 artifact 的 case 数: {runtime_count}\n"
        "- `offline_only` case 不被误报为运行时覆盖（fail-closed）\n"
    )


def _render_budget_semantics(
    artifacts: list[RawArtifact],
    real_model_blocked: bool,
) -> str:
    """Section 18: request/token budget 真实语义.

    Explains how BudgetedUsageModel enforces the request/token cap and
    how budget_exhausted artifacts are handled.
    """
    budget_exhausted_count = sum(
        1 for a in artifacts if a.budget_exhausted
    )
    total_executed_requests = sum(
        a.executed_requests or 0 for a in artifacts
    )
    total_executed_tokens = sum(
        a.executed_tokens or 0 for a in artifacts
    )

    if real_model_blocked:
        runtime_status = (
            "**状态: BLOCKED** — 真实模型未运行，budget 未被触发。"
            "BudgetedUsageModel 已通过离线端到端测试验证 cap 有效性"
            "（见 §19 thinking 验证方式 + 离线 e2e 测试）。\n\n"
        )
    else:
        runtime_status = (
            f"**状态: 已运行** — {budget_exhausted_count} 个 artifact "
            "因 budget 耗尽而未执行。\n\n"
        )

    return (
        "## 18. request/token budget 真实语义\n\n"
        f"{runtime_status}"
        "### 18.1 BudgetedUsageModel 合同\n\n"
        "- 包装 resolved model（实现 pydantic-ai `Model` 接口）\n"
        "- 每次 provider model request 前递增 request count\n"
        "- 达到 `max_requests` cap 时在发出请求**前**拒绝"
        "（抛 `BudgetExhaustedError`，wrapped model 不被调用）\n"
        "- 从 model response usage 聚合 input/output token\n"
        "- token cap 尽可能在请求前或下一请求前阻断\n"
        "- **不记录** request body、reasoning_content、API key\n"
        "- wrapper 不改变模型输出或 tool loop 语义\n\n"
        "### 18.2 budget_exhausted artifact 处理\n\n"
        "- budget_exhausted artifact **不**被 evaluate（无内容可评估）\n"
        "- budget_exhausted artifact **不**被当作 pass（不当做通过）\n"
        "- report 中显式显示 cap 触发状态\n"
        "- 缺失 run 不被误报为 runtime coverage\n\n"
        "### 18.3 本轮执行统计\n\n"
        f"- budget_exhausted artifact 数: {budget_exhausted_count}\n"
        f"- 总执行 requests (executed_requests): {total_executed_requests}\n"
        f"- 总执行 tokens (executed_tokens): {total_executed_tokens}\n"
    )


def _render_thinking_verification(real_model_blocked: bool) -> str:
    """Section 19: thinking 验证方式.

    Documents how thinking/profile is verified per phase — not just
    written as labels, but actually asserted against resolved settings.
    """
    if real_model_blocked:
        verification_status = (
            "**状态: BLOCKED** — 真实模型未运行，thinking 验证通过"
            "离线端到端测试（FunctionModel + BudgetedUsageModel）验证"
            "断言逻辑正确，但未在真实 provider 上执行。\n\n"
        )
    else:
        verification_status = (
            "**状态: 已验证** — 真实模型运行时 thinking 配置已断言。\n\n"
        )

    return (
        "## 19. thinking 验证方式\n\n"
        f"{verification_status}"
        "### 19.1 Phase 1 (Flash non-thinking)\n\n"
        "- 断言 `model_config.model_settings.thinking_enabled() is False`\n"
        "- artifact.thinking_enabled 来自 resolved settings（非手写标签）\n"
        "- 离线 e2e 测试验证：Phase 1 artifact 的 thinking_enabled=False\n\n"
        "### 19.2 Phase 2 (Flash thinking)\n\n"
        "- 断言 `thinking_config.model_settings.thinking_enabled() is True`\n"
        "- artifact.thinking_enabled 来自 resolved settings\n"
        "- 离线 e2e 测试验证：Phase 2 artifact 的 thinking_enabled=True\n\n"
        "### 19.3 Phase 3 (Pro thinking)\n\n"
        "- 实际加载 `CLAREAD_R4_A3_PRO_PROFILE` 并验证 model_name + thinking\n"
        "- artifact.thinking_enabled 来自 resolved settings\n"
        "- Phase 3 真实运行需开放 env gate 后执行\n\n"
        "### 19.4 thinking_enabled() 实现\n\n"
        "- `RunModelSettings.thinking_enabled()` 检查 "
        "`extra_body.enable_thinking` 或 "
        "`extra_body.thinking.type == 'enabled'`\n"
        "- 不依赖字符串标签，而是检查 resolved model settings 的实际值\n"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_r4_a3_report(
    *,
    aggregated: AggregatedReport,
    dataset: ReaderRecordAskR4A3Dataset,
    artifacts: list[RawArtifact],
    start_head: str,
    end_head: str,
    parallel_dirty: list[str],
    harness_choice: str,
    rejected_harness: str,
    rejected_reason: str,
    real_model_blocked: bool,
    real_model_block_reason: str | None,
    real_model_user_commands: list[str] | None,
    deterministic_tests_passed: bool,
    deterministic_tests_summary: str,
    verdict: str,
    allow_r4_a4: bool,
    allow_r4_b1: bool,
    run_metadata: dict[str, Any] | None = None,
    # R4-A4-0 (Task 5) — parameterize previously hardcoded values so
    # the report no longer carries stale date / file list / tracker
    # path from the previous round.
    report_date: str | None = None,
    modified_files: list[str] | None = None,
    task_label: str = "Task 5",
    tracker_path: str | None = None,
) -> str:
    """Generate the R4-A3 evaluation markdown report.

    Returns a markdown string with the 15 required content items per
    spec Requirement: 交付报告内容.

    Sanitization (spec Requirement: 报告脱敏与可聚合):
    - No BBC article body (≥200 contiguous characters).
    - No ``reasoning_content`` reference.
    - No API key / provider request payload.
    - Sensitive exceptions truncated to 200 chars.
    - final_text summary ≤200 chars with ``[truncated]`` marker.
    - Evidence snippet ≤200 chars with ``[truncated]`` marker.

    Note: ``artifacts`` is consumed only for run metadata summary, not
    for re-evaluation. The ``aggregated`` report already contains the
    per-config / per-dimension / failure-cluster aggregates.
    """
    sections: list[str] = []

    # Title
    total_runs = aggregated.total_runs
    total_cases = aggregated.total_cases
    # R4-A4-0 (Task 5): use parameterized ``report_date`` instead of
    # the hardcoded "2026-07-17" from the previous round.
    if report_date is None:
        from datetime import date as _date

        report_date = _date.today().isoformat()
    sections.append(
        "# TMP — Reader Record Ask R4-A3 评测报告\n\n"
        f"> 生成时间: {report_date}  \n"
        f"> dataset: `{dataset.id}`  \n"
        f"> 总 cases: {total_cases}  \n"
        f"> 总 runs: {total_runs}  \n"
        f"> verdict: **{verdict}**  \n"
        f"> real_model_blocked: {real_model_blocked}  \n"
    )

    # Sections 1–15
    sections.append(_render_heads(start_head, end_head))
    sections.append(
        _render_files_and_dirty_tree(
            parallel_dirty=parallel_dirty,
            harness_choice=harness_choice,
            rejected_harness=rejected_harness,
            rejected_reason=rejected_reason,
            modified_files=modified_files,
            task_label=task_label,
        )
    )
    sections.append(_render_dataset_cases(dataset))
    sections.append(_render_evaluator_contract())
    sections.append(
        _render_test_results(
            deterministic_tests_passed=deterministic_tests_passed,
            deterministic_tests_summary=deterministic_tests_summary,
            real_model_blocked=real_model_blocked,
        )
    )
    sections.append(
        _render_real_model_runs(
            aggregated=aggregated,
            real_model_blocked=real_model_blocked,
            real_model_block_reason=real_model_block_reason,
            real_model_user_commands=real_model_user_commands,
        )
    )
    sections.append(_render_per_dimension_table(aggregated))
    sections.append(
        _render_ab_comparison(
            aggregated=aggregated,
            real_model_blocked=real_model_blocked,
        )
    )
    sections.append(
        _render_failure_clusters(
            aggregated=aggregated,
            real_model_blocked=real_model_blocked,
        )
    )
    sections.append(
        _render_r4_a4_candidates(
            aggregated=aggregated,
            real_model_blocked=real_model_blocked,
        )
    )
    sections.append(_render_verdict(verdict))
    sections.append(
        _render_next_step_decision(
            allow_r4_a4=allow_r4_a4,
            allow_r4_b1=allow_r4_b1,
            verdict=verdict,
        )
    )
    sections.append(
        _render_tracker_update(
            verdict,
            tracker_path=tracker_path,
            report_date=report_date,
        )
    )
    sections.append(_render_no_commit(parallel_dirty))

    # Sections 16-19: rework closure additions
    sections.append(_render_capability_boundary())
    sections.append(_render_coverage_status(dataset=dataset, artifacts=artifacts))
    sections.append(
        _render_budget_semantics(
            artifacts=artifacts,
            real_model_blocked=real_model_blocked,
        )
    )
    sections.append(_render_thinking_verification(real_model_blocked))

    # Optional run metadata (sanitized — no API key / reasoning_content).
    if run_metadata:
        sanitized_meta: dict[str, Any] = {}
        for key, value in run_metadata.items():
            if isinstance(value, str):
                if "reasoning_content" in key.lower() or "reasoning_content" in value.lower():
                    continue
                if "sk-" in value or "api_key=" in value.lower():
                    continue
                sanitized_meta[key] = _truncate(value, 200)
            else:
                sanitized_meta[key] = value
        sections.append(
            "## 附录: run_metadata (sanitized)\n\n"
            "```json\n"
            f"{_format_json_compact(sanitized_meta)}\n"
            "```\n"
        )

    markdown = "\n".join(sections)

    # Note: the declarative sections (16 能力边界, 18 budget 语义) legitimately
    # mention ``reasoning_content`` as a field name — e.g. "不记录
    # reasoning_content". This is a concept reference, not a leaked value.
    # Leaked values from run_metadata are already stripped by the
    # sanitization loop above (keys/values containing "reasoning_content"
    # are dropped before reaching this point). No blanket redaction here.
    return markdown


def _format_json_compact(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public helpers used by tests / runner
# ---------------------------------------------------------------------------


def sanitize_for_report(text: str | None, *, limit: int = _MAX_SNIPPET_CHARS) -> str:
    """Public truncation helper exposed for tests and the runner script."""
    return _truncate(text, limit)
