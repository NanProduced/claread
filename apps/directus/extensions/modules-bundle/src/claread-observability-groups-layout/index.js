import { defineLayout } from "@directus/extensions-sdk";
import { defineComponent, h, onMounted, ref, watch } from "vue";
import { renderMappedBadge } from "../shared/enum-display.js";
import { formatDateTime, shortId } from "../shared/format.js";

function compactNumber(value) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "0";
  if (Math.abs(numeric) >= 1000000) return `${(numeric / 1000000).toFixed(1)}M`;
  if (Math.abs(numeric) >= 1000) return `${(numeric / 1000).toFixed(1)}K`;
  return `${numeric}`;
}

function compactLatency(value) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return "0 ms";
  if (numeric >= 1000) return `${(numeric / 1000).toFixed(1)} s`;
  return `${numeric} ms`;
}

function flattenQuery(prefix, value, target) {
  if (value == null) return;

  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      flattenQuery(`${prefix}[${index}]`, value[index], target);
    }
    return;
  }

  if (typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      flattenQuery(prefix ? `${prefix}[${key}]` : key, child, target);
    }
    return;
  }

  target.push([prefix, String(value)]);
}

function buildQueryString(query) {
  const entries = [];
  flattenQuery("", query, entries);
  const params = new URLSearchParams();
  for (const [key, value] of entries) {
    params.append(key, value);
  }
  return params.toString();
}

function normalizeItems(value) {
  return Array.isArray(value) ? value : [];
}

function resolveLayoutQuery(layoutQuery) {
  if (
    layoutQuery &&
    typeof layoutQuery === "object" &&
    layoutQuery["claread-observability-groups"] &&
    typeof layoutQuery["claread-observability-groups"] === "object"
  ) {
    return layoutQuery["claread-observability-groups"];
  }

  return layoutQuery ?? {};
}

function buildFetchPath(collection, fields, filter, search, layoutQuery, defaultSort) {
  const query = {
    fields: fields.join(","),
    limit: Number(layoutQuery?.limit) > 0 ? Number(layoutQuery.limit) : 200,
  };

  const sort = layoutQuery?.sort ?? defaultSort;
  if (Array.isArray(sort) && sort.length > 0) {
    query.sort = sort.join(",");
  } else if (typeof sort === "string" && sort) {
    query.sort = sort;
  }

  if (search) query.search = search;
  if (filter && Object.keys(filter).length > 0) query.filter = filter;

  return `/items/${collection}?${buildQueryString(query)}`;
}

function groupItems(items, config) {
  const groups = new Map();

  for (const item of items) {
    const meta = config.groupBy(item);
    if (!groups.has(meta.id)) {
      groups.set(meta.id, {
        meta,
        items: [],
      });
    }

    groups.get(meta.id).items.push(item);
  }

  return Array.from(groups.values());
}

function renderLink(text, href, subdued = false) {
  return h(
    "a",
    {
      href,
      style: {
        color: subdued ? "#64748B" : "#0F172A",
        textDecoration: "none",
      },
    },
    text,
  );
}

function groupBadge(text, tone = "muted") {
  const palette = {
    muted: {
      background: "#F8FAFC",
      border: "#CBD5E1",
      color: "#475569",
    },
    info: {
      background: "#EFF6FF",
      border: "#BFDBFE",
      color: "#1D4ED8",
    },
    accent: {
      background: "#ECFDF5",
      border: "#A7F3D0",
      color: "#047857",
    },
    warning: {
      background: "#FFFBEB",
      border: "#FCD34D",
      color: "#92400E",
    },
  };

  const colors = palette[tone] ?? palette.muted;

  return h(
    "span",
    {
      style: {
        display: "inline-flex",
        alignItems: "center",
        minHeight: "22px",
        padding: "0 10px",
        borderRadius: "999px",
        border: `1px solid ${colors.border}`,
        background: colors.background,
        color: colors.color,
        fontSize: "12px",
        lineHeight: "18px",
        whiteSpace: "nowrap",
      },
    },
    text,
  );
}

function statBadge(text, tone = "muted") {
  const palette = {
    muted: {
      background: "#FFFFFF",
      border: "#CBD5E1",
      color: "#475569",
    },
    info: {
      background: "#EFF6FF",
      border: "#BFDBFE",
      color: "#1D4ED8",
    },
    accent: {
      background: "#ECFDF5",
      border: "#A7F3D0",
      color: "#047857",
    },
    warning: {
      background: "#FFFBEB",
      border: "#FCD34D",
      color: "#92400E",
    },
  };

  const colors = palette[tone] ?? palette.muted;

  return h(
    "span",
    {
      style: {
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "26px",
        padding: "0 10px",
        borderRadius: "10px",
        border: `1px solid ${colors.border}`,
        background: colors.background,
        color: colors.color,
        fontSize: "12px",
        lineHeight: "18px",
        fontWeight: "700",
        whiteSpace: "nowrap",
      },
    },
    text,
  );
}

function rowCell(content, align = "left", emphasis = false) {
  return h(
    "div",
    {
      style: {
        minWidth: "0",
        display: "flex",
        alignItems: "center",
        justifyContent: align === "right" ? "flex-end" : align === "center" ? "center" : "flex-start",
        color: emphasis ? "#0F172A" : "#475569",
        fontSize: "12px",
        lineHeight: "18px",
      },
    },
    content,
  );
}

function rowText(text, align = "left", emphasis = false) {
  return rowCell(
    h(
      "span",
      {
        style: {
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        },
      },
      text,
    ),
    align,
    emphasis,
  );
}

function buildGroupTitleCell(meta, isOpen) {
  const badges = [];

  if (meta.clientRecordId) {
    badges.push(renderMappedBadge(meta.clientRecordId, "client_source_from_id", false));
  }

  if (meta.taskStatus) {
    badges.push(renderMappedBadge(meta.taskStatus, "task_status", true));
  }

  if (meta.taskId) {
    badges.push(groupBadge(`Task ${shortId(meta.taskId, 8)}`, "info"));
  } else if (meta.recordId) {
    badges.push(groupBadge(`Record ${shortId(meta.recordId, 8)}`, "info"));
  }

  return h(
    "div",
    {
      style: {
        display: "flex",
        alignItems: "flex-start",
        gap: "12px",
        minWidth: "0",
      },
    },
    [
      h(
        "span",
        {
          style: {
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: "20px",
            minWidth: "20px",
            color: "#64748B",
            fontSize: "13px",
            lineHeight: "20px",
            transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 120ms ease",
          },
        },
        "▶",
      ),
      h(
        "div",
        {
          style: {
            display: "flex",
            flexDirection: "column",
            gap: "6px",
            minWidth: "0",
          },
        },
        [
          h(
            "strong",
            {
              style: {
                color: "#0F172A",
                fontSize: "13px",
                lineHeight: "20px",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              },
            },
            meta.title,
          ),
          badges.length > 0
            ? h(
                "div",
                {
                  style: {
                    display: "flex",
                    gap: "6px",
                    flexWrap: "wrap",
                    alignItems: "center",
                  },
                },
                badges,
              )
            : null,
        ].filter(Boolean),
      ),
    ].filter(Boolean),
  );
}

function statusSummary(items, field) {
  const counts = new Map();
  for (const item of items) {
    const raw = item?.[field];
    if (!raw) continue;
    counts.set(raw, (counts.get(raw) ?? 0) + 1);
  }

  if (counts.size === 0) return "—";

  return Array.from(counts.entries())
    .map(([key, count]) => `${key} ${count}`)
    .join(" / ");
}

const COLLECTION_CONFIG = {
  analysis_task_events: {
    title: "主解析任务事件分组表",
    subtitle: "按 task 聚合；展开后查看同一 task 的事件流水。",
    fields: [
      "id",
      "event_type",
      "created_at",
      "task_id.id",
      "task_id.status",
      "task_id.analysis_record_id.id",
      "task_id.analysis_record_id.title",
      "task_id.analysis_record_id.client_record_id",
    ],
    defaultSort: ["task_id", "created_at"],
    gridColumns: "minmax(340px, 2.6fr) minmax(220px, 1.5fr) minmax(180px, 1.1fr)",
    headers: ["Task / Article", "Event Type", "Created At"],
    groupBy(item) {
      const task = item.task_id && typeof item.task_id === "object" ? item.task_id : null;
      const record =
        task?.analysis_record_id && typeof task.analysis_record_id === "object"
          ? task.analysis_record_id
          : null;

      return {
        id: `task:${task?.id ?? "unknown"}`,
        taskId: task?.id ?? "",
        taskStatus: task?.status ?? "",
        title: record?.title ?? "未关联文章",
        clientRecordId: record?.client_record_id ?? "",
      };
    },
    renderGroupSummary(group) {
      const last = group.items[group.items.length - 1];

      return [
        buildGroupTitleCell(group.meta, group.isOpen),
        rowCell(statBadge(`${group.items.length} 条事件`, "accent")),
        rowText(formatDateTime(last?.created_at), "left", true),
      ];
    },
    renderChildRow(item) {
      return [
        rowCell(
          h(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                gap: "10px",
                minWidth: "0",
                paddingLeft: "24px",
              },
            },
            [
              h("span", { style: { color: "#94A3B8", fontSize: "14px" } }, "└"),
              renderLink(
                `事件 ${shortId(item.id, 8)}`,
                `/admin/content/analysis_task_events/${encodeURIComponent(item.id)}`,
                true,
              ),
            ],
          ),
        ),
        rowCell(renderMappedBadge(item.event_type, "event_type", true)),
        rowText(formatDateTime(item.created_at), "left", true),
      ];
    },
  },
  analysis_overview_task_events: {
    title: "Overview 任务事件分组表",
    subtitle: "按 overview task 聚合；展开后查看同一 task 的事件流水。",
    fields: [
      "id",
      "event_type",
      "created_at",
      "task_id.id",
      "task_id.status",
      "task_id.analysis_record_id.id",
      "task_id.analysis_record_id.title",
      "task_id.analysis_record_id.client_record_id",
    ],
    defaultSort: ["task_id", "created_at"],
    gridColumns: "minmax(340px, 2.6fr) minmax(220px, 1.5fr) minmax(180px, 1.1fr)",
    headers: ["Task / Article", "Event Type", "Created At"],
    groupBy(item) {
      const task = item.task_id && typeof item.task_id === "object" ? item.task_id : null;
      const record =
        task?.analysis_record_id && typeof task.analysis_record_id === "object"
          ? task.analysis_record_id
          : null;

      return {
        id: `overview-task:${task?.id ?? "unknown"}`,
        taskId: task?.id ?? "",
        taskStatus: task?.status ?? "",
        title: record?.title ?? "未关联文章",
        clientRecordId: record?.client_record_id ?? "",
      };
    },
    renderGroupSummary(group) {
      const last = group.items[group.items.length - 1];

      return [
        buildGroupTitleCell(group.meta, group.isOpen),
        rowCell(statBadge(`${group.items.length} 条事件`, "accent")),
        rowText(formatDateTime(last?.created_at), "left", true),
      ];
    },
    renderChildRow(item) {
      return [
        rowCell(
          h(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                gap: "10px",
                minWidth: "0",
                paddingLeft: "24px",
              },
            },
            [
              h("span", { style: { color: "#94A3B8", fontSize: "14px" } }, "└"),
              renderLink(
                `事件 ${shortId(item.id, 8)}`,
                `/admin/content/analysis_overview_task_events/${encodeURIComponent(item.id)}`,
                true,
              ),
            ],
          ),
        ),
        rowCell(renderMappedBadge(item.event_type, "event_type", true)),
        rowText(formatDateTime(item.created_at), "left", true),
      ];
    },
  },
  ai_usage_events: {
    title: "AI Usage 分组表",
    subtitle: "优先按 task 聚合；没有 task 的 usage 再按 record 聚合。",
    fields: [
      "id",
      "capability_code",
      "status",
      "input_tokens",
      "output_tokens",
      "billed_points",
      "usage_scope",
      "billing_mode",
      "latency_ms",
      "created_at",
      "record_id.id",
      "record_id.title",
      "record_id.client_record_id",
      "task_id.id",
      "task_id.status",
      "task_id.analysis_record_id.id",
      "task_id.analysis_record_id.title",
      "task_id.analysis_record_id.client_record_id",
    ],
    defaultSort: ["-created_at"],
    gridColumns:
      "minmax(320px, 2.2fr) minmax(180px, 1.2fr) minmax(170px, 1.2fr) minmax(110px, 0.8fr) minmax(110px, 0.8fr) minmax(110px, 0.8fr) minmax(130px, 0.9fr) minmax(130px, 0.9fr) minmax(110px, 0.8fr) minmax(170px, 1fr)",
    headers: [
      "Group",
      "Capability",
      "Status",
      "Input",
      "Output",
      "Points",
      "Scope",
      "Billing",
      "Latency",
      "Created At",
    ],
    groupBy(item) {
      const task = item.task_id && typeof item.task_id === "object" ? item.task_id : null;
      const taskRecord =
        task?.analysis_record_id && typeof task.analysis_record_id === "object"
          ? task.analysis_record_id
          : null;
      const record = item.record_id && typeof item.record_id === "object" ? item.record_id : null;

      if (task?.id) {
        return {
          id: `task:${task.id}`,
          taskId: task.id,
          taskStatus: task.status ?? "",
          title: taskRecord?.title ?? record?.title ?? "未关联文章",
          clientRecordId: taskRecord?.client_record_id ?? record?.client_record_id ?? "",
          recordId: taskRecord?.id ?? record?.id ?? "",
        };
      }

      return {
        id: `record:${record?.id ?? "unknown"}`,
        taskId: "",
        taskStatus: "",
        title: record?.title ?? "未关联文章",
        clientRecordId: record?.client_record_id ?? "",
        recordId: record?.id ?? "",
      };
    },
    renderGroupSummary(group) {
      const input = group.items.reduce((sum, item) => sum + Number(item.input_tokens ?? 0), 0);
      const output = group.items.reduce((sum, item) => sum + Number(item.output_tokens ?? 0), 0);
      const billed = group.items.reduce((sum, item) => sum + Number(item.billed_points ?? 0), 0);
      const maxLatency = group.items.reduce(
        (current, item) => Math.max(current, Number(item.latency_ms ?? 0)),
        0,
      );
      const last = group.items[0];

      return [
        buildGroupTitleCell(group.meta, group.isOpen),
        rowCell(renderMappedBadge(group.items[0]?.capability_code ?? "", "capability_code", false)),
        rowCell(statBadge(statusSummary(group.items, "status"), "info")),
        rowCell(statBadge(compactNumber(input), "accent"), "right"),
        rowCell(statBadge(compactNumber(output), "accent"), "right"),
        rowCell(statBadge(compactNumber(billed), "warning"), "right"),
        rowText(statusSummary(group.items, "usage_scope"), "left", true),
        rowText(statusSummary(group.items, "billing_mode"), "left", true),
        rowCell(statBadge(compactLatency(maxLatency), "muted"), "right"),
        rowText(formatDateTime(last?.created_at), "left", true),
      ];
    },
    renderChildRow(item) {
      return [
        rowCell(
          h(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                gap: "10px",
                minWidth: "0",
                paddingLeft: "24px",
              },
            },
            [
              h("span", { style: { color: "#94A3B8", fontSize: "14px" } }, "└"),
              renderLink(
                `Usage ${shortId(item.id, 8)}`,
                `/admin/content/ai_usage_events/${encodeURIComponent(item.id)}`,
                true,
              ),
            ],
          ),
        ),
        rowCell(renderMappedBadge(item.capability_code, "capability_code", true)),
        rowCell(renderMappedBadge(item.status, "usage_status", true)),
        rowText(`${compactNumber(item.input_tokens)} tok`, "right", true),
        rowText(`${compactNumber(item.output_tokens)} tok`, "right", true),
        rowText(`${compactNumber(item.billed_points)} pts`, "right", true),
        rowCell(renderMappedBadge(item.usage_scope, "usage_scope", false)),
        rowCell(renderMappedBadge(item.billing_mode, "billing_mode", false)),
        rowText(compactLatency(item.latency_ms), "right", true),
        rowText(formatDateTime(item.created_at), "left", true),
      ];
    },
  },
};

function renderTableRow(columns, content, style = {}, rowKey = undefined) {
  return h(
    "div",
    {
      key: rowKey,
      style: {
        display: "grid",
        gridTemplateColumns: columns,
        gap: "0",
        alignItems: "stretch",
        ...style,
      },
    },
    content.map((cell, index) =>
      h(
        "div",
        {
          key: `${index}`,
          style: {
            minWidth: "0",
            padding: "12px 14px",
            borderRight: index === content.length - 1 ? "none" : "1px solid #E2E8F0",
          },
        },
        [cell],
      ),
    ),
  );
}

const LayoutComponent = defineComponent({
  inheritAttrs: false,
  props: {
    collection: {
      type: String,
      required: true,
    },
    layoutQuery: {
      type: Object,
      default: () => ({}),
    },
    filter: {
      type: Object,
      default: () => ({}),
    },
    search: {
      type: String,
      default: "",
    },
  },
  setup(props) {
    const loading = ref(false);
    const items = ref([]);
    const error = ref("");
    const openGroupIds = ref([]);
    const hasInitializedOpenState = ref(false);

    const load = async () => {
      const config = COLLECTION_CONFIG[props.collection];
      if (!config) {
        items.value = [];
        error.value = "当前 collection 暂未接入 grouped layout。";
        return;
      }

      loading.value = true;
      error.value = "";

      try {
        const response = await fetch(
          buildFetchPath(
            props.collection,
            config.fields,
            props.filter,
            props.search,
            resolveLayoutQuery(props.layoutQuery),
            config.defaultSort,
          ),
          {
            credentials: "include",
            headers: {
              Accept: "application/json",
            },
          },
        );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const payload = await response.json();
        const nextItems = normalizeItems(payload?.data);
        const nextGroups = groupItems(nextItems, config);
        const nextGroupIds = new Set(nextGroups.map((group) => group.meta.id));
        items.value = nextItems;

        if (nextGroups.length === 0) {
          openGroupIds.value = [];
        } else if (!hasInitializedOpenState.value) {
          openGroupIds.value = [nextGroups[0].meta.id];
          hasInitializedOpenState.value = true;
        } else {
          openGroupIds.value = openGroupIds.value.filter((groupId) => nextGroupIds.has(groupId));
        }
      } catch (cause) {
        items.value = [];
        error.value = `读取分组数据失败：${cause instanceof Error ? cause.message : "unknown error"}`;
      } finally {
        loading.value = false;
      }
    };

    onMounted(load);
    watch(() => [props.collection, props.layoutQuery, props.filter, props.search], load, {
      deep: true,
    });

    const toggleGroup = (groupId) => {
      const current = new Set(openGroupIds.value);
      if (current.has(groupId)) {
        current.delete(groupId);
      } else {
        current.add(groupId);
      }
      openGroupIds.value = Array.from(current);
    };

    return () => {
      const config = COLLECTION_CONFIG[props.collection];

      if (!config) {
        return h(
          "div",
          {
            style: {
              padding: "20px",
              color: "#64748B",
              fontSize: "13px",
              lineHeight: "20px",
            },
          },
          "当前 grouped table 只支持 analysis_task_events 和 ai_usage_events。",
        );
      }

      const groups = groupItems(items.value, config).map((group, index) => {
        return {
          ...group,
          id: group.meta.id,
          isOpen: openGroupIds.value.includes(group.meta.id),
        };
      });

      return h(
        "div",
        {
          style: {
            display: "flex",
            flexDirection: "column",
            gap: "14px",
            padding: "16px 20px 28px",
            background: "#F8FAFC",
            minHeight: "100%",
          },
        },
        [
          h(
            "div",
            {
              style: {
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-end",
                gap: "16px",
                flexWrap: "wrap",
              },
            },
            [
              h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } }, [
                h(
                  "strong",
                  {
                    style: {
                      color: "#0F172A",
                      fontSize: "18px",
                      lineHeight: "26px",
                    },
                  },
                  config.title,
                ),
                h(
                  "span",
                  {
                    style: {
                      color: "#64748B",
                      fontSize: "12px",
                      lineHeight: "18px",
                    },
                  },
                  props.search ? `当前搜索：${props.search}` : config.subtitle,
                ),
              ]),
              h(
                "div",
                {
                  style: {
                    display: "flex",
                    gap: "8px",
                    flexWrap: "wrap",
                  },
                },
                [
                  groupBadge(`分组 ${groups.length}`, "accent"),
                  groupBadge(`明细 ${items.value.length}`, "info"),
                ],
              ),
            ],
          ),
          loading.value
            ? h(
                "div",
                {
                  style: {
                    color: "#64748B",
                    fontSize: "13px",
                    lineHeight: "20px",
                  },
                },
                "正在加载 grouped 数据…",
              )
            : null,
          error.value
            ? h(
                "div",
                {
                  style: {
                    padding: "12px 14px",
                    borderRadius: "12px",
                    border: "1px solid #FECACA",
                    background: "#FEF2F2",
                    color: "#B91C1C",
                    fontSize: "12px",
                    lineHeight: "18px",
                  },
                },
                error.value,
              )
            : null,
          h(
            "div",
            {
              style: {
                border: "1px solid #D9E2EC",
                borderRadius: "18px",
                overflow: "hidden",
                background: "#FFFFFF",
              },
            },
            [
              renderTableRow(
                config.gridColumns,
                config.headers.map((label) =>
                  h(
                    "span",
                    {
                      style: {
                        color: "#64748B",
                        fontSize: "11px",
                        lineHeight: "16px",
                        letterSpacing: "0.04em",
                        textTransform: "uppercase",
                      },
                    },
                    label,
                  ),
                ),
                {
                  background: "#F8FAFC",
                  borderBottom: "1px solid #E2E8F0",
                },
                "header-row",
              ),
              !loading.value && !error.value && groups.length === 0
                ? h(
                    "div",
                    {
                      style: {
                        padding: "18px 20px",
                        color: "#64748B",
                        fontSize: "13px",
                        lineHeight: "20px",
                      },
                    },
                    "当前筛选条件下没有数据。",
                  )
                : null,
              ...groups.map((group, index) =>
                h(
                  "div",
                  {
                    key: group.id,
                    style: {
                      borderTop: index === 0 ? "none" : "1px solid #E2E8F0",
                    },
                  },
                  [
                    h("div", {
                      role: "button",
                      tabindex: 0,
                      onClick: () => toggleGroup(group.id),
                      onKeydown: (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          toggleGroup(group.id);
                        }
                      },
                      style: {
                        display: "block",
                        width: "100%",
                        outline: "none",
                        cursor: "pointer",
                      },
                    }, [
                      renderTableRow(
                        config.gridColumns,
                        config.renderGroupSummary(group),
                        {
                          background: group.isOpen ? "#F8FAFC" : "#FFFFFF",
                          boxShadow: group.isOpen ? "inset 3px 0 0 #3B82F6" : "none",
                        },
                        `group-row:${group.id}`,
                      ),
                    ]),
                    group.isOpen
                      ? h(
                          "div",
                          {
                            key: `group-body:${group.id}`,
                            style: {
                              display: "flex",
                              flexDirection: "column",
                            },
                          },
                          group.items.map((item, itemIndex) =>
                            renderTableRow(
                              config.gridColumns,
                              config.renderChildRow(item),
                              {
                                borderTop: itemIndex === 0 ? "1px solid #E2E8F0" : "1px solid #F1F5F9",
                                background: itemIndex % 2 === 0 ? "#FFFFFF" : "#FCFDFE",
                              },
                              `child-row:${item.id}`,
                            ),
                          ),
                        )
                      : null,
                  ].filter(Boolean),
                ),
              ),
            ].filter(Boolean),
          ),
        ].filter(Boolean),
      );
    };
  },
});

export default defineLayout({
  id: "claread-observability-groups",
  name: "Observability Groups",
  icon: "table_rows_narrow",
  component: LayoutComponent,
  slots: {
    options: () => null,
    sidebar: () => null,
    actions: () => null,
  },
  setup() {
    return {};
  },
});
