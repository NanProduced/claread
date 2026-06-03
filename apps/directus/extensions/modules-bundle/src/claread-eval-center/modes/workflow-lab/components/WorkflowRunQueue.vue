<script setup>
defineProps({
  requests: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedRunId: { type: String, default: "" },
});
const emit = defineEmits(["refresh", "select-run", "cancel", "retry"]);

function statusTone(status) {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "queued" || status === "running") return "warning";
  return "neutral";
}
</script>

<template>
  <section class="queue">
    <header>
      <div>
        <p>运行队列</p>
        <h2>等待中与进行中的回归任务</h2>
      </div>
      <button type="button" :disabled="loading" title="刷新后台 runner bridge 请求状态。" @click="emit('refresh')">
        {{ loading ? "刷新中" : "刷新" }}
      </button>
    </header>

    <div class="request-list">
      <button
        v-for="row in requests"
        :key="row.id || row.run_id"
        type="button"
        class="request-item"
        :class="{ active: row.run_id === selectedRunId }"
        @click="emit('select-run', row.run_id)"
      >
        <span>
          <strong>{{ row.run_id }}</strong>
          <small>{{ row.dataset_id }} / {{ row.prompt_variant_id || "baseline" }}</small>
        </span>
        <em :class="statusTone(row.status)">{{ row.status }}</em>
      </button>
      <p v-if="!loading && requests.length === 0" class="empty">当前没有排队中的回归任务。</p>
    </div>

    <footer class="queue-actions">
      <button
        v-for="row in requests.filter((item) => item.cancelable || item.retryable).slice(0, 4)"
        :key="`action-${row.id}`"
        type="button"
        @click="row.cancelable ? emit('cancel', row) : emit('retry', row)"
      >
        {{ row.cancelable ? "取消" : "重试" }} {{ row.run_id }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.queue {
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
small {
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
.request-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  max-height: 360px;
  overflow: auto;
}
.request-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  text-align: left;
}
.request-item.active {
  border-color: var(--theme--primary);
  background: var(--theme--background-subdued);
}
.request-item strong,
.request-item small {
  display: block;
  overflow-wrap: anywhere;
}
em {
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}
em.success { background: var(--theme--success-background); }
em.warning { background: var(--theme--warning-background); }
em.danger { background: var(--theme--danger-background); }
em.neutral { background: var(--theme--background-subdued); }
.queue-actions {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}
</style>
