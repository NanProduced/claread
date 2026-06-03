<script setup>
import { useApi } from "@directus/extensions-sdk";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import JsonTreeView from "../components/JsonTreeView.vue";
import NodeProbeOutputView from "../components/NodeProbeOutputView.vue";
import ResultBlock from "../components/ResultBlock.vue";
import StatusPill from "../components/StatusPill.vue";
import {
  formatDateTime,
  shortId,
  statusLabel,
  statusTone,
} from "../composables/useEvalFormatting";

const api = useApi();

const props = defineProps({
  initialRunId: { type: String, default: "" },
  initialSource: { type: String, default: "workflow" },
  initialNodeProbeRunId: { type: String, default: "" },
});

const endpoint = "/eval-center/node-lab/run-history";

const POLL_INTERVAL_MS = 7000;
const LIVE_STATUSES = new Set(["queued", "running"]);

const loading = ref(false);
const detailLoading = ref(false);
const error = ref("");
const detailError = ref("");
const records = ref([]);
const selectedTrialId = ref("");
const detail = ref(null);
const filters = ref({
  workspace_type: "all",
  session_scope: "all",
  node_name: "all",
});
const isPollingActive = ref(false);
let pollHandle = null;
let visibilityListenerBound = false;

const filteredTitle = computed(() => {
  const workspace = filters.value.workspace_type === "single_run"
    ? "Single Run"
    : filters.value.workspace_type === "baseline_compare"
      ? "Baseline Compare"
      : "全部类型";
  const scope = filters.value.session_scope === "standalone"
    ? "Standalone"
    : filters.value.session_scope === "session"
      ? "Session"
      : "全部来源";
  return `${workspace} / ${scope}`;
});

const selectedRecord = computed(() => (
  records.value.find((record) => record.trial_id === selectedTrialId.value) || null
));

const currentTrial = computed(() => detail.value?.trial || selectedRecord.value || null);
const currentResult = computed(() => detail.value?.result || null);
const judgeRequests = computed(() => detail.value?.judge_requests || []);
const currentSession = computed(() => detail.value?.session || null);

const resultKindLabel = computed(() => {
  const kind = currentTrial.value?.result_kind;
  if (kind === "single_run_result") return "Single Run";
  if (kind === "compare_result") return "Baseline Compare";
  return "Result";
});

watch(
  () => [props.initialSource, props.initialRunId, props.initialNodeProbeRunId],
  ([source, runId]) => {
    if (source === "node_lab" && runId) {
      void selectRecord(runId);
    }
  },
);

onMounted(async () => {
  await loadRecords();
  if (props.initialSource === "node_lab" && props.initialRunId) {
    await selectRecord(props.initialRunId);
  } else if (!selectedTrialId.value && records.value.length) {
    await selectRecord(records.value[0].trial_id);
  }
  startPolling();
  bindVisibilityListener();
});

onBeforeUnmount(() => {
  stopPolling();
  unbindVisibilityListener();
});

async function fetchData(url) {
  const response = await api.get(url);
  return response?.data?.data ?? response?.data;
}

function buildQuery() {
  const params = new URLSearchParams();
  params.set("limit", "80");
  for (const [key, value] of Object.entries(filters.value)) {
    if (value && value !== "all") params.set(key, value);
  }
  return params.toString();
}

async function loadRecords(options = {}) {
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchData(`${endpoint}?${buildQuery()}`);
    records.value = Array.isArray(data?.records) ? data.records : [];
    if (!options.keepSelection) {
      selectedTrialId.value = "";
      detail.value = null;
    }
    if (selectedTrialId.value && !records.value.some((record) => record.trial_id === selectedTrialId.value)) {
      selectedTrialId.value = "";
      detail.value = null;
    }
    if (!selectedTrialId.value && records.value.length) {
      await selectRecord(records.value[0].trial_id);
    }
  } catch (err) {
    error.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取 Run History 失败。";
  } finally {
    loading.value = false;
  }
}

async function selectRecord(trialId) {
  if (!trialId) return;
  selectedTrialId.value = trialId;
  detailLoading.value = true;
  detailError.value = "";
  try {
    detail.value = await fetchData(`${endpoint}/${encodeURIComponent(trialId)}`);
  } catch (err) {
    detailError.value = err?.response?.data?.errors?.[0]?.message || err?.message || "读取详情失败。";
  } finally {
    detailLoading.value = false;
  }
}

function setFilter(key, value) {
  filters.value = { ...filters.value, [key]: value };
  void loadRecords({ keepSelection: true });
}

function retryLoadRecords() {
  void loadRecords({ keepSelection: true });
}

function retrySelectRecord() {
  if (selectedTrialId.value) {
    void selectRecord(selectedTrialId.value);
  }
}

function hasLiveRecords() {
  return records.value.some((record) => LIVE_STATUSES.has(record.status));
}

async function pollOnce() {
  if (!isPollingActive.value) return;
  if (loading.value) return;
  if (!hasLiveRecords()) return;
  try {
    const data = await fetchData(`${endpoint}?${buildQuery()}`);
    const next = Array.isArray(data?.records) ? data.records : [];
    records.value = next;
  } catch {
    // Polling 失败静默,下一次 interval 继续
  }
}

function startPolling() {
  if (pollHandle) return;
  isPollingActive.value = true;
  pollHandle = window.setInterval(() => {
    void pollOnce();
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  isPollingActive.value = false;
  if (pollHandle) {
    window.clearInterval(pollHandle);
    pollHandle = null;
  }
}

function handleVisibilityChange() {
  if (document.hidden) {
    stopPolling();
  } else {
    startPolling();
    void pollOnce();
  }
}

function bindVisibilityListener() {
  if (visibilityListenerBound) return;
  if (typeof document === "undefined") return;
  document.addEventListener("visibilitychange", handleVisibilityChange);
  visibilityListenerBound = true;
}

function unbindVisibilityListener() {
  if (!visibilityListenerBound) return;
  if (typeof document === "undefined") return;
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  visibilityListenerBound = false;
}

function workspaceLabel(record) {
  if (record?.result_kind === "single_run_result") return "Single Run";
  if (record?.workspace_type === "baseline_compare") return "Compare";
  return record?.workspace_type || "Result";
}

function sourceLabel(record) {
  if (record?.session_id) return record.session_title || record.session_id;
  return "Standalone";
}

function runOutput(result) {
  return result?.run?.node_output || null;
}

function compareSides(result) {
  if (!result) return [];
  return [
    { key: "baseline", label: "Baseline", value: result.baseline || null },
    { key: "candidate", label: "Candidate", value: result.candidate || null },
  ].filter((item) => item.value);
}

function tokenSummary(runtime) {
  const total = runtime?.aggregate?.total_tokens;
  if (total == null) return "未记录";
  return `${total} tokens`;
}
</script>

<template>
  <section class="run-history">
    <aside class="history-list">
      <div class="list-header">
        <div>
          <p class="eyebrow">Run History</p>
          <h2>{{ filteredTitle }}</h2>
        </div>
        <button class="icon-button" type="button" :disabled="loading" aria-label="刷新历史记录" title="刷新" @click="loadRecords({ keepSelection: true })">
          <span aria-hidden="true">↻</span>
        </button>
      </div>

      <div class="filters">
        <label>
          <span>类型</span>
          <select :value="filters.workspace_type" @change="setFilter('workspace_type', $event.target.value)">
            <option value="all">全部</option>
            <option value="single_run">Single Run</option>
            <option value="baseline_compare">Baseline Compare</option>
          </select>
        </label>
        <label>
          <span>来源</span>
          <select :value="filters.session_scope" @change="setFilter('session_scope', $event.target.value)">
            <option value="all">全部</option>
            <option value="standalone">Standalone</option>
            <option value="session">Session</option>
          </select>
        </label>
        <label>
          <span>Node</span>
          <select :value="filters.node_name" @change="setFilter('node_name', $event.target.value)">
            <option value="all">全部</option>
            <option value="grammar">Grammar</option>
            <option value="vocabulary">Vocabulary</option>
            <option value="translation">Translation</option>
          </select>
        </label>
      </div>

      <div v-if="error" class="notice is-danger" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="notice-retry" @click="retryLoadRecords">重试</button>
      </div>
      <div v-else-if="loading && !records.length" class="empty-state">正在读取历史记录...</div>
      <div v-else-if="!records.length" class="empty-state">暂无匹配记录。</div>

      <div class="record-list">
        <button
          v-for="record in records"
          :key="record.trial_id"
          type="button"
          class="record-row"
          :class="{ active: record.trial_id === selectedTrialId }"
          @click="selectRecord(record.trial_id)"
        >
          <span class="record-topline">
            <span class="record-type">{{ workspaceLabel(record) }}</span>
            <StatusPill :label="statusLabel(record.status)" :tone="statusTone(record.status)" />
          </span>
          <strong>{{ record.node_name }} · {{ record.reading_goal }} · {{ record.reading_variant }}</strong>
          <span class="record-excerpt">{{ record.display_excerpt || record.input_excerpt || record.input_text_hash }}</span>
          <span class="record-meta">
            <span>{{ sourceLabel(record) }}</span>
            <span>{{ formatDateTime(record.date_created) }}</span>
          </span>
        </button>
      </div>
    </aside>

    <main class="history-detail">
      <div v-if="detailLoading" class="empty-state">正在读取详情...</div>
      <div v-else-if="detailError" class="notice is-danger" role="alert">
        <span>{{ detailError }}</span>
        <button type="button" class="notice-retry" @click="retrySelectRecord">重试</button>
      </div>
      <div v-else-if="!currentTrial" class="empty-state">请选择一条历史记录。</div>
      <template v-else>
        <header class="detail-header">
          <div>
            <p class="eyebrow">{{ resultKindLabel }}</p>
            <h2>{{ currentTrial.node_name }} · {{ currentTrial.reading_goal }} · {{ currentTrial.reading_variant }}</h2>
            <p>{{ currentTrial.display_excerpt || currentTrial.input_excerpt || currentTrial.input_text_hash }}</p>
          </div>
          <StatusPill :label="statusLabel(currentTrial.status)" :tone="statusTone(currentTrial.status)" size="large" />
        </header>

        <dl class="meta-grid">
          <div>
            <dt>Trial</dt>
            <dd>{{ currentTrial.trial_id }}</dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>{{ sourceLabel(currentTrial) }}</dd>
          </div>
          <div>
            <dt>Prompt</dt>
            <dd>{{ shortId(currentTrial.baseline_snapshot_hash || "baseline", 16) }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatDateTime(currentTrial.date_created) }}</dd>
          </div>
        </dl>

        <section v-if="currentSession" class="session-band">
          <div>
            <strong>{{ currentSession.title }}</strong>
            <span>{{ currentSession.status }} · {{ currentSession.goal || "未记录目标" }}</span>
          </div>
          <span class="session-id">{{ currentSession.session_id }}</span>
        </section>

        <section v-if="currentTrial.result_kind === 'single_run_result'" class="result-section">
          <div class="section-heading">
            <h3>Single Run 输出</h3>
            <span>{{ tokenSummary(currentResult?.run?.runtime_summary) }}</span>
          </div>
          <NodeProbeOutputView
            :node-name="currentTrial.node_name"
            :output="runOutput(currentResult)"
            :prepared-sentences="currentResult?.run?.prepared_sentences || []"
            :quick-validation="currentResult?.run?.quick_validation || null"
            empty-text="这条记录没有结构化输出。"
          />
        </section>

        <section v-else class="result-section">
          <div class="section-heading">
            <h3>Compare 输出</h3>
            <span>{{ judgeRequests.length }} 条 Judge</span>
          </div>
          <div class="compare-grid">
            <article v-for="side in compareSides(currentResult)" :key="side.key" class="compare-side">
              <div class="compare-side__header">
                <strong>{{ side.label }}</strong>
                <StatusPill :label="statusLabel(side.value.status)" :tone="statusTone(side.value.status)" />
              </div>
              <p>{{ side.value.model_identity?.model_name || "未记录模型" }} · {{ tokenSummary(side.value.runtime_summary) }}</p>
              <NodeProbeOutputView
                :node-name="currentTrial.node_name"
                :output="side.value.node_output || null"
                :prepared-sentences="side.value.prepared_sentences || []"
                :quick-validation="side.value.quick_validation || null"
                empty-text="这侧没有结构化输出。"
              />
            </article>
          </div>
        </section>

        <section class="result-section">
          <div class="section-heading">
            <h3>Judge 结果</h3>
            <span>{{ judgeRequests.length ? "只读" : "暂无 Judge" }}</span>
          </div>
          <div v-if="!judgeRequests.length" class="empty-inline">这条记录还没有 Judge request。</div>
          <div v-else class="judge-list">
            <ResultBlock
              v-for="request in judgeRequests"
              :key="request.judge_request_id"
              :title="`${request.judge_request_id} · ${statusLabel(request.status)}`"
              :open="request.status === 'succeeded'"
            >
              <dl class="judge-meta">
                <div>
                  <dt>Mode</dt>
                  <dd>{{ request.judge_config_snapshot_json?.judge_mode || "未记录" }}</dd>
                </div>
                <div>
                  <dt>Preset</dt>
                  <dd>{{ request.judge_config_snapshot_json?.preset_id || "未记录" }}</dd>
                </div>
                <div>
                  <dt>时间</dt>
                  <dd>{{ formatDateTime(request.finished_at || request.date_updated) }}</dd>
                </div>
              </dl>
              <JsonTreeView :value="request.result || request.artifacts || request.error_json" empty-text="暂无 Judge artifact。" />
            </ResultBlock>
          </div>
        </section>

        <ResultBlock title="完整结果 JSON" :open="false">
          <JsonTreeView :value="currentResult" empty-text="暂无 result artifact。" />
        </ResultBlock>
      </template>
    </main>
  </section>
</template>

<style scoped>
.run-history {
  display: grid;
  grid-template-columns: minmax(320px, 0.38fr) minmax(0, 1fr);
  gap: 16px;
  min-height: 720px;
}

.history-list,
.history-detail {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.history-list {
  display: flex;
  flex-direction: column;
}

.list-header,
.detail-header,
.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.list-header {
  padding: 16px;
  border-bottom: 1px solid var(--theme--border-color);
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

h2,
h3 {
  margin: 0;
  color: var(--theme--foreground);
}

.list-header h2,
.detail-header h2 {
  font-size: 18px;
  line-height: 1.3;
}

.detail-header p {
  margin: 8px 0 0;
  color: var(--theme--foreground-subdued);
  line-height: 1.5;
}

.icon-button {
  width: 34px;
  height: 34px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
}

.icon-button:disabled {
  cursor: default;
  opacity: 0.5;
}

.filters {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--theme--border-color);
}

.filters label {
  display: grid;
  gap: 5px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.filters select {
  width: 100%;
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground);
}

.record-list {
  overflow: auto;
}

.record-row {
  display: grid;
  width: 100%;
  gap: 7px;
  padding: 14px 16px;
  border: 0;
  border-bottom: 1px solid var(--theme--border-color);
  background: transparent;
  color: var(--theme--foreground);
  text-align: left;
  cursor: pointer;
}

.record-row.active {
  background: color-mix(in srgb, var(--theme--primary) 10%, transparent);
  box-shadow: inset 3px 0 0 var(--theme--primary);
}

.record-topline,
.record-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.record-type,
.record-meta,
.record-excerpt {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.record-excerpt {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-detail {
  padding: 18px;
  overflow: auto;
}

.meta-grid,
.judge-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}

.meta-grid div,
.judge-meta div {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
}

dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.session-band {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 16px 0;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
  background: color-mix(in srgb, var(--theme--primary) 7%, transparent);
}

.session-band div {
  display: grid;
  gap: 4px;
}

.session-band span {
  color: var(--theme--foreground-subdued);
}

.session-id {
  font-family: var(--theme--fonts--monospace--font-family);
  font-size: 12px;
}

.result-section {
  margin: 18px 0;
}

.section-heading {
  margin-bottom: 10px;
}

.section-heading span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.compare-side {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--theme--border-color);
}

.compare-side__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.compare-side p {
  margin: 0 0 10px;
  color: var(--theme--foreground-subdued);
}

.judge-list {
  display: grid;
  gap: 10px;
}

.empty-state,
.empty-inline,
.notice {
  padding: 16px;
  color: var(--theme--foreground-subdued);
}

.empty-inline {
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
}

.notice.is-danger {
  color: var(--theme--danger);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.notice-retry {
  border: 1px solid color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: var(--theme--background);
  color: var(--theme--danger);
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  cursor: pointer;
}

.notice-retry:hover {
  background: color-mix(in srgb, var(--theme--danger) 12%, var(--theme--background));
}

@media (max-width: 1100px) {
  .run-history,
  .compare-grid,
  .meta-grid,
  .judge-meta {
    grid-template-columns: 1fr;
  }
}
</style>
