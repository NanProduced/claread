import { h } from "vue";

function formatCompact(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: value >= 10000 ? 1 : 0,
  }).format(value);
}

function formatInteger(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0";
  return new Intl.NumberFormat("en-US").format(value);
}

function normalizeValue(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return {
      total_tokens: Number(value.total_tokens ?? 0),
      billed_points: value.billed_points == null ? null : Number(value.billed_points),
      input_tokens: Number(value.input_tokens ?? 0),
      output_tokens: Number(value.output_tokens ?? 0),
      cache_read_tokens: Number(value.cache_read_tokens ?? 0),
      cache_write_tokens: Number(value.cache_write_tokens ?? 0),
      latency_ms: value.latency_ms == null ? null : Number(value.latency_ms),
      status: value.status == null ? null : String(value.status),
    };
  }

  return {
    total_tokens: Number(value ?? 0),
    billed_points: null,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    latency_ms: null,
    status: null,
  };
}

function renderUsageSummary(value, mode) {
  if (mode === "tokens") {
    const tokenValue = Number(value ?? 0);
    const rawText = Number.isNaN(tokenValue) ? "0" : formatInteger(tokenValue);
    return h(
      "div",
      {
        title: `total_tokens: ${rawText}`,
        style: {
          display: "inline-flex",
          alignItems: "center",
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
              fontSize: "12px",
              lineHeight: "18px",
              whiteSpace: "nowrap",
            },
          },
          `${formatCompact(tokenValue)} tok`,
        ),
      ],
    );
  }

  if (mode === "points") {
    const pointsValue = Number(value ?? 0);
    const rawText = Number.isNaN(pointsValue) ? "0" : formatInteger(pointsValue);
    return h(
      "div",
      {
        title: `billed_points: ${rawText}`,
        style: {
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          minWidth: "0",
        },
      },
      [
        h(
          "span",
          {
            style: {
              color: "#475569",
              fontSize: "12px",
              lineHeight: "18px",
              whiteSpace: "nowrap",
            },
          },
          `${formatCompact(pointsValue)} pts`,
        ),
      ],
    );
  }

  const normalized = normalizeValue(value);
  const tokenText = `${formatCompact(normalized.total_tokens)} tok`;
  const pointsText =
    normalized.billed_points == null ? "No pts" : `${formatCompact(normalized.billed_points)} pts`;
  const detailText = [
    `In ${formatCompact(normalized.input_tokens)}`,
    `Out ${formatCompact(normalized.output_tokens)}`,
    `Cache ${formatCompact(normalized.cache_read_tokens + normalized.cache_write_tokens)}`,
    normalized.latency_ms == null ? null : `${formatInteger(normalized.latency_ms)} ms`,
  ]
    .filter(Boolean)
    .join(" / ");

  const titleLines = [
    `total_tokens: ${formatInteger(normalized.total_tokens)}`,
    `billed_points: ${normalized.billed_points == null ? "NULL" : formatInteger(normalized.billed_points)}`,
    `input_tokens: ${formatInteger(normalized.input_tokens)}`,
    `output_tokens: ${formatInteger(normalized.output_tokens)}`,
    `cache_read_tokens: ${formatInteger(normalized.cache_read_tokens)}`,
    `cache_write_tokens: ${formatInteger(normalized.cache_write_tokens)}`,
  ];

  if (normalized.latency_ms != null) {
    titleLines.push(`latency_ms: ${formatInteger(normalized.latency_ms)}`);
  }

  if (normalized.status) {
    titleLines.push(`status: ${normalized.status}`);
  }

  return h(
    "div",
    {
      title: titleLines.join("\n"),
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "2px",
        minWidth: "0",
      },
    },
    [
      h(
        "div",
        {
          style: {
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            flexWrap: "wrap",
          },
        },
        [
          h(
            "strong",
            {
              style: {
                color: "#0F172A",
                fontSize: "12px",
                lineHeight: "18px",
              },
            },
            tokenText,
          ),
          h(
            "span",
            {
              style: {
                color: "#475569",
                fontSize: "12px",
                lineHeight: "18px",
              },
            },
            pointsText,
          ),
        ],
      ),
      h(
        "span",
        {
          style: {
            color: "#64748B",
            fontSize: "11px",
            lineHeight: "16px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          },
        },
        detailText,
      ),
    ],
  );
}

export default {
  id: "claread-usage-summary",
  name: "Claread Usage Summary",
  icon: "toll",
  description: "将 token 或 billed points 压缩成可快速扫描的 usage 显示。",
  types: ["integer", "bigInteger"],
  options: [
    {
      field: "mode",
      type: "string",
      name: "Mode",
      meta: {
        width: "full",
        interface: "select-dropdown",
        options: {
          choices: [
            { text: "Tokens", value: "tokens" },
            { text: "Points", value: "points" },
            { text: "Summary", value: "summary" },
          ],
        },
      },
    },
  ],
  component: function UsageSummaryDisplay({ value, mode }) {
    return renderUsageSummary(value, mode ?? "summary");
  },
};
