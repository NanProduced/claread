# task-history: TEST-GOVERNANCE-GATE-A-SAFE-REBUILD-R1 / G0
"""Syntax-aware task-history naming governance for tracked Claread sources.

The guard deliberately has no residual ceiling. During the rolling cleanup its
repository-wide assertion remains RED; focused self-tests and changed-scope
checks must be GREEN. Once every accepted item is removed, the same assertion
becomes the permanent backflow guard.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest

pytestmark = (
    [
        pytest.mark.chain_infra,
        pytest.mark.seam_pure_unit,
        pytest.mark.life_permanent_regression,
    ]
    if __name__ != "claread_task_number_naming_guard"
    else []
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_ROOTS = (
    "services/api",
    "evals",
    "apps/web",
    "apps/miniprogram",
    "apps/directus",
    "packages",
    "infra",
)
CONFIG_SUFFIXES = {
    ".cfg",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".rst",
    ".scss",
    ".sql",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
GUARD_PATHS = {
    "apps/web/src/lib/reader-orchestration/task-number-naming-guard.test.ts",
    "evals/tests/test_task_number_naming_guard.py",
    "services/api/tests/test_task_number_naming_guard.py",
}

# Only the exact frozen protocol/data token is removed before rescanning the
# remainder of the same syntax item.
KEEP_WIRE_TOKENS = (
    "ask_retry_contract_r5",
    "CLAREAD_R4_A3_BBC_RECORD_ID",
    "CLAREAD_R4_A3_DATASET_DIR",
    "CLAREAD_R4_A3_MAX_REQUESTS",
    "CLAREAD_R4_A3_MAX_TOKENS",
    "CLAREAD_R4_A3_PRIOR_RUN_ID",
    "CLAREAD_R4_A3_PRO_PROFILE",
    "CLAREAD_R4_A3_RUN",
    "CLAREAD_R4_A3_RUN_ID",
    "CLAREAD_R4_A3_RUNS_DIR",
    "CLAREAD_R4_A3_THINKING_VIA_PROFILE",
    "d4-p1-translation-worker",
    "d4-p2-translation-parsed",
    "d5-v3-vocabulary-worker",
    "d5-v6-grammar-worker",
    "d6_i3b_structured_source_v1",
    "d6_i3b_structured_source_v2",
    "full_snapshot_until_pux_r4",
    "r4-a3-dataset-v1",
    "r4-a4-2r2",
    "r4-a4-2r3",
    "reader_d5_attribution_schema_drift",
    "reader_d6_anchor_migration_missing",
    "reader-record-ask-r4-a3",
    "reading_base_builder_d3_p2_v1",
    "t1-1-translation-batch-worker",
    "t1-1-vocabulary-batch-worker",
    "zplus_grammar_bundle_v1",
)
_KEEP_FIXTURE_TOKENS = (
    "d6_i3b_plain_text_markdown_v1",
    "r14_complex",
)

_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:Task|Phase|Wave)\s+\d+(?:\.\d+)?[A-Za-z]?|"
    r"(?<![A-Za-z0-9])(?:"
    r"R\d+(?:[._-][A-Za-z0-9]+)*|"
    r"D\d+(?:[._-][A-Za-z0-9]+)*|"
    r"P\d+[A-Z]?(?:[._-][A-Za-z0-9]+)*|"
    r"T\d+(?:\.\d+)?[a-z]?(?:[._-][A-Za-z0-9]+)*|"
    r"A\d+(?:[._-][A-Za-z0-9]+)*|"
    r"B\d+(?:[._-][A-Za-z0-9]+)*|"
    r"C[123]|"
    r"S\d+(?:\.\d+)?|U\d+)"
    r"(?![A-Za-z0-9])"
)
_MACHINE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:d\d+[-_]i\d+[a-z]?|d\d+[-_][pv]\d+|"
    r"r\d+[-_][ab]\d+|t\d+[-_]\d+)"
    r"(?:[-_][a-z0-9]+)+(?![A-Za-z0-9_])"
)
_SPECIAL_IDENTIFIER_RE = re.compile(
    r"(?:ReaderRecordAskR4A3|load_r4_a3_dataset|allow_r4_a4|"
    r"allow_r4_b1|task_label|_P1(?:D|F|G)(?:_R1)?_)"
)
_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
_ISSUE_RE = re.compile(r"#\d+\b")
_CEFR_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:A1|A2|B1|B2|C1|C2)(?![A-Za-z0-9])"
)
_ARTICLE_RAG_RE = re.compile(r"(?<![A-Za-z0-9])B[123](?![A-Za-z0-9])")
_SEGMENT_TOKEN_RE = re.compile(
    r"(?<=[_-])(?:A|B|C|D|P|R|S|T|U)\d{1,3}(?:\.\d+)?[A-Za-z]?"
)
_D3_READING_BASE_RE = re.compile(r"(?<![a-z0-9])d3-p[14](?![a-z0-9])")
_TRACKED_FILENAME_RE = re.compile(r"(?:^|_)d\d+(?:_|\.|$)", re.I)

# Accepted semantic KEEP ratchet. Keys are path + syntax kind + exact token;
# values are frozen occurrence counts. This is not a residual baseline ceiling.
ACCEPTED_SEMANTIC_KEEP_COUNTS = {
    ("apps/miniprogram/src/app.scss", "config_or_fixture_line", "B45309"): 2,
    ("apps/miniprogram/src/app.scss", "config_or_fixture_line", "D97706"): 1,
    ("apps/miniprogram/src/packageA/credit-detail/index.scss", "config_or_fixture_line", "D97706"): 1,
    ("apps/miniprogram/src/packageB/daily-reader/index.scss", "config_or_fixture_line", "A66445"): 2,
    ("apps/miniprogram/src/packageC/feedback/index.scss", "config_or_fixture_line", "B45309"): 1,
    ("apps/miniprogram/src/packageC/feedback/index.scss", "config_or_fixture_line", "D97706"): 3,
    ("apps/miniprogram/src/packageC/feedback/my-feedback.scss", "config_or_fixture_line", "B45309"): 1,
    ("infra/migrations/0001_initial.sql", "config_or_fixture_line", "A2"): 1,
    ("infra/migrations/0001_initial.sql", "config_or_fixture_line", "B1"): 1,
    ("infra/migrations/0001_initial.sql", "config_or_fixture_line", "B2"): 1,
    ("infra/migrations/0001_initial.sql", "config_or_fixture_line", "C1"): 1,
    ("services/api/app/services/daily_reader/scoring.py", "python_string_literal", "A2"): 2,
    ("services/api/app/services/daily_reader/scoring.py", "python_string_literal", "B1"): 3,
    ("services/api/app/services/daily_reader/scoring.py", "python_string_literal", "B2"): 3,
    ("services/api/app/services/daily_reader/scoring.py", "python_string_literal", "C1"): 3,
    ("services/api/app/services/reader_orchestration/text_artifact_extraction_provider.py", "python_docstring", "S3"): 1,
    ("services/api/app/services/reader_record_ask/turn_coordinator.py", "python_string_literal", "U00020000"): 1,
    ("services/api/prompts/agents/daily_vocab.yaml", "config_or_fixture_line", "A1-A2"): 1,
    ("services/api/prompts/agents/daily_vocab.yaml", "config_or_fixture_line", "B1-C1"): 1,
    ("services/api/prompts/agents/daily_vocab.yaml", "config_or_fixture_line", "C1"): 1,
    ("services/api/prompts/policies/daily.yaml", "config_or_fixture_line", "A1-A2"): 1,
    ("services/api/prompts/policies/daily.yaml", "config_or_fixture_line", "B1-C1"): 1,
    ("services/api/prompts/policies/daily.yaml", "config_or_fixture_line", "C1"): 1,
    ("services/api/tests/fixtures/markdown_structured_source/CONTRACT.md", "config_or_fixture_line", "d6_i3b_plain_text_markdown_v1"): 3,
    ("services/api/tests/fixtures/markdown_structured_source/CONTRACT.md", "config_or_fixture_line", "r14_complex"): 1,
    ("services/api/tests/fixtures/markdown_structured_source/r14_complex/expected_blocks.json", "config_or_fixture_line", "r14_complex"): 1,
    ("services/api/tests/fixtures/markdown_structured_source/r14_complex/expected_diagnostics.json", "config_or_fixture_line", "r14_complex"): 1,
    ("services/api/tests/fixtures/markdown_structured_source/r14_complex/expected_policy.json", "config_or_fixture_line", "r14_complex"): 1,
    ("services/api/tests/reader_stable_order_real_product_fixture.py", "python_identifier", "U3"): 4,
    ("services/api/tests/services/reader_record_ask/thread_memory/test_emergency.py", "python_string_literal", "A1"): 4,
    ("services/api/tests/services/reader_record_ask/thread_memory/test_emergency.py", "python_string_literal", "A2"): 3,
    ("services/api/tests/services/reader_record_ask/thread_memory/test_real_shape_regression.py", "python_string_literal", "P0-1"): 1,
    ("services/api/tests/services/reader_record_ask/thread_memory/test_real_shape_regression.py", "python_string_literal", "R1.6.1"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "A1"): 8,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "A2"): 8,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "A3"): 8,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "B1"): 5,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "B2"): 5,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "B3"): 5,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "C1"): 4,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "C2"): 4,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "C3"): 3,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "D1"): 2,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "D2"): 7,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "D3"): 6,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_comment", "U1"): 11,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_docstring", "D2"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_docstring", "D3"): 3,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "A1-aaaa"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "A2-aaaa"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "A3-aaaa"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "B1-bbbb"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "B2-bbbb"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "B3-leftover"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "C1"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "C2"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "C3"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "D1-dddd"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "D2-dddd"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "D3-dddd"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "D4"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "R2"): 1,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "R3"): 2,
    ("services/api/tests/test_article_rag_single_path_real_acceptance.py", "python_string_literal", "U1"): 1,
    ("services/api/tests/test_artifact_pipeline_worker_service.py", "python_string_literal", "d3-p1-builder"): 1,
    ("services/api/tests/test_artifact_pipeline_worker_service.py", "python_string_literal", "d3-p1-canonicalizer"): 1,
    ("services/api/tests/test_artifact_pipeline_worker_service.py", "python_string_literal", "d3-p1-segmenter"): 1,
    ("services/api/tests/test_candidate_document_freeze_plan.py", "python_string_literal", "D6-I1"): 1,
    ("services/api/tests/test_candidate_routing_distribution.py", "python_string_literal", "r14_complex"): 1,
    ("services/api/tests/test_completion_finalizer.py", "python_string_literal", "T35"): 1,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "B1"): 1,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "B2"): 4,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "C1"): 1,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "S0"): 4,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "S1"): 2,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "S2"): 1,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "T0"): 4,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "T1"): 2,
    ("services/api/tests/test_daily_reader_structure.py", "python_string_literal", "T2"): 1,
    ("services/api/tests/test_extracted_artifact_materialization_service.py", "python_string_literal", "d3-p1-builder"): 1,
    ("services/api/tests/test_extracted_artifact_materialization_service.py", "python_string_literal", "d3-p1-canonicalizer"): 1,
    ("services/api/tests/test_extracted_artifact_materialization_service.py", "python_string_literal", "d3-p1-segmenter"): 1,
    ("services/api/tests/test_markdown_safe_normalization.py", "python_string_literal", "r14_complex"): 1,
    ("services/api/tests/test_markdown_source_parser.py", "python_string_literal", "r14_complex"): 2,
    ("services/api/tests/test_materialization_job_runtime.py", "python_string_literal", "d3-p1-builder"): 1,
    ("services/api/tests/test_materialization_job_runtime.py", "python_string_literal", "d3-p1-canonicalizer"): 1,
    ("services/api/tests/test_materialization_job_runtime.py", "python_string_literal", "d3-p1-segmenter"): 1,
    ("services/api/tests/test_reader_orchestration_article_ready_service.py", "python_string_literal", "d3-p3-builder"): 1,
    ("services/api/tests/test_reader_orchestration_article_ready_service.py", "python_string_literal", "d3-p3-canonicalizer"): 1,
    ("services/api/tests/test_reader_orchestration_article_ready_service.py", "python_string_literal", "d3-p3-segmenter"): 1,
    ("services/api/tests/test_reader_orchestration_grammar_worker.py", "python_string_literal", "T4.1c"): 2,
    ("services/api/tests/test_reader_orchestration_job_runtime.py", "python_string_literal", "d3-p4-builder"): 1,
    ("services/api/tests/test_reader_orchestration_job_runtime.py", "python_string_literal", "d3-p4-canonicalizer"): 1,
    ("services/api/tests/test_reader_orchestration_job_runtime.py", "python_string_literal", "d3-p4-segmenter"): 1,
    ("services/api/tests/test_reader_orchestration_pipeline_runner.py", "python_string_literal", "T3.1"): 1,
    ("services/api/tests/test_reader_orchestration_pipeline_runner.py", "python_string_literal", "T3.2b"): 1,
    ("services/api/tests/test_reader_orchestration_pipeline_runner.py", "python_string_literal", "T4.1c"): 4,
    ("services/api/tests/test_reader_orchestration_schema_baseline.py", "python_string_literal", "d3-p1-builder"): 3,
    ("services/api/tests/test_reader_orchestration_schema_baseline.py", "python_string_literal", "d3-p1-canonicalizer"): 3,
    ("services/api/tests/test_reader_orchestration_schema_baseline.py", "python_string_literal", "d3-p1-segmenter"): 3,
    ("services/api/tests/test_reader_orchestration_sentence_segmentation.py", "python_string_literal", "R7-1"): 1,
    ("services/api/tests/test_reader_orchestration_translation_worker.py", "python_string_literal", "d4-p1-test"): 1,
    ("services/api/tests/test_reader_parse_eval.py", "python_string_literal", "P1-6"): 1,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "P0-1"): 3,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "P0-3"): 2,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "P0-Identity"): 5,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "r4_a3_eval"): 2,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R"): 2,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R2"): 2,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R3"): 6,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R5"): 6,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R5R"): 1,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R5R2"): 10,
    ("services/api/tests/test_reader_record_ask_real_llm_eval.py", "python_string_literal", "R4-A4-2R5R3"): 4,
    ("services/api/tests/test_reader_record_ask_turn_coordinator_map_source.py", "python_string_literal", "D1"): 1,
    ("services/api/tests/test_reader_section_seams.py", "python_string_literal", "d3-p4-builder"): 1,
    ("services/api/tests/test_reader_section_seams.py", "python_string_literal", "d3-p4-canonicalizer"): 1,
    ("services/api/tests/test_reader_section_seams.py", "python_string_literal", "d3-p4-segmenter"): 1,
    ("services/api/tests/test_semantic_outline_content_sufficiency.py", "python_string_literal", "A6"): 1,
    ("services/api/tests/test_tecd3_import.py", "python_string_literal", "A1"): 14,
    ("services/api/tests/test_vocabulary_highlight_single_item_guard.py", "python_string_literal", "R7-2"): 1,
}


@dataclass(frozen=True)
class SyntaxItem:
    path: str
    kind: str
    line: int
    text: str
    purpose: str = ""


@dataclass(frozen=True)
class GuardHit:
    path: str
    kind: str
    line: int
    token: str
    purpose: str


def tracked_paths() -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", *EXPECTED_ROOTS],
        check=True,
        capture_output=True,
    )
    return tuple(
        path
        for path in completed.stdout.decode("utf-8").split("\0")
        if path
    )

def _strip_exact(text: str, tokens: Iterable[str]) -> str:
    remainder = text
    for token in tokens:
        remainder = remainder.replace(token, "")
    return remainder


def _strip_contextual_keeps(text: str, purpose: str) -> str:
    remainder = _HEX_COLOR_RE.sub("", text)
    remainder = _ISSUE_RE.sub("", remainder)
    remainder = _strip_exact(remainder, KEEP_WIRE_TOKENS)
    remainder = _strip_exact(remainder, _KEEP_FIXTURE_TOKENS)

    context = f"{text} {purpose}"
    if re.search(r"\b(?:CEFR|difficulty|reading level)\b", context, re.I):
        remainder = _CEFR_RE.sub("", remainder)
    if re.search(r"\bArticle RAG\b", context, re.I):
        remainder = _ARTICLE_RAG_RE.sub("", remainder)
    if re.search(
        r"(?:SEGMENT_ID|segment_id|unit_id|sentence_id|summary_id|"
        r"chunk_id|block_id|fixture_identity)",
        context,
        re.I,
    ):
        remainder = _SEGMENT_TOKEN_RE.sub("", remainder)
    if "reading_bases" in context:
        remainder = _D3_READING_BASE_RE.sub("", remainder)
    return remainder


def task_tokens(text: str, *, purpose: str = "", kind: str = "") -> tuple[str, ...]:
    if kind == "tracked_filename" and _TRACKED_FILENAME_RE.search(text):
        return (text,)

    remainder = _strip_contextual_keeps(text, purpose)
    matches: list[tuple[int, str]] = []
    for pattern in (
        _LABEL_RE,
        _MACHINE_RE,
        _SPECIAL_IDENTIFIER_RE,
    ):
        matches.extend(
            (match.start(), match.group(0)) for match in pattern.finditer(remainder)
        )
    return tuple(dict.fromkeys(token for _, token in sorted(matches)))


def _assignment_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(
            name
            for child in node.elts
            for name in _assignment_names(child)
        )
    return ()


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _string_purpose(node: ast.Constant, parents: dict[ast.AST, ast.AST]) -> str:
    parent = parents.get(node)
    if isinstance(parent, ast.Assign):
        return "assignment:" + ",".join(
            name for target in parent.targets for name in _assignment_names(target)
        )
    if isinstance(parent, ast.AnnAssign):
        return "assignment:" + ",".join(_assignment_names(parent.target))
    if isinstance(parent, ast.keyword):
        return f"keyword:{parent.arg or '**'}"
    if isinstance(parent, ast.Dict):
        try:
            index = parent.values.index(node)
        except ValueError:
            return "dict"
        key = parent.keys[index]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return f"dict:{key.value}"
        return "dict"
    if isinstance(parent, ast.Call):
        function = parent.func
        if isinstance(function, ast.Name):
            return f"call:{function.id}"
        if isinstance(function, ast.Attribute):
            return f"call:{function.attr}"
    return type(parent).__name__ if parent is not None else "module"


def _docstring_nodes(tree: ast.AST) -> set[ast.Constant]:
    nodes: set[ast.Constant] = set()
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for owner in ast.walk(tree):
        if not isinstance(owner, owners) or not owner.body:
            continue
        first = owner.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            nodes.add(first.value)
    return nodes


def _source_span_lines(source_lines: list[str], node: ast.Constant) -> tuple[str, ...]:
    start = node.lineno - 1
    end = (node.end_lineno or node.lineno) - 1

    def byte_slice(line: str, lower: int | None, upper: int | None) -> str:
        return line.encode("utf-8")[lower:upper].decode("utf-8")

    if start == end:
        return (byte_slice(source_lines[start], node.col_offset, node.end_col_offset),)
    return (
        byte_slice(source_lines[start], node.col_offset, None),
        *source_lines[start + 1 : end],
        byte_slice(source_lines[end], 0, node.end_col_offset),
    )


def scan_python(path: Path, relative_path: str) -> tuple[SyntaxItem, ...]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=relative_path)
    parents = _parents(tree)
    docstrings = _docstring_nodes(tree)
    items: list[SyntaxItem] = []

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            items.append(
                SyntaxItem(
                    relative_path,
                    "python_comment",
                    token.start[0],
                    token.string,
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            kind = "python_docstring" if node in docstrings else "python_string_literal"
            purpose = "docstring" if node in docstrings else _string_purpose(node, parents)
            for offset, line in enumerate(_source_span_lines(source_lines, node)):
                if line.strip():
                    items.append(
                        SyntaxItem(
                            relative_path,
                            kind,
                            node.lineno + offset,
                            line,
                            purpose,
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            items.append(
                SyntaxItem(
                    relative_path,
                    "python_identifier",
                    node.lineno,
                    node.name,
                    type(node).__name__,
                )
            )
        elif isinstance(node, ast.arg):
            items.append(
                SyntaxItem(
                    relative_path,
                    "python_identifier",
                    node.lineno,
                    node.arg,
                    "argument",
                )
            )
        elif isinstance(node, ast.Name):
            items.append(
                SyntaxItem(
                    relative_path,
                    "python_identifier",
                    node.lineno,
                    node.id,
                    type(parents.get(node)).__name__,
                )
            )
    return tuple(items)


def scan_python_and_config(
    *,
    path_prefix: str | None = None,
) -> tuple[SyntaxItem, ...]:
    items: list[SyntaxItem] = []
    for relative_path in tracked_paths():
        if relative_path in GUARD_PATHS:
            continue
        if path_prefix is not None and not relative_path.startswith(path_prefix):
            continue
        path = REPO_ROOT / relative_path
        # Working-tree deletions (pending, unstaged removals) still appear
        # in git ls-files output; only files that exist on disk are
        # scannable.
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        if suffix == ".py":
            items.extend(scan_python(path, relative_path))
        elif suffix in CONFIG_SUFFIXES and (
            suffix not in {".md", ".rst"}
            or "/fixtures/" in f"/{relative_path}"
            or relative_path.startswith("evals/datasets/")
        ):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if line.strip():
                    items.append(
                        SyntaxItem(
                            relative_path,
                            "config_or_fixture_line",
                            line_number,
                            line,
                        )
                    )

        if task_tokens(path.name, kind="tracked_filename"):
            items.append(
                SyntaxItem(
                    relative_path,
                    "tracked_filename",
                    0,
                    path.name,
                )
            )
    return tuple(items)


def residual_hits(items: Iterable[SyntaxItem]) -> tuple[GuardHit, ...]:
    hits: list[GuardHit] = []
    accepted_seen: dict[tuple[str, str, str], int] = {}
    seen_occurrences: set[tuple[str, str, int, str]] = set()
    for item in items:
        for token in task_tokens(item.text, purpose=item.purpose, kind=item.kind):
            occurrence = (item.path, item.kind, item.line, token)
            if occurrence in seen_occurrences:
                continue
            seen_occurrences.add(occurrence)
            key = (item.path, item.kind, token)
            seen = accepted_seen.get(key, 0)
            if seen < ACCEPTED_SEMANTIC_KEEP_COUNTS.get(key, 0):
                accepted_seen[key] = seen + 1
                continue
            hits.append(
                GuardHit(item.path, item.kind, item.line, token, item.purpose)
            )
    return tuple(hits)


def damage_hits(items: Iterable[SyntaxItem]) -> tuple[str, ...]:
    damage: list[str] = []
    for item in items:
        text = item.text
        if re.search(r"\b(?:pre-|LP-)\s*(?:$|[,.;:])", text):
            damage.append(f"{item.path}:{item.line}:dangling-prefix")
        if chr(96) * 4 in text:
            damage.append(f"{item.path}:{item.line}:empty-inline-code")
        if re.search(r":[A-Za-z][\w.-]*:\s+\x60", text):
            damage.append(f"{item.path}:{item.line}:broken-sphinx-role")
        if re.search(r"\x60[A-Za-z]", text):
            damage.append(f"{item.path}:{item.line}:role-adhesion")
    return tuple(damage)


def _format_hits(hits: Iterable[GuardHit], limit: int = 80) -> list[str]:
    return [
        f"{hit.path}:{hit.line}:{hit.kind}:{hit.token}:{hit.purpose}"
        for hit in tuple(hits)[:limit]
    ]


def test_expected_roots_and_source_buckets_are_non_vacuous() -> None:
    paths = tracked_paths()
    for root in EXPECTED_ROOTS:
        assert any(path == root or path.startswith(f"{root}/") for path in paths), root
    assert sum(path.endswith(".py") for path in paths) > 0
    assert sum(Path(path).suffix.lower() in CONFIG_SUFFIXES for path in paths) > 0


def test_python_sources_parse_and_syntax_partitions_are_distinct() -> None:
    sample = '# R7 comment\n"""R7 docstring."""\nvalue = "R7 string"\n'
    tree = ast.parse(sample)
    assert len(_docstring_nodes(tree)) == 1

    parsed = 0
    for relative_path in tracked_paths():
        if relative_path.endswith(".py"):
            # Pending working-tree deletions are still git-tracked; only
            # files that exist on disk are parseable.
            if not (REPO_ROOT / relative_path).exists():
                continue
            ast.parse(
                (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                filename=relative_path,
            )
            parsed += 1
    assert parsed > 0


def test_mandatory_task_code_boundary_samples() -> None:
    must_fail = (
        "Phase " + "2 in process prose",
        "Task " + "5 task-label output",
        "R" + "4-A3 cleanup",
        "D" + "6-I3Q",
        "ReaderRecordAsk" + "R4A3",
        "load_r4_a3_dataset and " + "task_label",
    )
    tick = chr(96)
    must_pass = (
        "CEFR A1/A2/B1/B2/C1/C2 business fields",
        "SEGMENT_ID_U1_S1 and related fixture identities",
        "Article RAG B1/B2/B3 chunk identities",
        "d3-p1/d3-p4 reading_bases versions",
        "d6_i3b_plain_text_markdown_v1",
        "r14_complex",
        "#A66445 and #B45309 colors",
        f":class:{tick}ReaderRecord{tick} and :func:{tick}load_record{tick}",
        "issue #1234 and issue #5678",
    )
    for sample in must_fail:
        assert task_tokens(sample), sample
    for sample in must_pass:
        assert not task_tokens(sample), sample


def test_exact_keep_wire_token_never_shields_same_item_residual() -> None:
    wire = KEEP_WIRE_TOKENS[0]
    assert not task_tokens(wire)
    assert task_tokens(f"{wire}; " + "R" + "7 cleanup") == ("R7",)


def test_damage_matcher_self_check() -> None:
    clean = SyntaxItem("sample.py", "python_docstring", 1, "Business behavior.")
    damaged = SyntaxItem(
        "sample.py",
        "python_docstring",
        1,
        "dangling " + "pre" + "-;",
    )
    assert not damage_hits((clean,))
    assert damage_hits((damaged,))


def test_changed_python_guard_sources_have_no_mechanical_damage() -> None:
    items = tuple(
        item
        for relative_path in (
            "services/api/tests/test_task_number_naming_guard.py",
            "evals/tests/test_task_number_naming_guard.py",
        )
        for item in scan_python(REPO_ROOT / relative_path, relative_path)
    )
    assert not damage_hits(items)


def test_repository_has_no_task_history_residuals() -> None:
    hits = residual_hits(scan_python_and_config())
    assert not hits, (
        "task-history residuals remain; the rolling branch is expected to stay "
        "RED until the accepted inventory reaches zero: "
        f"{_format_hits(hits)} (reported={min(len(hits), 80)}, total={len(hits)})"
    )
