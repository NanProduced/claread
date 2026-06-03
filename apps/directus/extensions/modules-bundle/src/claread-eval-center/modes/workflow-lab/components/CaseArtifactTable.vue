<script setup>
defineProps({
  cases: { type: Array, default: () => [] },
  selectedCaseId: { type: String, default: "" },
});
const emit = defineEmits(["select"]);

function tone(row) {
  if (row.adapter_status === "failed" || row.hard_failures > 0) return "danger";
  if (row.soft_failures > 0 || row.warning_count > 0) return "warning";
  return "success";
}
</script>

<template>
  <div class="case-table">
    <table>
      <thead>
        <tr>
          <th title="Dataset case id。点击后在右侧查看完整 evidence。">Case</th>
          <th title="adapter 执行状态。">状态</th>
          <th title="最终 render_scene 的用户可见状态。">输出状态</th>
          <th title="硬失败数量，通常代表必须处理的问题。">硬失败</th>
          <th title="软失败数量，通常代表需要复查的质量风险。">软失败</th>
          <th title="translations / inline_marks / sentence_entries 数量。">输出数量</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in cases"
          :key="row.case_id"
          :class="{ active: row.case_id === selectedCaseId }"
        >
          <td data-label="Case">
            <button
              type="button"
              class="case-link"
              :aria-current="row.case_id === selectedCaseId ? 'true' : undefined"
              @click="emit('select', row.case_id)"
            >
              {{ row.case_id }}
            </button>
          </td>
          <td data-label="状态"><span :class="tone(row)">{{ row.adapter_status || "-" }}</span></td>
          <td data-label="输出状态">{{ row.user_facing_state || "-" }}</td>
          <td data-label="硬失败">{{ row.hard_failures ?? 0 }}</td>
          <td data-label="软失败">{{ row.soft_failures ?? 0 }}</td>
          <td data-label="输出数量">{{ row.translation_count ?? 0 }} / {{ row.inline_mark_count ?? 0 }} / {{ row.sentence_entry_count ?? 0 }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="cases.length === 0" class="empty">当前没有可展示的 learning case。</p>
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
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
}

th {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

tbody tr.active {
  background: var(--theme--background-subdued);
}

.case-link {
  border: 0;
  background: transparent;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 0;
}

.case-link[aria-current="true"] {
  text-decoration: underline;
}

span {
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 11px;
  font-weight: 700;
}

.success { background: var(--theme--success-background); }
.warning { background: var(--theme--warning-background); }
.danger { background: var(--theme--danger-background); }

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
