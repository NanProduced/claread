<script setup>
import { computed } from "vue";

const props = defineProps({
  requests: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedRunId: { type: String, default: "" },
});
const emit = defineEmits(["refresh", "select-run", "cancel", "retry"]);

function statusTone(status) {
  if (status === "succeeded" || status === "complete") return "success";
  if (status === "failed" || status === "total_failure" || status === "cancelled") return "danger";
  if (status === "queued" || status === "running" || status === "partial_failure") return "warning";
  return "neutral";
}

function statusLabel(status) {
  const map = {
    succeeded: "已完成",
    complete: "已完成",
    failed: "失败",
    cancelled: "已取消",
    total_failure: "全部失败",
    partial_failure: "部分失败",
    queued: "排队中",
    running: "运行中",
  };
  return map[status] || status || "未知";
}

const actionable = computed(() =>
  props.requests.filter((item) => item.cancelable || item.retryable).slice(0, 4),
);

function statusRank(status) {
  if (status === "running") return 0;
  if (status === "queued") return 1;
  if (status === "partial_failure") return 2;
  if (status === "failed" || status === "total_failure") return 3;
  if (status === "cancelled") return 4;
  return 5;
}

function sortRows(rows) {
  return [...rows].sort((a, b) => {
    const statusDelta = statusRank(a.status) - statusRank(b.status);
    if (statusDelta !== 0) return statusDelta;
    const aTime = Date.parse(a.created_at || "") || 0;
    const bTime = Date.parse(b.created_at || "") || 0;
    if (aTime !== bTime) return bTime - aTime;
    return String(a.run_id || "").localeCompare(String(b.run_id || ""));
  });
}

const groupedRuns = computed(() => {
  const inFlight = [];
  const history = [];
  for (const row of props.requests) {
    if (row.status === "queued" || row.status === "running" || row.status === "partial_failure") {
      inFlight.push(row);
    } else {
      history.push(row);
    }
  }
  return [
    { key: "in_flight", label: "进行中 / 排队中", rows: sortRows(inFlight) },
    { key: "history", label: "已完成 / 失败 / 已取消", rows: sortRows(history) },
  ].filter((group) => group.rows.length > 0);
});

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
</script>

<template>
  <section class="runs-list">
    <header>
      <div>
        <p>Runs</p>
        <h2>数据集验证运行</h2>
      </div>
      <button type="button" :disabled="loading" title="刷新运行状态。" @click="emit('refresh')">
        {{ loading ? "刷新中" : "刷新" }}
      </button>
    </header>

    <p v-if="!loading && requests.length === 0" class="empty">暂无运行记录。在右侧发起一次数据集验证。</p>

    <div v-else class="groups" role="list" aria-label="运行列表">
      <section v-for="group in groupedRuns" :key="group.key" class="run-group">
        <header class="group-head">
          <strong>{{ group.label }}</strong>
          <small>{{ group.rows.length }} 条</small>
        </header>
        <div class="request-list">
          <button
            v-for="row in group.rows"
            :key="row.id || row.run_id"
            type="button"
            class="request-item"
            :class="{ active: row.run_id === selectedRunId }"
            :aria-selected="row.run_id === selectedRunId"
            role="listitem"
            @click="emit('select-run', row.run_id)"
          >
            <div class="row-main">
              <div class="row-head">
                <strong class="run-id">{{ row.run_id }}</strong>
                <span class="status-pill" :class="`is-${statusTone(row.status)}`">{{ statusLabel(row.status) }}</span>
              </div>
              <div class="row-meta">
                <span class="meta-pill">{{ row.prompt_variant_id || "baseline" }}</span>
                <span class="meta-divider">·</span>
                <span class="meta-dataset">{{ row.dataset_id || "—" }}</span>
                <span v-if="row.learning_case_count" class="meta-divider">·</span>
                <span v-if="row.learning_case_count" class="meta-cases">{{ row.learning_case_count }} cases</span>
                <span v-if="row.created_at" class="meta-divider">·</span>
                <span v-if="row.created_at" class="meta-time">{{ formatTime(row.created_at) }}</span>
              </div>
            </div>
          </button>
        </div>
      </section>
    </div>

    <footer v-if="actionable.length" class="runs-actions">
      <button
        v-for="row in actionable"
        :key="`action-${row.id || row.run_id}`"
        type="button"
        @click="row.cancelable ? emit('cancel', row) : emit('retry', row)"
      >
        {{ row.cancelable ? "取消" : "重试" }} {{ row.run_id }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.runs-list {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  min-height: 0;
  padding: 14px;
}
header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
header p,
.empty,
.row-meta {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
header h2 {
  margin: 2px 0 0;
  font-size: 16px;
}
button {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  padding: 7px 9px;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.empty {
  margin-top: 12px;
  padding: 16px;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  text-align: left;
}
.groups {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}
.run-group {
  display: grid;
  gap: 8px;
}
.group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.group-head small {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}
.request-list {
  display: grid;
  gap: 8px;
  max-height: 60vh;
  overflow: auto;
}
.request-item {
  display: block;
  text-align: left;
  padding: 10px 12px;
}
.request-item.active {
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
}
.request-item:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
}
.row-main {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.run-id {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  background: var(--theme--background);
}
.status-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}
.status-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}
.status-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}
.status-pill.is-neutral {
  color: var(--theme--foreground-subdued);
}
.row-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.meta-pill {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 999px;
  padding: 1px 8px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font-weight: 600;
}
.meta-divider {
  color: var(--theme--foreground-subdued);
}
.meta-dataset,
.meta-cases,
.meta-time {
  color: var(--theme--foreground-subdued);
}
.runs-actions {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}
</style>
