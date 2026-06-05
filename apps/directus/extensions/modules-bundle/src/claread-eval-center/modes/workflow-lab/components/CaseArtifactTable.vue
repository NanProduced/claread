<script setup>
import { computed } from "vue";

const emit = defineEmits(["select"]);

function tone(row) {
  if (row.adapter_status === "failed" || row.hard_failures > 0) return "danger";
  if (row.soft_failures > 0 || row.warning_count > 0) return "warning";
  return "success";
}

function dropCount(row) {
  if (typeof row.drop_count === "number") return row.drop_count;
  if (Array.isArray(row.drop_log)) return row.drop_log.length;
  return null;
}

function severityScore(row) {
  const outputCount = (row.translation_count ?? 0) + (row.inline_mark_count ?? 0) + (row.sentence_entry_count ?? 0);
  if (row.adapter_status === "failed" || (row.hard_failures ?? 0) > 0) return 5000 + (row.hard_failures ?? 0);
  if (row.adapter_status === "timeout") return 4500;
  if ((row.soft_failures ?? 0) > 0) return 3000 + (row.soft_failures ?? 0);
  if ((row.warning_count ?? 0) > 0) return 2000 + (row.warning_count ?? 0);
  if ((dropCount(row) ?? 0) > 0) return 1000 + (dropCount(row) ?? 0);
  if (outputCount === 0) return 900;
  return 0;
}

const props = defineProps({
  cases: { type: Array, default: () => [] },
  selectedCaseId: { type: String, default: "" },
});

const sortedCases = computed(() => [...props.cases].sort((a, b) => {
  const delta = severityScore(b) - severityScore(a);
  if (delta !== 0) return delta;
  return String(a.case_id || "").localeCompare(String(b.case_id || ""));
}));
</script>

<template>
  <div class="case-table">
    <table>
      <thead>
        <tr>
          <th scope="col" title="差异句 ID。点击后在右侧查看完整证据。">差异句</th>
          <th scope="col" title="adapter 执行状态。">状态</th>
          <th scope="col" title="该差异句收集到的 warnings 数量。">Warn</th>
          <th scope="col" title="该差异句在证据准备过程中被丢弃的条目数；后端尚未暴露时显示 —。">Drop</th>
          <th scope="col" title="逐句翻译条目数量。" class="col-numeric">Trans</th>
          <th scope="col" title="行内标注数量。" class="col-numeric">Marks</th>
          <th scope="col" title="句子条目数量。" class="col-numeric">Entries</th>
          <th scope="col" title="打开证据查看器。">证据</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in sortedCases"
          :key="row.case_id"
          :class="{ active: row.case_id === props.selectedCaseId }"
        >
          <td data-label="差异句">
            <button
              type="button"
              class="case-link"
              :aria-current="row.case_id === props.selectedCaseId ? 'true' : undefined"
              @click="emit('select', row.case_id)"
            >
              {{ row.case_id }}
            </button>
          </td>
          <td data-label="运行状态"><span :class="`status-pill is-${tone(row)}`">{{ row.adapter_status || "—" }}</span></td>
          <td data-label="Warnings">{{ row.warning_count ?? 0 }}</td>
          <td data-label="Drop">{{ dropCount(row) ?? "—" }}</td>
          <td data-label="Translations">{{ row.translation_count ?? 0 }}</td>
          <td data-label="Marks">{{ row.inline_mark_count ?? 0 }}</td>
          <td data-label="Entries">{{ row.sentence_entry_count ?? 0 }}</td>
          <td data-label="证据">
            <button
              type="button"
              class="evidence-link"
              :aria-current="row.case_id === props.selectedCaseId ? 'true' : undefined"
              @click="emit('select', row.case_id)"
            >
              {{ row.case_id === props.selectedCaseId ? "正在查看" : "查看证据" }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="props.cases.length === 0" class="empty">当前没有可展示的差异句。</p>
  </div>
</template>

<style scoped>
.case-table {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border-bottom: 1px solid var(--theme--border-color);
  padding: 8px 10px;
  text-align: left;
  vertical-align: middle;
}

th {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  white-space: nowrap;
}

th.col-numeric,
td.col-numeric {
  text-align: right;
}

tbody tr {
  transition: background 0.12s ease;
}

tbody tr:hover {
  background: color-mix(in srgb, var(--theme--primary) 3%, var(--theme--background));
}

tbody tr.active {
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
}

.case-link,
.evidence-link {
  border: 0;
  background: transparent;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 0;
}

.case-link[aria-current="true"],
.evidence-link[aria-current="true"] {
  text-decoration: underline;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  background: var(--theme--background);
}

.status-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 6%, var(--theme--background));
}

.status-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 6%, var(--theme--background));
}

.status-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 6%, var(--theme--background));
}

.empty {
  margin: 0;
  padding: 16px;
  color: var(--theme--foreground-subdued);
}

@media (max-width: 900px) {
  .case-table {
    overflow: visible;
    border: 0;
  }

  table,
  thead,
  tbody,
  tr,
  td {
    display: block;
    width: 100%;
  }

  thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }

  tbody {
    display: grid;
    gap: 10px;
  }

  tr {
    border: 1px solid var(--theme--border-color);
    border-radius: 8px;
    overflow: hidden;
    background: var(--theme--background);
  }

  td {
    display: grid;
    grid-template-columns: minmax(96px, 0.8fr) minmax(0, 1fr);
    gap: 10px;
    border-bottom: 1px solid var(--theme--border-color);
  }

  td:last-child {
    border-bottom: 0;
  }

  td::before {
    content: attr(data-label);
    color: var(--theme--foreground-subdued);
    font-size: 12px;
    font-weight: 700;
  }
}
</style>
