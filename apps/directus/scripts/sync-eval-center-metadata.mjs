import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DIRECTUS_URL = process.env.DIRECTUS_URL ?? "http://127.0.0.1:8055";
const DIRECTUS_CONTAINER = process.env.DIRECTUS_CONTAINER ?? "claread-directus";
const POSTGRES_CONTAINER = process.env.DIRECTUS_POSTGRES_CONTAINER ?? "claread-postgres";
const POSTGRES_DB = process.env.DIRECTUS_POSTGRES_DB ?? "claread";
const POSTGRES_USER = process.env.DIRECTUS_POSTGRES_USER ?? "claread";
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const EVAL_CONTROL_MIGRATION = resolve(
  SCRIPT_DIR,
  "../../../infra/migrations/eval-center/0001_eval_center_control_plane.sql",
);

const DIRECTUS_EMAIL = process.env.DIRECTUS_EMAIL ?? process.env.ADMIN_EMAIL ?? "admin@claread.dev";
const DIRECTUS_PASSWORD = process.env.DIRECTUS_PASSWORD ?? process.env.ADMIN_PASSWORD ?? "";
const DIRECTUS_TOKEN = process.env.DIRECTUS_TOKEN ?? process.env.ADMIN_TOKEN ?? "";

const MODULE_BAR_ITEMS = [
  { type: "module", id: "claread-eval-center", enabled: true },
];

// 已完成清理的历史 deprecation 项（2026-06 一次性移除）：
//   - module id "claread-grammar-prompt-lab" → 入口 index.js 变量名同步改为 EvalCenterModule
//   - collection "eval_node_probe_runs" → NodeProbeMode.vue 已删，migrations 中无该表
//   - field  "eval_review_notes.ab_report_id" → 已无引用
// 如需添加新弃用项，请直接修改本注释上方。
const DEPRECATED_MODULE_IDS = new Set();
const DEPRECATED_COLLECTION_IDS = [];
const DEPRECATED_FIELDS = [
  ["eval_example_lab_entries", "rag_eligible"],
];
const LEGACY_COLLECTIONS = [];

const COLLECTIONS = [
  {
    collection: "eval_prompt_variant_drafts",
    icon: "edit_note",
    color: "#7C3AED",
    note: "Eval-only prompt variant drafts. 不直接修改业务 prompt YAML。",
    display_template: "{{ variant_id }} {{ status }}",
    sort_field: "date_updated",
    sort: 31,
  },
  {
    collection: "eval_workflow_run_requests",
    icon: "pending_actions",
    color: "#0F766E",
    note: "Workflow run 请求队列。Directus 保存控制面记录，执行可由 directus_async 或外部 worker 完成。",
    display_template: "{{ run_id }} {{ status }}",
    sort_field: "date_created",
    sort: 32,
  },
  {
    collection: "eval_judge_run_requests",
    icon: "fact_check",
    color: "#4338CA",
    note: "LLM-as-a-Judge request queue. Directus 只创建/查看请求，执行由 evals judge worker 完成。",
    display_template: "{{ run_id }} {{ judge_run_id }} {{ status }}",
    sort_field: "date_created",
    sort: 33,
  },
  {
    collection: "eval_review_notes",
    icon: "rate_review",
    color: "#B45309",
    note: "Eval Center human review notes linked to workflow runs, workflow compare results, cases, or prompt variants. Artifacts remain immutable.",
    display_template: "{{ target_type }} {{ target_id }} {{ verdict }}",
    sort_field: "date_created",
    sort: 34,
  },
  {
    collection: "eval_example_lab_entries",
    icon: "school",
    color: "#059669",
    note: "Example Lab few-shot example entries. Stores manually curated examples with RAG-ready metadata for grammar_note / sentence_analysis.",
    display_template: "{{ example_id }} {{ example_type }} {{ label }}",
    sort_field: "date_updated",
    sort: 35,
  },
];

const FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 10,
      options: {
        choices: [
          { text: "Succeeded", value: "succeeded" },
          { text: "Failed", value: "failed" },
          { text: "Timeout", value: "timeout" },
        ],
      },
    },
  ],
  [
    "node_name",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 11,
      options: {
        choices: [
          { text: "Grammar", value: "grammar" },
          { text: "Vocabulary", value: "vocabulary" },
          { text: "Translation", value: "translation" },
        ],
      },
    },
  ],
  ["dry_run", { interface: "boolean", width: "half", sort: 12 }],
  ["reading_goal", { interface: "input", width: "half", sort: 13 }],
  ["reading_variant", { interface: "input", width: "half", sort: 14 }],
  ["source_type", { interface: "input", width: "half", sort: 15 }],
  ["input_text_hash", { interface: "input", readonly: true, width: "half", sort: 16 }],
  ["input_excerpt", { interface: "input-multiline", width: "full", sort: 17 }],
  ["input_text", { interface: "input-rich-text-md", width: "full", sort: 18 }],
  [
    "prompt_mode",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 20,
      options: {
        choices: [
          { text: "Baseline", value: "baseline" },
          { text: "No Few-shot", value: "no_few_shot" },
          { text: "Variant", value: "variant" },
        ],
      },
    },
  ],
  ["prompt_variant_id", { interface: "input", width: "half", sort: 21 }],
  ["prompt_preview", { interface: "input-rich-text-md", width: "full", sort: 22 }],
  ["agent_instructions", { interface: "input-rich-text-md", width: "full", sort: 23 }],
  [
    "human_verdict",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 30,
      options: {
        allowNone: true,
        choices: [
          { text: "Good", value: "good" },
          { text: "Bad", value: "bad" },
          { text: "Mixed", value: "mixed" },
          { text: "Needs Review", value: "needs_review" },
        ],
      },
    },
  ],
  ["human_notes", { interface: "input-rich-text-md", width: "full", sort: 31 }],
  ["promote_candidate", { interface: "boolean", width: "half", sort: 32 }],
  ["tags", { interface: "tags", width: "half", sort: 33 }],
  ["prompt_identity_json", jsonMeta(40, "Prompt identity")],
  ["model_profile", { interface: "input", width: "half", sort: 41 }],
  ["model_identity_json", jsonMeta(42, "Model identity")],
  ["workflow_identity_json", jsonMeta(43, "Workflow identity")],
  ["schema_identity_json", jsonMeta(44, "Schema identity")],
  ["prepared_sentences_json", jsonMeta(45, "Prepared sentences")],
  ["example_summary_json", jsonMeta(46, "Example summary")],
  ["preprocess_summary_json", jsonMeta(47, "Preprocess summary")],
  ["node_output_json", jsonMeta(48, "Node output")],
  ["rag_debug_json", jsonMeta(49, "Sanitized RAG/debug evidence")],
  ["warnings_json", jsonMeta(50, "Warnings")],
  ["runtime_summary_json", jsonMeta(51, "Runtime summary")],
  ["trace_refs_json", jsonMeta(52, "Trace refs")],
  ["error_json", jsonMeta(53, "Error")],
];

const PROMPT_VARIANT_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3 }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["variant_id", { interface: "input", width: "half", sort: 10 }],
  ["target", { interface: "input", width: "half", sort: 11, readonly: true }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 12,
      options: {
        choices: [
          { text: "Draft", value: "draft" },
          { text: "Ready for Eval", value: "ready_for_eval" },
          { text: "Archived", value: "archived" },
        ],
      },
    },
  ],
  [
    "scope",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 13,
      options: {
        choices: [
          { text: "Workflow Lab", value: "workflow_lab" },
        ],
      },
    },
  ],
  [
    "few_shot_mode",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 14,
      options: {
        choices: [
          { text: "Off", value: "off" },
          { text: "Baseline", value: "baseline" },
          { text: "Variant", value: "variant" },
          { text: "Settings", value: "settings" },
        ],
      },
    },
  ],
  ["snapshot_hash", { interface: "input", readonly: true, width: "half", sort: 15 }],
  ["notes", { interface: "input-rich-text-md", width: "full", sort: 16 }],
  ["policies_json", jsonMeta(20, "Policy overrides")],
  ["examples_json", jsonMeta(21, "Variant examples")],
  ["manifest_json", jsonMeta(22, "Normalized manifest snapshot")],
];

const WORKFLOW_RUN_REQUEST_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3 }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["run_id", { interface: "input", width: "half", sort: 10 }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 11,
      options: {
        choices: [
          { text: "Queued", value: "queued" },
          { text: "Running", value: "running" },
          { text: "Succeeded", value: "succeeded" },
          { text: "Failed", value: "failed" },
          { text: "Cancelled", value: "cancelled" },
        ],
      },
    },
  ],
  ["dataset_id", { interface: "input", width: "half", sort: 12 }],
  ["eval_purpose", { interface: "input", width: "half", sort: 13 }],
  ["adapter_kind", { interface: "input", width: "half", sort: 14 }],
  ["runner_kind", { interface: "input", width: "half", sort: 15, readonly: true }],
  ["prompt_variant_id", { interface: "input", width: "half", sort: 16 }],
  ["prompt_variant_snapshot_hash", { interface: "input", width: "half", sort: 17 }],
  ["artifact_run_id", { interface: "input", width: "half", sort: 18 }],
  ["artifact_path", { interface: "input", width: "half", sort: 19 }],
  ["source_request_id", { interface: "input", width: "half", sort: 20, readonly: true }],
  ["attempt_no", { interface: "input", width: "half", sort: 21, readonly: true }],
  ["max_attempts", { interface: "input", width: "half", sort: 22, readonly: true }],
  ["retry_reason", { interface: "input-multiline", width: "full", sort: 23 }],
  ["max_concurrency", { interface: "input", width: "half", sort: 24 }],
  ["lease_owner", { interface: "input", width: "half", sort: 25 }],
  ["lease_until", { interface: "datetime", width: "half", sort: 26 }],
  ["heartbeat_at", { interface: "datetime", width: "half", sort: 27 }],
  ["started_at", { interface: "datetime", width: "half", sort: 28 }],
  ["finished_at", { interface: "datetime", width: "half", sort: 29 }],
  ["notes", { interface: "input-rich-text-md", width: "full", sort: 30 }],
  ["tags", { interface: "tags", width: "half", sort: 31 }],
  ["config_json", jsonMeta(40, "Runner config snapshot")],
  ["error_json", jsonMeta(41, "Runner error summary")],
];

const JUDGE_RUN_REQUEST_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3 }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["judge_run_id", { interface: "input", width: "half", sort: 10 }],
  ["run_id", { interface: "input", width: "half", sort: 11 }],
  ["rubric_id", { interface: "input", width: "half", sort: 12 }],
  ["rubric_version", { interface: "input", width: "half", sort: 13, readonly: true }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 14,
      options: {
        choices: [
          { text: "Queued", value: "queued" },
          { text: "Running", value: "running" },
          { text: "Succeeded", value: "succeeded" },
          { text: "Failed", value: "failed" },
          { text: "Cancelled", value: "cancelled" },
        ],
      },
    },
  ],
  [
    "judge_adapter_kind",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 15,
      options: {
        choices: [
          { text: "LLM", value: "llm" },
        ],
      },
    },
  ],
  ["artifact_path", { interface: "input", width: "half", sort: 16, readonly: true }],
  ["source_request_id", { interface: "input", width: "half", sort: 17, readonly: true }],
  ["attempt_no", { interface: "input", width: "half", sort: 18, readonly: true }],
  ["max_attempts", { interface: "input", width: "half", sort: 19, readonly: true }],
  ["retry_reason", { interface: "input-multiline", width: "full", sort: 20 }],
  ["lease_owner", { interface: "input", width: "half", sort: 21 }],
  ["lease_until", { interface: "datetime", width: "half", sort: 22 }],
  ["heartbeat_at", { interface: "datetime", width: "half", sort: 23 }],
  ["started_at", { interface: "datetime", width: "half", sort: 24 }],
  ["finished_at", { interface: "datetime", width: "half", sort: 25 }],
  ["notes", { interface: "input-rich-text-md", width: "full", sort: 30 }],
  ["tags", { interface: "tags", width: "half", sort: 31 }],
  ["config_json", jsonMeta(40, "Judge config snapshot")],
  ["error_json", jsonMeta(41, "Judge worker error summary")],
];

const REVIEW_NOTES_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  [
    "target_type",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 10,
      options: {
        choices: [
          { text: "Workflow Run", value: "workflow_run" },
          { text: "Case Artifact", value: "case_artifact" },
          { text: "Workflow Compare", value: "workflow_compare" },
          { text: "Prompt Variant", value: "prompt_variant" },
        ],
      },
    },
  ],
  ["target_id", { interface: "input", width: "half", sort: 11 }],
  ["run_id", { interface: "input", width: "half", sort: 12 }],
  ["case_id", { interface: "input", width: "half", sort: 13 }],
  ["prompt_variant_id", { interface: "input", width: "half", sort: 14 }],
  [
    "verdict",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 16,
      options: {
        allowNone: true,
        choices: [
          { text: "Good", value: "good" },
          { text: "Bad", value: "bad" },
          { text: "Mixed", value: "mixed" },
          { text: "Needs Review", value: "needs_review" },
          { text: "Win", value: "win" },
          { text: "Loss", value: "loss" },
          { text: "Tie", value: "tie" },
          { text: "Blocked", value: "blocked" },
        ],
      },
    },
  ],
  ["promote_candidate", { interface: "boolean", width: "half", sort: 16 }],
  ["tags", { interface: "tags", width: "half", sort: 17 }],
  ["note", { interface: "input-rich-text-md", width: "full", sort: 18 }],
];

const EXAMPLE_LAB_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2, translations: [{ language: "zh-CN", translation: "创建时间" }] }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, translations: [{ language: "zh-CN", translation: "更新时间" }] }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],

  ["example_id", { interface: "input", width: "half", sort: 10, note: "人工可读 ID，如 grammar-gaokao-003", translations: [{ language: "zh-CN", translation: "示例 ID (example_id)" }] }],
  [
    "example_type",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 11,
      note: "RAG 相关类型按固定映射保存：grammar -> grammar_note，sentence_analysis -> sentence_analysis",
      translations: [{ language: "zh-CN", translation: "示例类型 (example_type)" }],
      options: {
        choices: [
          { text: "grammar — 语法批注", value: "grammar" },
          { text: "sentence_analysis — 句子分析", value: "sentence_analysis" },
          { text: "vocab — 词汇高亮", value: "vocab" },
          { text: "phrase — 短语释义", value: "phrase" },
          { text: "context — 语境释义", value: "context" },
          { text: "translation — 翻译", value: "translation" },
        ],
      },
    },
  ],

  ["sentence_text", { interface: "input-multiline", width: "full", sort: 20, note: "英文原句", translations: [{ language: "zh-CN", translation: "原句 (sentence_text)" }] }],
  ["output_fragment", { interface: "claread-output-fragment-editor", width: "full", sort: 21, note: "根据 example_type 自动切换结构化表单；RAG 路径固定 grammar -> grammar_note、sentence_analysis -> sentence_analysis", translations: [{ language: "zh-CN", translation: "输出片段 (output_fragment)" }] }],
  ["label", {
    interface: "input",
    width: "half",
    sort: 22,
    note: "中文标签/概述；grammar/sentence_analysis 在表单中由 output_fragment.label 自动同步",
    hidden: false,
    translations: [{ language: "zh-CN", translation: "标签 (label)" }],
    conditions: [
      {
        rule: { example_type: { _in: ["grammar", "sentence_analysis"] } },
        hidden: true,
      },
    ],
  }],

  [
    "source_kind",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 30,
      hidden: true,
      note: "默认 manual；仅导入/追溯场景需要查看",
      translations: [{ language: "zh-CN", translation: "来源类型 (source_kind)" }],
      options: {
        choices: [
          { text: "manual — 手动输入", value: "manual" },
          { text: "run_capture — 运行捕获", value: "run_capture" },
          { text: "yaml_import — YAML 导入", value: "yaml_import" },
          { text: "seed_import — Seed 导入", value: "seed_import" },
          { text: "other — 其他", value: "other" },
        ],
      },
    },
  ],
  ["source_ref", { interface: "input", width: "half", sort: 31, hidden: true, translations: [{ language: "zh-CN", translation: "来源引用 (source_ref)" }] }],
  [
    "reading_variant",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 32,
      translations: [{ language: "zh-CN", translation: "阅读变体 (reading_variant)" }],
      options: {
        allowNone: true,
        choices: [
          { text: "beginner_reading — 入门阅读", value: "beginner_reading" },
          { text: "intermediate_reading — 中阶阅读", value: "intermediate_reading" },
          { text: "intensive_reading — 精读模式", value: "intensive_reading" },
          { text: "gaokao — 高考", value: "gaokao" },
          { text: "cet — 四六级", value: "cet" },
          { text: "kaoyan — 考研", value: "kaoyan" },
          { text: "tem — 专四专八", value: "tem" },
          { text: "ielts_toefl — 雅思/托福", value: "ielts_toefl" },
          { text: "academic_general — 学术通用", value: "academic_general" },
        ],
      },
    },
  ],
  [
    "target_node",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 33,
      hidden: true,
      note: "由 example_type 自动映射；仅导入/审计场景需要查看",
      translations: [{ language: "zh-CN", translation: "目标节点 (target_node)" }],
      options: {
        allowNone: true,
        choices: [
          { text: "grammar — 语法", value: "grammar" },
          { text: "vocabulary — 词汇", value: "vocabulary" },
          { text: "translation — 翻译", value: "translation" },
          { text: "academic — 学术", value: "academic" },
        ],
      },
    },
  ],

  // 注：决策 3 (2026-06) 已移除 rag_eligible 字段；准入由 DB CHECK 约束
  // eval_example_lab_entries_approved_rag_eligible_check 强制 example_type 限制。
  // AI RAG Generator presentation interface (alias field, no DB column)
  ["ai_rag_generator", { interface: "claread-ai-rag-generator-interface", special: ["alias", "no-data"], width: "full", sort: 40, hidden: false, required: false, note: "AI 生成 grammar_tags / structure_signals / retrieval_text / teaching_goal", conditions: [{ rule: { example_type: { _in: ["grammar", "sentence_analysis"] } }, hidden: false }] }],
  ["grammar_tags", { interface: "input-code", width: "full", sort: 41, options: { language: "json" }, note: "AI 生成或手动编辑的语法标签数组", hidden: true, required: false, translations: [{ language: "zh-CN", translation: "语法标签 (grammar_tags)" }], conditions: [{ rule: { example_type: { _in: ["grammar", "sentence_analysis"] } }, hidden: false, required: false }] }],
  ["structure_signals", { interface: "input-code", width: "full", sort: 42, options: { language: "json" }, note: "AI 生成或手动编辑的结构信号数组", hidden: true, required: false, translations: [{ language: "zh-CN", translation: "结构信号 (structure_signals)" }], conditions: [{ rule: { example_type: { _in: ["grammar", "sentence_analysis"] } }, hidden: false, required: false }] }],
  ["retrieval_text", { interface: "input-multiline", width: "full", sort: 43, note: "AI 生成或手动编辑的 RAG 检索文本", hidden: true, required: false, translations: [{ language: "zh-CN", translation: "检索文本 (retrieval_text)" }], conditions: [{ rule: { example_type: { _in: ["grammar", "sentence_analysis"] } }, hidden: false, required: false }] }],
  [
    "teaching_goal",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 44,
      hidden: true,
      required: false,
      translations: [{ language: "zh-CN", translation: "教学目标 (teaching_goal)" }],
      conditions: [
        {
          rule: { example_type: { _in: ["grammar", "sentence_analysis"] } },
          hidden: false,
          required: false,
        },
      ],
      options: {
        allowNone: true,
        choices: [
          { text: "focused — 聚焦", value: "focused" },
          { text: "balanced — 均衡", value: "balanced" },
          { text: "structural — 结构", value: "structural" },
          { text: "explicit_split — 显式拆分", value: "explicit_split" },
          { text: "structural_logic — 结构逻辑", value: "structural_logic" },
          { text: "explicit_exam — 应试", value: "explicit_exam" },
          { text: "speed_support — 速读辅助", value: "speed_support" },
          { text: "rhetorical — 修辞", value: "rhetorical" },
          { text: "info_extraction — 信息提取", value: "info_extraction" },
        ],
      },
    },
  ],

  ["quality_score", { interface: "input", width: "half", sort: 50, note: "0.0 - 1.0", translations: [{ language: "zh-CN", translation: "质量评分 (quality_score)" }] }],
  ["approved", { interface: "boolean", width: "half", sort: 51, note: "审批通过后才可写入向量库", hidden: true, required: false, translations: [{ language: "zh-CN", translation: "已审批 (approved)" }], conditions: [{ rule: { example_type: { _in: ["grammar", "sentence_analysis"] } }, hidden: false }] }],

  ["notes", { interface: "input-rich-text-md", width: "full", sort: 60, translations: [{ language: "zh-CN", translation: "备注 (notes)" }] }],
  ["tags_json", { ...jsonMeta(61, "自定义标签数组"), translations: [{ language: "zh-CN", translation: "自定义标签 (tags_json)" }] }],
];

const FIELD_METADATA_BY_COLLECTION = {
  eval_prompt_variant_drafts: PROMPT_VARIANT_FIELD_METADATA,
  eval_workflow_run_requests: WORKFLOW_RUN_REQUEST_FIELD_METADATA,
  eval_judge_run_requests: JUDGE_RUN_REQUEST_FIELD_METADATA,
  eval_review_notes: REVIEW_NOTES_FIELD_METADATA,
  eval_example_lab_entries: EXAMPLE_LAB_FIELD_METADATA,
};

function jsonMeta(sort, note) {
  return {
    interface: "input-code",
    options: { language: "json", template: "{}" },
    width: "full",
    sort,
    note,
  };
}

function sqlLiteral(value) {
  if (value == null) return "NULL";
  return `'${String(value).replace(/'/g, "''")}'`;
}

function runSql(sql) {
  execFileSync(
    "docker",
    [
      "exec",
      POSTGRES_CONTAINER,
      "psql",
      "-U",
      POSTGRES_USER,
      "-d",
      POSTGRES_DB,
      "-v",
      "ON_ERROR_STOP=1",
      "-c",
      sql,
    ],
    { stdio: "pipe" },
  );
}

function readEvalControlMigrationSql() {
  return readFileSync(EVAL_CONTROL_MIGRATION, "utf8").replace(/^\uFEFF/, "");
}

function restartDirectus() {
  execFileSync("docker", ["restart", DIRECTUS_CONTAINER], { stdio: "pipe" });
}

async function waitForDirectusReady() {
  for (let index = 0; index < 30; index += 1) {
    try {
      const response = await fetch(`${DIRECTUS_URL}/server/ping`);
      if (response.ok) return;
    } catch {
      // Directus is still restarting.
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Directus did not become ready after metadata sync restart.");
}

function buildCollectionMetadataSql() {
  return `
    INSERT INTO directus_collections (collection, accountability, collapse)
    VALUES ${COLLECTIONS.map((item) => `(${sqlLiteral(item.collection)}, 'all', 'open')`).join(", ")}
    ON CONFLICT (collection) DO NOTHING;
  `;
}

function joinUrl(baseUrl, path) {
  return `${String(baseUrl).replace(/\/+$/, "")}/${String(path).replace(/^\/+/, "")}`;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(joinUrl(DIRECTUS_URL, path), options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.errors?.[0]?.message || payload?.message || response.statusText;
    throw new Error(`${options.method || "GET"} ${path} failed: ${message}`);
  }
  return payload;
}

async function tryRequestWithToken(token, path = "/users/me") {
  const response = await fetch(joinUrl(DIRECTUS_URL, path), {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
  });
  return response.ok;
}

async function login() {
  if (DIRECTUS_TOKEN) {
    const tokenValid = await tryRequestWithToken(DIRECTUS_TOKEN);
    if (tokenValid) return DIRECTUS_TOKEN;
  }
  if (!DIRECTUS_PASSWORD) {
    throw new Error(
      "Directus metadata sync requires DIRECTUS_TOKEN/ADMIN_TOKEN or DIRECTUS_PASSWORD/ADMIN_PASSWORD.",
    );
  }
  const payload = await fetchJson("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: DIRECTUS_EMAIL,
      password: DIRECTUS_PASSWORD,
    }),
  });
  const token = payload?.data?.access_token;
  if (!token) throw new Error("Directus login did not return an access token.");
  return token;
}

async function request(token, method, path, body) {
  return fetchJson(path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: body == null ? undefined : JSON.stringify(body),
  });
}

function upsertModuleBarItems(current, additions) {
  const byId = new Map();
  for (const item of current) {
    if (item?.id && !DEPRECATED_MODULE_IDS.has(item.id)) byId.set(item.id, item);
  }
  for (const item of additions) {
    byId.set(item.id, { ...byId.get(item.id), ...item });
  }
  return Array.from(byId.values());
}

async function syncCollections(token) {
  for (const item of COLLECTIONS) {
    await request(token, "PATCH", `/collections/${item.collection}`, {
      meta: {
        icon: item.icon,
        color: item.color,
        note: item.note,
        display_template: item.display_template,
        sort_field: item.sort_field,
        sort: item.sort,
        hidden: false,
        singleton: false,
        accountability: "all",
        collapse: "open",
      },
    });
  }

  for (const item of LEGACY_COLLECTIONS) {
    try {
      await request(token, "PATCH", `/collections/${item.collection}`, {
        meta: {
          note: item.note,
          hidden: true,
        },
      });
    } catch (error) {
      if (!String(error?.message || "").includes("404")) throw error;
    }
  }
}

async function syncFields(token) {
  for (const [collection, fields] of Object.entries(FIELD_METADATA_BY_COLLECTION)) {
    for (const [field, meta] of fields) {
      const body = { meta };
      // Alias fields need type and schema for creation
      if (meta.special?.includes("alias")) {
        body.field = field;
        body.type = "alias";
        body.schema = null;
      }
      try {
        await request(token, "PATCH", `/fields/${collection}/${field}`, body);
      } catch (e) {
        if (String(e?.message || "").includes("404") || String(e?.message || "").includes("doesn't exist")) {
          await request(token, "POST", `/fields/${collection}`, body);
        } else {
          throw e;
        }
      }
    }
  }
}

function cleanupDeprecatedMetadata() {
  if (DEPRECATED_COLLECTION_IDS.length) {
    runSql(`
      DELETE FROM directus_fields
      WHERE collection IN (${DEPRECATED_COLLECTION_IDS.map(sqlLiteral).join(", ")});
      DELETE FROM directus_relations
      WHERE many_collection IN (${DEPRECATED_COLLECTION_IDS.map(sqlLiteral).join(", ")})
         OR one_collection IN (${DEPRECATED_COLLECTION_IDS.map(sqlLiteral).join(", ")});
      DELETE FROM directus_permissions
      WHERE collection IN (${DEPRECATED_COLLECTION_IDS.map(sqlLiteral).join(", ")});
      DELETE FROM directus_collections
      WHERE collection IN (${DEPRECATED_COLLECTION_IDS.map(sqlLiteral).join(", ")});
    `);
  }
  for (const [collection, field] of DEPRECATED_FIELDS) {
    runSql(`
      DELETE FROM directus_fields
      WHERE collection = ${sqlLiteral(collection)} AND field = ${sqlLiteral(field)};
    `);
  }
}

async function syncModuleBar(token) {
  const settings = await request(token, "GET", "/settings");
  const currentModuleBar = Array.isArray(settings?.data?.module_bar)
    ? settings.data.module_bar
    : [];
  const nextModuleBar = upsertModuleBarItems(currentModuleBar, MODULE_BAR_ITEMS);

  await request(token, "PATCH", "/settings", { module_bar: nextModuleBar });
}

runSql(readEvalControlMigrationSql());
runSql(buildCollectionMetadataSql());
restartDirectus();
await waitForDirectusReady();

const token = await login();
cleanupDeprecatedMetadata();
await syncCollections(token);
await syncFields(token);
await syncModuleBar(token);

console.log(
  `Eval Center metadata synced. Enabled modules: ${MODULE_BAR_ITEMS.map((item) => item.id).join(", ")}; collections: ${COLLECTIONS.map((item) => item.collection).join(", ")}`,
);
