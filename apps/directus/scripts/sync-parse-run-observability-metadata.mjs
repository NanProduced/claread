import { execFileSync } from "node:child_process";

const DIRECTUS_URL = process.env.DIRECTUS_URL ?? "http://127.0.0.1:8055";
const DIRECTUS_CONTAINER = process.env.DIRECTUS_CONTAINER ?? "claread-directus";
const POSTGRES_CONTAINER = process.env.DIRECTUS_POSTGRES_CONTAINER ?? "claread-postgres";
const POSTGRES_DB = process.env.DIRECTUS_POSTGRES_DB ?? "claread";
const POSTGRES_USER = process.env.DIRECTUS_POSTGRES_USER ?? "claread";
const PARSE_RUN_DASHBOARD_ID = "4ad98e26-314a-4f6f-a7b1-2d5f85b8e001";
const RESET_PARSE_RUN_DASHBOARD =
  String(process.env.RESET_PARSE_RUN_DASHBOARD ?? "").toLowerCase() === "true";

function readContainerEnv(container, name) {
  try {
    return execFileSync("docker", ["exec", container, "printenv", name], {
      stdio: "pipe",
      encoding: "utf8",
    }).trim();
  } catch {
    return "";
  }
}

const DIRECTUS_EMAIL =
  process.env.DIRECTUS_EMAIL?.trim() ||
  process.env.ADMIN_EMAIL?.trim() ||
  readContainerEnv(DIRECTUS_CONTAINER, "ADMIN_EMAIL");
const DIRECTUS_PASSWORD =
  process.env.DIRECTUS_PASSWORD?.trim() ||
  process.env.ADMIN_PASSWORD?.trim() ||
  readContainerEnv(DIRECTUS_CONTAINER, "ADMIN_PASSWORD");
const DIRECTUS_RESET_PRESET_EMAIL =
  process.env.DIRECTUS_RESET_PRESET_EMAIL?.trim() || DIRECTUS_EMAIL;

const COLLECTIONS = [
  {
    collection: "analysis_records",
    icon: "article",
    color: "#6366F1",
    note: "文章解析主记录，以它为入口回查结果、任务、事件和 usage。",
    display_template: "{{ title }} {{ client_record_id }}",
    sort_field: "updated_at",
    sort: 1,
  },
  {
    collection: "analysis_results",
    icon: "description",
    color: "#8B5CF6",
    note: "文章解析结果快照，承载 render_scene_json 和 page_state_json。",
    display_template: "{{ record_id }} {{ schema_version }}",
    sort_field: "created_at",
    sort: 2,
  },
  {
    collection: "analysis_tasks",
    icon: "assignment",
    color: "#F59E0B",
    note: "主解析任务执行记录。",
    display_template: "{{ analysis_record_id.title }} {{ status }} {{ id }}",
    sort_field: "queued_at",
    sort: 3,
  },
  {
    collection: "analysis_task_events",
    icon: "history",
    color: "#EF4444",
    note: "主解析任务事件流。",
    display_template: "{{ task_id.analysis_record_id.title }} {{ event_type }} {{ created_at }}",
    sort_field: "created_at",
    sort: 4,
  },
  {
    collection: "analysis_overview_tasks",
    icon: "summarize",
    color: "#10B981",
    note: "Overview Hint 派生任务。",
    display_template: "{{ analysis_record_id.title }} {{ status }} {{ id }}",
    sort_field: "queued_at",
    sort: 5,
  },
  {
    collection: "analysis_overview_task_events",
    icon: "event_note",
    color: "#6B7280",
    note: "Overview 任务事件流。",
    display_template: "{{ task_id.analysis_record_id.title }} {{ event_type }} {{ created_at }}",
    sort_field: "created_at",
    sort: 6,
  },
  {
    collection: "ai_usage_events",
    icon: "analytics",
    color: "#EC4899",
    note: "AI 使用量审计事件。",
    display_template: "{{ record_id.title }} {{ capability_code }} {{ billed_points }}",
    sort_field: "created_at",
    sort: 7,
  },
  {
    collection: "analysis_debug_snapshots",
    icon: "bug_report",
    color: "#0EA5E9",
    note: "主解析任务调试摘要快照，用于 Inspector 和 workflow 质量诊断。",
    display_template: "{{ task_id.analysis_record_id.title }} {{ task_status }} {{ created_at }}",
    sort_field: "created_at",
    sort: 8,
  },
];

const FIELD_METADATA = [
  {
    collection: "analysis_records",
    field: "id",
    meta: {
      interface: "claread-inspector-launcher-interface",
      options: {
        buttonLabel: "Open Inspector",
        showContext: false,
        target: "record",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "解析记录主键，并在详情页提供 Inspector 入口。",
    },
  },
  {
    collection: "analysis_records",
    field: "title",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 2,
      note: "解析记录标题。",
    },
  },
  {
    collection: "analysis_records",
    field: "source_type",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "记录来源类型。",
    },
  },
  {
    collection: "analysis_records",
    field: "reading_goal",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 5,
      note: "阅读目标。",
    },
  },
  {
    collection: "analysis_records",
    field: "reading_variant",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 6,
      note: "阅读目标变体。",
    },
  },
  {
    collection: "analysis_records",
    field: "analysis_status",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "analysis_status",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 7,
      note: "解析记录状态。",
    },
  },
  {
    collection: "analysis_records",
    field: "user_facing_state",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "user_facing_state",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 8,
      note: "面向用户的结果状态。",
    },
  },
  {
    collection: "analysis_records",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 10,
      note: "记录创建时间。",
    },
  },
  {
    collection: "analysis_records",
    field: "updated_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 11,
      note: "记录更新时间。",
    },
  },
  {
    collection: "analysis_records",
    field: "user_id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 20,
      note: "所属用户 ID。",
    },
  },
  {
    collection: "analysis_records",
    field: "client_record_id",
    meta: {
      interface: "claread-enum-label-interface",
      options: {
        variant: "client_source_from_id",
        show_raw: true,
      },
      display: "claread-enum-label-display",
      display_options: {
        variant: "client_source_from_id",
        show_raw: true,
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 3,
      note: "客户端记录 ID，并根据前缀展示来源端。",
    },
  },
  {
    collection: "analysis_records",
    field: "source_text",
    meta: {
      interface: "claread-text-preview-interface",
      options: {
        preview_length: 240,
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 12,
      note: "原始输入文本折叠预览。",
    },
  },
  {
    collection: "analysis_records",
    field: "request_payload_json",
    meta: {
      interface: "claread-json-summary-interface",
      options: {
        summary_kind: "generic",
      },
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 13,
      note: "请求侧持久化快照 JSON。用于来源信息和 reader scene 组装。",
    },
  },
  {
    collection: "analysis_records",
    field: "source_text_hash",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 23,
      note: "原始文本哈希。",
    },
  },
  {
    collection: "analysis_records",
    field: "extended",
    meta: {
      interface: "boolean",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 24,
      note: "是否启用扩展分析。",
    },
  },
  {
    collection: "analysis_records",
    field: "deleted_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 25,
      note: "软删除时间。",
    },
  },
  {
    collection: "analysis_records",
    field: "deleted_by",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 26,
      note: "执行删除的用户 ID。",
    },
  },
  {
    collection: "analysis_records",
    field: "last_opened_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 9,
      note: "最近打开时间。",
    },
  },
  {
    collection: "analysis_results",
    field: "record_id",
    meta: {
      interface: "claread-inspector-launcher-interface",
      options: {
        buttonLabel: "Open Inspector",
        showContext: true,
        target: "record",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "record",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "关联的解析记录主键，并在详情页提供 Inspector 入口。",
    },
  },
  {
    collection: "analysis_results",
    field: "workflow_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 2,
      note: "分析 workflow 版本号。",
    },
  },
  {
    collection: "analysis_results",
    field: "schema_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 3,
      note: "渲染结果 schema 版本号。",
    },
  },
  {
    collection: "analysis_results",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "结果创建时间。",
    },
  },
  {
    collection: "analysis_results",
    field: "render_scene_json",
    meta: {
      interface: "claread-json-summary-interface",
      options: {
        summary_kind: "render_scene",
      },
      display: "claread-json-summary",
      display_options: {
        summary_kind: "render_scene",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 20,
      note: "渲染场景 JSON。",
    },
  },
  {
    collection: "analysis_results",
    field: "page_state_json",
    meta: {
      interface: "claread-json-summary-interface",
      options: {
        summary_kind: "page_state",
      },
      display: "claread-json-summary",
      display_options: {
        summary_kind: "page_state",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 21,
      note: "页面状态 JSON。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "主解析任务主键。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "analysis_record_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ title }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "record",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 2,
      note: "关联的解析记录。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "status",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "task_status",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 3,
      note: "主解析任务状态。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "failure_code",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "失败错误码。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "failure_message",
    meta: {
      interface: "input-multiline",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 5,
      note: "失败错误信息。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "queued_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 6,
      note: "进入队列时间。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "started_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 7,
      note: "任务开始时间。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "finished_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 8,
      note: "任务完成时间。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 9,
      note: "任务创建时间。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "updated_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 10,
      note: "任务更新时间。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "user_id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 20,
      note: "所属用户 ID。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "worker_token",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "full",
      sort: 21,
      note: "工作线程 token。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "queue_name",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 22,
      note: "队列名称。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "attempt_no",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 23,
      note: "任务尝试次数。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "usage_summary_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: true,
      width: "full",
      sort: 24,
      note: "任务 usage 汇总 JSON。",
    },
  },
  {
    collection: "analysis_tasks",
    field: "quota_cost_points",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 25,
      note: "配额消耗积分。",
    },
  },
  {
    collection: "analysis_task_events",
    field: "id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "主解析任务事件主键。",
    },
  },
  {
    collection: "analysis_task_events",
    field: "task_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ analysis_record_id.title }} {{ status }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "analysis_task",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 2,
      note: "关联的主解析任务。",
    },
  },
  {
    collection: "analysis_task_events",
    field: "event_type",
    meta: {
      interface: "claread-enum-label-interface",
      options: {
        variant: "event_type",
        show_raw: true,
      },
      display: "claread-event-type-display",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 3,
      note: "事件类型中文标签。",
    },
  },
  {
    collection: "analysis_task_events",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "事件创建时间。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "Overview 任务主键。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "analysis_record_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ title }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "record",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 2,
      note: "关联的解析记录。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "status",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "task_status",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 3,
      note: "Overview 任务状态。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "failure_code",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "失败错误码。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "failure_message",
    meta: {
      interface: "input-multiline",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 5,
      note: "失败错误信息。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "queued_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 6,
      note: "进入队列时间。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "started_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 7,
      note: "任务开始时间。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "finished_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 8,
      note: "任务完成时间。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 9,
      note: "任务创建时间。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "updated_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 10,
      note: "任务更新时间。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "user_id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 20,
      note: "所属用户 ID。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "worker_token",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "full",
      sort: 21,
      note: "工作线程 token。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "attempt_no",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 22,
      note: "任务尝试次数。",
    },
  },
  {
    collection: "analysis_overview_tasks",
    field: "usage_summary_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: true,
      width: "full",
      sort: 23,
      note: "任务 usage 汇总 JSON。",
    },
  },
  {
    collection: "analysis_overview_task_events",
    field: "id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "Overview 任务事件主键。",
    },
  },
  {
    collection: "analysis_overview_task_events",
    field: "task_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ analysis_record_id.title }} {{ status }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "analysis_overview_task",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 2,
      note: "关联的 overview 任务。",
    },
  },
  {
    collection: "analysis_overview_task_events",
    field: "event_type",
    meta: {
      interface: "claread-enum-label-interface",
      options: {
        variant: "event_type",
        show_raw: true,
      },
      display: "claread-event-type-display",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 3,
      note: "事件类型中文标签。",
    },
  },
  {
    collection: "analysis_overview_task_events",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "事件创建时间。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "AI usage 审计事件主键。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "record_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ title }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "record",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 2,
      note: "关联的解析记录。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "task_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ analysis_record_id.title }} {{ status }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "analysis_task",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 3,
      note: "关联的主解析任务。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "capability_code",
    meta: {
      interface: "claread-enum-label-interface",
      options: {
        variant: "capability_code",
        show_raw: true,
      },
      display: "claread-enum-label-display",
      display_options: {
        variant: "capability_code",
        show_raw: true,
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "能力代码中文说明。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "status",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "usage_status",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 5,
      note: "usage 事件状态。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "input_tokens",
    meta: {
      interface: "input",
      display: "claread-usage-summary",
      display_options: {
        mode: "tokens",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 6,
      note: "输入 token 数。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "output_tokens",
    meta: {
      interface: "input",
      display: "claread-usage-summary",
      display_options: {
        mode: "tokens",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 7,
      note: "输出 token 数。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "billed_points",
    meta: {
      interface: "input",
      display: "claread-usage-summary",
      display_options: {
        mode: "points",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 8,
      note: "计费积分。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "usage_scope",
    meta: {
      interface: "claread-enum-label-interface",
      options: {
        variant: "usage_scope",
        show_raw: true,
      },
      display: "claread-enum-label-display",
      display_options: {
        variant: "usage_scope",
        show_raw: true,
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 9,
      note: "usage 作用域。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "billing_mode",
    meta: {
      interface: "claread-enum-label-interface",
      options: {
        variant: "billing_mode",
        show_raw: true,
      },
      display: "claread-enum-label-display",
      display_options: {
        variant: "billing_mode",
        show_raw: true,
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 10,
      note: "计费模式。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "latency_ms",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 11,
      note: "请求延迟毫秒数。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "model_provider",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 12,
      note: "模型提供方。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "model_name",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 13,
      note: "模型名称。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "model_route",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 14,
      note: "模型路由。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "prompt_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 15,
      note: "Prompt 版本号。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 16,
      note: "事件创建时间。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "user_id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 20,
      note: "所属用户 ID。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "workflow_name",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 21,
      note: "Workflow 名称。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "request_id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 22,
      note: "请求 ID。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "client_platform",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 23,
      note: "客户端平台。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "workflow_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 24,
      note: "Workflow 版本号。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "schema_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 25,
      note: "Schema 版本号。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "total_tokens",
    meta: {
      interface: "input",
      display: "claread-usage-summary",
      display_options: {
        mode: "tokens",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 27,
      note: "总 token 数。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "model_profile",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 28,
      note: "模型 profile。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "error_code",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 29,
      note: "错误码。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "error_message",
    meta: {
      interface: "input-multiline",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 30,
      note: "错误信息。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "cache_read_tokens",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 31,
      note: "缓存读取 token 数。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "cache_write_tokens",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 32,
      note: "缓存写入 token 数。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "billing_policy_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 34,
      note: "计费策略版本。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "metadata_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: true,
      width: "full",
      sort: 36,
      note: "扩展元数据 JSON。",
    },
  },
  {
    collection: "ai_usage_events",
    field: "daily_reader_article_id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: true,
      width: "half",
      sort: 38,
      note: "每日精读文章 ID。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "id",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 1,
      note: "调试摘要快照主键。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "record_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ title }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "record",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 2,
      note: "关联的解析记录。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "task_id",
    meta: {
      special: ["m2o"],
      interface: "select-dropdown-m2o",
      options: {
        template: "{{ analysis_record_id.title }} {{ status }} {{ id }}",
      },
      display: "claread-record-context-display",
      display_options: {
        target: "analysis_task",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 3,
      note: "关联的主解析任务。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "workflow_name",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 4,
      note: "workflow 名称。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "workflow_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 5,
      note: "workflow 版本号。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "schema_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 6,
      note: "render scene schema 版本号。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "prompt_version",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 7,
      note: "prompt registry 版本号。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "task_status",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "task_status",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 8,
      note: "任务最终状态。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "user_facing_state",
    meta: {
      interface: "input",
      display: "claread-status-badge",
      display_options: {
        variant: "user_facing_state",
      },
      readonly: true,
      hidden: false,
      width: "half",
      sort: 9,
      note: "面向用户的结果状态。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "failure_code",
    meta: {
      interface: "input",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 10,
      note: "失败代码。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "failure_message",
    meta: {
      interface: "input-multiline",
      readonly: true,
      hidden: false,
      width: "full",
      sort: 11,
      note: "失败信息。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "created_at",
    meta: {
      interface: "datetime",
      readonly: true,
      hidden: false,
      width: "half",
      sort: 12,
      note: "快照创建时间。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "preprocess_summary_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 20,
      note: "预处理摘要 JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "normalize_summary_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 21,
      note: "normalize 摘要 JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "drop_log_summary_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: true,
      width: "full",
      sort: 22,
      note: "drop log 摘要 JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "runtime_summary_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 23,
      note: "运行时摘要 JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "academic_quality_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 24,
      note: "academic 质量摘要 JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "few_shot_debug_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 25,
      note: "few-shot provenance JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "rag_debug_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: false,
      width: "full",
      sort: 26,
      note: "grammar RAG provenance JSON。",
    },
  },
  {
    collection: "analysis_debug_snapshots",
    field: "trace_refs_json",
    meta: {
      interface: "input-code",
      display: "claread-json-summary",
      display_options: {
        summary_kind: "generic",
      },
      readonly: true,
      hidden: true,
      width: "full",
      sort: 27,
      note: "trace refs JSON。",
    },
  },
];

const ALIAS_FIELDS = [
  {
    collection: "analysis_records",
    field: "result",
    note: "解析结果快照。",
    sort: 90,
  },
  {
    collection: "analysis_records",
    field: "analysis_tasks",
    note: "主解析任务列表。",
    sort: 91,
  },
  {
    collection: "analysis_records",
    field: "overview_tasks",
    note: "Overview 任务列表。",
    sort: 92,
  },
  {
    collection: "analysis_records",
    field: "usage_events",
    note: "关联的 AI usage 事件。",
    sort: 93,
  },
  {
    collection: "analysis_records",
    field: "debug_snapshots",
    note: "关联的调试摘要快照。",
    sort: 94,
  },
  {
    collection: "analysis_tasks",
    field: "events",
    note: "主解析任务事件流。",
    sort: 90,
    meta: {
      interface: "claread-relational-events-display",
    },
  },
  {
    collection: "analysis_tasks",
    field: "usage_events",
    note: "关联到该主任务的 AI usage 事件。",
    sort: 91,
  },
  {
    collection: "analysis_tasks",
    field: "debug_snapshot",
    note: "关联到该主任务的调试摘要快照。",
    sort: 92,
  },
  {
    collection: "analysis_overview_tasks",
    field: "events",
    note: "Overview 任务事件流。",
    sort: 90,
    meta: {
      interface: "claread-relational-events-display",
    },
  },
];

const RELATIONS = [
  {
    many_collection: "analysis_results",
    many_field: "record_id",
    one_collection: "analysis_records",
    one_field: "result",
    one_deselect_action: "delete",
  },
  {
    many_collection: "analysis_tasks",
    many_field: "analysis_record_id",
    one_collection: "analysis_records",
    one_field: "analysis_tasks",
    one_deselect_action: "delete",
  },
  {
    many_collection: "analysis_overview_tasks",
    many_field: "analysis_record_id",
    one_collection: "analysis_records",
    one_field: "overview_tasks",
    one_deselect_action: "delete",
  },
  {
    many_collection: "analysis_task_events",
    many_field: "task_id",
    one_collection: "analysis_tasks",
    one_field: "events",
    one_deselect_action: "delete",
  },
  {
    many_collection: "analysis_overview_task_events",
    many_field: "task_id",
    one_collection: "analysis_overview_tasks",
    one_field: "events",
    one_deselect_action: "delete",
  },
  {
    many_collection: "ai_usage_events",
    many_field: "record_id",
    one_collection: "analysis_records",
    one_field: "usage_events",
    one_deselect_action: "nullify",
  },
  {
    many_collection: "ai_usage_events",
    many_field: "task_id",
    one_collection: "analysis_tasks",
    one_field: "usage_events",
    one_deselect_action: "nullify",
  },
  {
    many_collection: "analysis_debug_snapshots",
    many_field: "record_id",
    one_collection: "analysis_records",
    one_field: "debug_snapshots",
    one_deselect_action: "delete",
  },
  {
    many_collection: "analysis_debug_snapshots",
    many_field: "task_id",
    one_collection: "analysis_tasks",
    one_field: "debug_snapshot",
    one_deselect_action: "delete",
  },
];

const DEFAULT_PRESETS = [
  {
    collection: "analysis_records",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "title",
          "client_record_id",
          "source_type",
          "reading_goal",
          "reading_variant",
          "analysis_status",
          "user_facing_state",
          "last_opened_at",
          "updated_at",
        ],
      },
    },
  },
  {
    collection: "analysis_results",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: ["record_id", "workflow_version", "schema_version", "created_at"],
      },
    },
  },
  {
    collection: "analysis_tasks",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "id",
          "analysis_record_id",
          "status",
          "failure_code",
          "queued_at",
          "started_at",
          "finished_at",
        ],
      },
    },
  },
  {
    collection: "analysis_task_events",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: ["task_id", "event_type", "created_at"],
      },
    },
  },
  {
    collection: "analysis_overview_tasks",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "id",
          "analysis_record_id",
          "status",
          "failure_code",
          "queued_at",
          "started_at",
          "finished_at",
        ],
      },
    },
  },
  {
    collection: "analysis_overview_task_events",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: ["task_id", "event_type", "created_at"],
      },
    },
  },
  {
    collection: "ai_usage_events",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "record_id",
          "task_id",
          "capability_code",
          "status",
          "input_tokens",
          "output_tokens",
          "billed_points",
          "usage_scope",
          "billing_mode",
          "latency_ms",
          "model_provider",
          "model_name",
          "created_at",
        ],
      },
    },
  },
  {
    collection: "analysis_debug_snapshots",
    layout: "tabular",
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "task_id",
          "task_status",
          "user_facing_state",
          "workflow_version",
          "schema_version",
          "failure_code",
          "created_at",
        ],
      },
    },
  },
];

const GLOBAL_BOOKMARKS = [
  {
    bookmark: "Parse Records / Active",
    collection: "analysis_records",
    layout: "tabular",
    filter: {
      _and: [
        {
          analysis_status: {
            _eq: "ready",
          },
        },
        {
          deleted_at: {
            _null: true,
          },
        },
      ],
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "title",
          "client_record_id",
          "source_type",
          "reading_goal",
          "reading_variant",
          "analysis_status",
          "user_facing_state",
          "last_opened_at",
          "updated_at",
        ],
        sort: ["-updated_at"],
      },
    },
  },
  {
    bookmark: "Parse Records / Failed",
    collection: "analysis_records",
    layout: "tabular",
    filter: {
      _and: [
        {
          analysis_status: {
            _eq: "failed",
          },
        },
        {
          deleted_at: {
            _null: true,
          },
        },
      ],
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "title",
          "client_record_id",
          "source_type",
          "reading_goal",
          "reading_variant",
          "analysis_status",
          "user_facing_state",
          "last_opened_at",
          "updated_at",
        ],
        sort: ["-updated_at"],
      },
    },
  },
  {
    bookmark: "Parse Records / Partial",
    collection: "analysis_records",
    layout: "tabular",
    filter: {
      _and: [
        {
          analysis_status: {
            _eq: "partial",
          },
        },
        {
          deleted_at: {
            _null: true,
          },
        },
      ],
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "title",
          "client_record_id",
          "source_type",
          "reading_goal",
          "reading_variant",
          "analysis_status",
          "user_facing_state",
          "last_opened_at",
          "updated_at",
        ],
        sort: ["-updated_at"],
      },
    },
  },
  {
    bookmark: "Analysis Tasks / Failed",
    collection: "analysis_tasks",
    layout: "tabular",
    filter: {
      status: {
        _eq: "failed",
      },
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "id",
          "analysis_record_id",
          "status",
          "failure_code",
          "queued_at",
          "started_at",
          "finished_at",
        ],
        sort: ["-queued_at"],
      },
    },
  },
  {
    bookmark: "Overview Tasks / Failed",
    collection: "analysis_overview_tasks",
    layout: "tabular",
    filter: {
      status: {
        _eq: "failed",
      },
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "id",
          "analysis_record_id",
          "status",
          "failure_code",
          "queued_at",
          "started_at",
          "finished_at",
        ],
        sort: ["-queued_at"],
      },
    },
  },
  {
    bookmark: "Usage Events / analysis_full",
    collection: "ai_usage_events",
    layout: "tabular",
    filter: {
      capability_code: {
        _eq: "analysis_full",
      },
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "record_id",
          "task_id",
          "capability_code",
          "status",
          "input_tokens",
          "output_tokens",
          "billed_points",
          "usage_scope",
          "billing_mode",
          "latency_ms",
          "model_provider",
          "model_name",
          "created_at",
        ],
        sort: ["-created_at"],
      },
    },
  },
  {
    bookmark: "Usage Events / analysis_overview_hint",
    collection: "ai_usage_events",
    layout: "tabular",
    filter: {
      capability_code: {
        _eq: "analysis_overview_hint",
      },
    },
    layout_query: {
      tabular: {
        page: 1,
        fields: [
          "record_id",
          "task_id",
          "capability_code",
          "status",
          "input_tokens",
          "output_tokens",
          "billed_points",
          "usage_scope",
          "billing_mode",
          "latency_ms",
          "model_provider",
          "model_name",
          "created_at",
        ],
        sort: ["-created_at"],
      },
    },
  },
  {
    bookmark: "Task Events / Grouped",
    collection: "analysis_task_events",
    layout: "claread-observability-groups",
    filter: null,
    layout_query: {
      "claread-observability-groups": {
        limit: 200,
        sort: ["task_id", "created_at"],
      },
    },
  },
  {
    bookmark: "Overview Task Events / Grouped",
    collection: "analysis_overview_task_events",
    layout: "claread-observability-groups",
    filter: null,
    layout_query: {
      "claread-observability-groups": {
        limit: 200,
        sort: ["task_id", "created_at"],
      },
    },
  },
  {
    bookmark: "Usage Events / Grouped",
    collection: "ai_usage_events",
    layout: "claread-observability-groups",
    filter: null,
    layout_query: {
      "claread-observability-groups": {
        limit: 200,
        sort: ["-created_at"],
      },
    },
  },
];

const DASHBOARDS = [
  {
    id: PARSE_RUN_DASHBOARD_ID,
    name: "Parse Run Observability",
    icon: "monitoring",
    color: "#245CB8",
    note: "解析链路观测首页：记录趋势、任务状态、usage 和最近失败任务。",
  },
];

const DASHBOARD_PANELS = [
  {
    id: "4ad98e26-314a-4f6f-a7b1-2d5f85b8e101",
    dashboard: PARSE_RUN_DASHBOARD_ID,
    name: "解析记录 / 近 7 天",
    icon: "monitoring",
    type: "claread-parse-run-records-7d",
    position_x: 1,
    position_y: 29,
    width: 72,
    height: 12,
    color: "#245CB8",
    show_header: true,
    note: "最近 7 天解析记录数量和日趋势。",
    options: {
      targetUrl: "/admin/content/analysis_records",
    },
  },
  {
    id: "4ad98e26-314a-4f6f-a7b1-2d5f85b8e102",
    dashboard: PARSE_RUN_DASHBOARD_ID,
    name: "主解析任务状态",
    icon: "donut_large",
    type: "claread-parse-run-task-status",
    position_x: 37,
    position_y: 1,
    width: 18,
    height: 16,
    color: "#9A5B00",
    show_header: true,
    note: "analysis_tasks 状态分布。",
    options: {
      collection: "analysis_tasks",
      laneLabel: "主解析任务",
      targetUrl: "/admin/content/analysis_tasks",
    },
  },
  {
    id: "4ad98e26-314a-4f6f-a7b1-2d5f85b8e103",
    dashboard: PARSE_RUN_DASHBOARD_ID,
    name: "Overview 任务状态",
    icon: "summarize",
    type: "claread-parse-run-task-status",
    position_x: 55,
    position_y: 1,
    width: 18,
    height: 16,
    color: "#11795B",
    show_header: true,
    note: "analysis_overview_tasks 状态分布。",
    options: {
      collection: "analysis_overview_tasks",
      laneLabel: "Overview 任务",
      targetUrl: "/admin/content/analysis_overview_tasks",
    },
  },
  {
    id: "4ad98e26-314a-4f6f-a7b1-2d5f85b8e104",
    dashboard: PARSE_RUN_DASHBOARD_ID,
    name: "解析 Usage",
    icon: "toll",
    type: "claread-parse-run-usage-total",
    position_x: 1,
    position_y: 17,
    width: 36,
    height: 12,
    color: "#0F6CBD",
    show_header: true,
    note: "analysis_full / analysis_overview_hint / rag_embedding / rag_rerank usage 汇总。",
    options: {
      targetUrl: "/admin/content/ai_usage_events",
      breakdownMode: "capability",
    },
  },
  {
    id: "4ad98e26-314a-4f6f-a7b1-2d5f85b8e105",
    dashboard: PARSE_RUN_DASHBOARD_ID,
    name: "最近失败任务",
    icon: "error",
    type: "claread-parse-run-recent-failures",
    position_x: 1,
    position_y: 1,
    width: 36,
    height: 16,
    color: "#BE123C",
    show_header: true,
    note: "合并主解析与 Overview lane 的最近失败任务。",
    options: {
      endpointUrl: "/parse-run-observability/recent-failures?limit=5",
    },
  },
  {
    id: "4ad98e26-314a-4f6f-a7b1-2d5f85b8e106",
    dashboard: PARSE_RUN_DASHBOARD_ID,
    name: "模型 Usage 分布",
    icon: "memory",
    type: "claread-parse-run-usage-total",
    position_x: 37,
    position_y: 17,
    width: 36,
    height: 12,
    color: "#30445F",
    show_header: true,
    note: "按 model_provider / model_name 聚合解析与 RAG usage。",
    options: {
      targetUrl: "/admin/content/ai_usage_events",
      breakdownMode: "model",
    },
  },
];

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

function restartDirectus() {
  execFileSync("docker", ["restart", DIRECTUS_CONTAINER], { stdio: "pipe" });
}

async function waitForDirectusReady() {
  const attempts = 30;

  for (let index = 0; index < attempts; index += 1) {
    try {
      const response = await fetch(`${DIRECTUS_URL}/server/ping`);
      if (response.ok) {
        return;
      }
    } catch {
      // Directus is still restarting.
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  throw new Error("Directus did not become ready after metadata sync restart.");
}

async function login() {
  if (!DIRECTUS_EMAIL || !DIRECTUS_PASSWORD) {
    throw new Error(
      "Directus metadata sync requires DIRECTUS_* / ADMIN_* credentials or a running local Directus container.",
    );
  }

  const response = await fetch(`${DIRECTUS_URL}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email: DIRECTUS_EMAIL,
      password: DIRECTUS_PASSWORD,
    }),
  });

  if (!response.ok) {
    throw new Error(`Directus login failed: ${response.status} ${await response.text()}`);
  }

  const payload = await response.json();
  const token = payload?.data?.access_token;
  if (!token) {
    throw new Error("Directus login succeeded but access token was missing.");
  }

  return token;
}

async function request(token, method, path, body) {
  const response = await fetch(`${DIRECTUS_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: body == null ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`${method} ${path} failed: ${response.status} ${await response.text()}`);
  }

  if (response.status === 204) return null;
  return response.json();
}

function buildCleanupSql() {
  const collectionList = COLLECTIONS.map((item) => sqlLiteral(item.collection)).join(", ");
  const bookmarkList = GLOBAL_BOOKMARKS.map((item) => sqlLiteral(item.bookmark)).join(", ");
  const dashboardPanelCleanupSql = RESET_PARSE_RUN_DASHBOARD
    ? `
    DELETE FROM directus_panels
    WHERE dashboard IN (${DASHBOARDS.map((item) => sqlLiteral(item.id)).join(", ")})
       OR dashboard IN (
        SELECT id
        FROM directus_dashboards
        WHERE name IN (${DASHBOARDS.map((item) => sqlLiteral(item.name)).join(", ")})
      );
`
    : "";
  return `
    ${dashboardPanelCleanupSql}

    DELETE FROM directus_presets
    WHERE collection IN (${collectionList})
      AND bookmark IS NULL
      AND "user" IS NULL
      AND role IS NULL;

    DELETE FROM directus_presets
    WHERE collection IN (${collectionList})
      AND bookmark IN (${bookmarkList})
      AND "user" IS NULL
      AND role IS NULL;

    DELETE FROM directus_presets
    WHERE collection IN (${collectionList})
      AND bookmark IS NULL
      AND role IS NULL
      AND "user" = (
        SELECT id
        FROM directus_users
        WHERE email = ${sqlLiteral(DIRECTUS_RESET_PRESET_EMAIL)}
        LIMIT 1
      );

    DELETE FROM directus_fields
    WHERE collection IN (${collectionList});

    DELETE FROM directus_relations
    WHERE many_collection IN (${collectionList})
       OR one_collection IN (${collectionList});

    INSERT INTO directus_collections (collection, accountability, collapse)
    VALUES ${COLLECTIONS.map((item) => `(${sqlLiteral(item.collection)}, 'all', 'open')`).join(", ")}
    ON CONFLICT (collection) DO NOTHING;
  `;
}

function buildDashboardsInsertSql() {
  return `
    INSERT INTO directus_dashboards (
      id,
      name,
      icon,
      note,
      color
    )
    VALUES
      ${DASHBOARDS.map(
        (item) =>
          `(${sqlLiteral(item.id)}, ${sqlLiteral(item.name)}, ${sqlLiteral(item.icon)}, ${sqlLiteral(item.note)}, ${sqlLiteral(item.color)})`,
      ).join(",\n      ")}
    ON CONFLICT (id) DO UPDATE SET
      name = EXCLUDED.name,
      icon = EXCLUDED.icon,
      note = EXCLUDED.note,
      color = EXCLUDED.color;
  `;
}

function buildPanelsInsertSql() {
  return `
    INSERT INTO directus_panels (
      id,
      dashboard,
      name,
      icon,
      color,
      show_header,
      note,
      type,
      position_x,
      position_y,
      width,
      height,
      options
    )
    VALUES
      ${DASHBOARD_PANELS.map(
        (item) =>
          `(${sqlLiteral(item.id)}, ${sqlLiteral(item.dashboard)}, ${sqlLiteral(item.name)}, ${sqlLiteral(item.icon)}, ${sqlLiteral(item.color)}, ${item.show_header ? "TRUE" : "FALSE"}, ${sqlLiteral(item.note)}, ${sqlLiteral(item.type)}, ${item.position_x}, ${item.position_y}, ${item.width}, ${item.height}, ${sqlLiteral(JSON.stringify(item.options))}::json)`,
      ).join(",\n      ")}
    ON CONFLICT (id) DO UPDATE SET
      dashboard = EXCLUDED.dashboard,
      name = EXCLUDED.name,
      icon = EXCLUDED.icon,
      color = EXCLUDED.color,
      show_header = EXCLUDED.show_header,
      note = EXCLUDED.note,
      type = EXCLUDED.type,
      options = EXCLUDED.options;
  `;
}

function buildRelationsInsertSql() {
  return `
    INSERT INTO directus_relations (
      many_collection,
      many_field,
      one_collection,
      one_field,
      one_deselect_action
    )
    VALUES
      ${RELATIONS.map(
        (item) =>
          `(${sqlLiteral(item.many_collection)}, ${sqlLiteral(item.many_field)}, ${sqlLiteral(item.one_collection)}, ${sqlLiteral(item.one_field)}, ${sqlLiteral(item.one_deselect_action)})`,
      ).join(",\n      ")}
    ;
  `;
}

function buildPresetsInsertSql() {
  return `
    INSERT INTO directus_presets (
      bookmark,
      "user",
      role,
      collection,
      layout,
      layout_query,
      layout_options,
      refresh_interval,
      filter,
      icon,
      color
    )
    VALUES
      ${DEFAULT_PRESETS.map(
        (item) =>
          `(NULL, NULL, NULL, ${sqlLiteral(item.collection)}, ${sqlLiteral(item.layout)}, ${sqlLiteral(JSON.stringify(item.layout_query))}::json, NULL, NULL, NULL, 'bookmark', NULL)`,
      ).join(",\n      ")}
    ;
  `;
}

function buildBookmarksInsertSql() {
  return `
    INSERT INTO directus_presets (
      bookmark,
      "user",
      role,
      collection,
      layout,
      layout_query,
      layout_options,
      refresh_interval,
      filter,
      icon,
      color
    )
    VALUES
      ${GLOBAL_BOOKMARKS.map(
        (item) =>
          `(${sqlLiteral(item.bookmark)}, NULL, NULL, ${sqlLiteral(item.collection)}, ${sqlLiteral(item.layout)}, ${sqlLiteral(JSON.stringify(item.layout_query))}::json, NULL, NULL, ${sqlLiteral(JSON.stringify(item.filter))}::json, 'bookmark', NULL)`,
      ).join(",\n      ")}
    ;
  `;
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
}

async function syncFields(token) {
  for (const item of FIELD_METADATA) {
    await request(token, "PATCH", `/fields/${item.collection}/${item.field}`, {
      meta: item.meta,
    });
  }
}

async function createAliasFields(token) {
  for (const item of ALIAS_FIELDS) {
    await request(token, "POST", `/fields/${item.collection}`, {
      field: item.field,
      type: "alias",
      meta: {
        special: ["o2m"],
        interface: "list-o2m",
        readonly: true,
        hidden: false,
        sort: item.sort,
        width: "full",
        note: item.note,
        ...(item.meta ?? {}),
      },
      schema: null,
    });
  }
}

async function verify(token) {
  const checks = [
    ["/collections/analysis_records", "analysis_records collection"],
    ["/collections/analysis_tasks", "analysis_tasks collection"],
    ["/collections/analysis_debug_snapshots", "analysis_debug_snapshots collection"],
    ["/fields/analysis_records/id", "analysis_records.id field metadata"],
    ["/fields/analysis_records/analysis_status", "analysis_records.analysis_status display metadata"],
    ["/fields/analysis_records/client_record_id", "analysis_records.client_record_id source display metadata"],
    ["/fields/analysis_records/source_text", "analysis_records.source_text preview metadata"],
    ["/fields/analysis_records/request_payload_json", "analysis_records.request_payload_json metadata"],
    ["/fields/analysis_records/result", "analysis_records.result alias field"],
    ["/fields/analysis_records/analysis_tasks", "analysis_records.analysis_tasks alias field"],
    ["/fields/analysis_records/debug_snapshots", "analysis_records.debug_snapshots alias field"],
    ["/fields/analysis_results/record_id", "analysis_results.record_id field metadata"],
    ["/fields/analysis_results/render_scene_json", "analysis_results.render_scene_json display metadata"],
    ["/fields/analysis_tasks/id", "analysis_tasks.id field metadata"],
    ["/fields/analysis_tasks/status", "analysis_tasks.status display metadata"],
    ["/fields/analysis_tasks/events", "analysis_tasks.events alias display metadata"],
    ["/fields/analysis_tasks/debug_snapshot", "analysis_tasks.debug_snapshot alias field"],
    ["/fields/analysis_tasks/analysis_record_id", "analysis_tasks.analysis_record_id relation field"],
    ["/fields/analysis_overview_tasks/id", "analysis_overview_tasks.id field metadata"],
    ["/fields/analysis_overview_tasks/status", "analysis_overview_tasks.status display metadata"],
    ["/fields/analysis_task_events/id", "analysis_task_events.id field metadata"],
    ["/fields/analysis_task_events/event_type", "analysis_task_events.event_type display metadata"],
    ["/fields/analysis_overview_task_events/id", "analysis_overview_task_events.id field metadata"],
    ["/fields/analysis_overview_task_events/event_type", "analysis_overview_task_events.event_type display metadata"],
    ["/fields/ai_usage_events/id", "ai_usage_events.id field metadata"],
    ["/fields/ai_usage_events/input_tokens", "ai_usage_events.input_tokens display metadata"],
    ["/fields/ai_usage_events/output_tokens", "ai_usage_events.output_tokens display metadata"],
    ["/fields/ai_usage_events/total_tokens", "ai_usage_events.total_tokens display metadata"],
    ["/fields/ai_usage_events/billed_points", "ai_usage_events.billed_points display metadata"],
    ["/fields/ai_usage_events/capability_code", "ai_usage_events.capability_code display metadata"],
    ["/fields/ai_usage_events/usage_scope", "ai_usage_events.usage_scope display metadata"],
    ["/fields/ai_usage_events/billing_mode", "ai_usage_events.billing_mode display metadata"],
    ["/fields/analysis_debug_snapshots/id", "analysis_debug_snapshots.id field metadata"],
    ["/fields/analysis_debug_snapshots/task_id", "analysis_debug_snapshots.task_id relation field"],
    ["/fields/analysis_debug_snapshots/runtime_summary_json", "analysis_debug_snapshots.runtime_summary_json metadata"],
    ["/relations/analysis_tasks/analysis_record_id", "analysis_tasks.analysis_record_id relation"],
    ["/relations/ai_usage_events/task_id", "ai_usage_events.task_id relation"],
    ["/relations/analysis_debug_snapshots/task_id", "analysis_debug_snapshots.task_id relation"],
    ["/presets", "presets endpoint"],
    [`/dashboards/${PARSE_RUN_DASHBOARD_ID}`, "Parse Run Observability dashboard"],
  ];

  for (const [path, label] of checks) {
    await request(token, "GET", path);
    process.stdout.write(`Verified ${label}\n`);
  }
}

async function main() {
  runSql(buildCleanupSql());

  let token = await login();

  await syncCollections(token);
  await syncFields(token);
  await createAliasFields(token);

  runSql(buildRelationsInsertSql());
  runSql(buildPresetsInsertSql());
  runSql(buildBookmarksInsertSql());
  runSql(buildDashboardsInsertSql());
  runSql(buildPanelsInsertSql());

  // Relation metadata is written through SQL for local bootstrap convenience.
  // Restart Directus so the running schema cache picks up virtual O2M alias fields.
  restartDirectus();
  await waitForDirectusReady();
  token = await login();

  await verify(token);

  process.stdout.write("Parse run observability metadata sync complete.\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error.message}\n`);
  process.exit(1);
});
