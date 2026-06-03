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
          @click="emit('select', row.case_id)"
        >
          <td><button type="button">{{ row.case_id }}</button></td>
          <td><span :class="tone(row)">{{ row.adapter_status || "-" }}</span></td>
          <td>{{ row.user_facing_state || "-" }}</td>
          <td>{{ row.hard_failures ?? 0 }}</td>
          <td>{{ row.soft_failures ?? 0 }}</td>
          <td>{{ row.translation_count ?? 0 }} / {{ row.inline_mark_count ?? 0 }} / {{ row.sentence_entry_count ?? 0 }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="cases.length === 0" class="empty">暂无 case artifact。</p>
  </div>
</template>

<style scoped>
.case-table {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: auto;
}
table {
  width: 100%;
  min-width: 720px;
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
tr {
  cursor: pointer;
}
tbody tr:hover,
tbody tr.active {
  background: var(--theme--background-subdued);
}
button {
  border: 0;
  background: transparent;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  padding: 0;
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
</style>
