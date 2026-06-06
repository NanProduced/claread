<script setup>
import { computed, onMounted, ref, watch } from "vue";

const props = defineProps({
  /** workflow compare id；同时也是 review note 的 target_id。 */
  compareId: { type: String, required: true },
  /** candidate run id，写入 review note 的 run_id 字段。 */
  candidateRunId: { type: String, default: "" },
  /** 可选：compare 报告里 wins / losses / ties 摘要，让决策层更直观。 */
  compareSummary: { type: Object, default: null },
});

const notesEndpoint = "/items/eval_review_notes";

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notes = ref([]);

const draftVerdict = ref("needs_review");
const draftNote = ref("");
const draftPromote = ref(false);

const VERDICT_OPTIONS = [
  { value: "win", label: "候选更优", short: "更优", tone: "success", hint: "候选在质量与一致性上都明显胜出" },
  { value: "loss", label: "Baseline 更优", short: "Baseline 胜", tone: "danger", hint: "候选引入了回归或明显掉点" },
  { value: "tie", label: "持平", short: "持平", tone: "neutral", hint: "两侧各有胜负，但综合判断持平" },
  { value: "needs_review", label: "需复查", short: "待复查", tone: "warning", hint: "尚有不确定项，需要更多证据或重新跑一次" },
  { value: "blocked", label: "阻塞", short: "阻塞", tone: "danger", hint: "本次结果不可信，需要先修复底层 prompt 或数据" },
];

const VERDICT_LABEL_MAP = VERDICT_OPTIONS.reduce((acc, option) => {
  acc[option.value] = option;
  return acc;
}, {});

const verdictMeta = computed(() => VERDICT_LABEL_MAP[draftVerdict.value] || VERDICT_OPTIONS[3]);

const promoteGating = computed(() => {
  const verdict = draftVerdict.value;
  if (verdict === "win") {
    return {
      enabled: true,
      tone: "primary",
      label: "建议晋升候选版本",
      hint: "verdict 已是“候选更优”，勾选后该 note 会作为晋升建议记录下来。",
    };
  }
  if (verdict === "needs_review") {
    return {
      enabled: false,
      tone: "muted",
      label: "需先解决复查项",
      hint: "verdict = 需复查 时不会触发晋升建议；先填 evidence 再决定。",
    };
  }
  if (verdict === "blocked") {
    return {
      enabled: false,
      tone: "danger",
      label: "verdict = 阻塞，不建议晋升",
      hint: "在 verdict 切换回 “候选更优” 之前，晋升推荐保持锁定。",
    };
  }
  return {
    enabled: false,
    tone: "muted",
    label: "当前 verdict 不支持晋升",
    hint: "只有 verdict = 候选更优 时，晋升推荐才会激活。",
  };
});

// 最小评审说明门槛：避免“只点了 verdict、没留下任何 evidence”的脏记录。
// 6 字足以写“通过 / 阻塞，prompt 仍需细化”等短理由。
const MIN_NOTE_LENGTH = 6;
const trimmedNote = computed(() => draftNote.value.trim());
const noteMeetsMinimum = computed(() => trimmedNote.value.length >= MIN_NOTE_LENGTH);

const canSave = computed(() => Boolean(
  props.compareId
  && noteMeetsMinimum.value
  && !saving.value,
));
const canSaveHint = computed(() => {
  if (!props.compareId) return "缺少 compare id，无法保存。";
  if (!noteMeetsMinimum.value) return `评审依据至少 ${MIN_NOTE_LENGTH} 字，请补全 evidence。`;
  return "";
});

const orderedNotes = computed(() => {
  const list = Array.isArray(notes.value) ? notes.value.slice() : [];
  list.sort((a, b) => {
    const ta = new Date(a.date_created || 0).getTime();
    const tb = new Date(b.date_created || 0).getTime();
    return tb - ta;
  });
  return list;
});

const latestNote = computed(() => orderedNotes.value[0] || null);
const olderNotes = computed(() => orderedNotes.value.slice(1));

const decisionSummary = computed(() => {
  const latest = latestNote.value;
  if (!latest) return null;
  const meta = VERDICT_LABEL_MAP[latest.verdict] || null;
  return {
    verdict: latest.verdict,
    label: meta?.label || latest.verdict || "未设",
    tone: meta?.tone || "neutral",
    promote: Boolean(latest.promote_candidate),
    date: latest.date_created,
    body: latest.note,
  };
});

const totalDecisionCount = computed(() => orderedNotes.value.length);

const draftPromoteEnabled = computed(() => promoteGating.value.enabled);

onMounted(() => {
  void loadNotes();
});

watch(
  () => props.compareId,
  () => {
    draftNote.value = "";
    draftVerdict.value = "needs_review";
    draftPromote.value = false;
    void loadNotes();
  },
);

watch(
  () => draftVerdict.value,
  (next) => {
    if (next !== "win" && draftPromote.value) {
      draftPromote.value = false;
    }
  },
);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.errors?.[0]?.message || payload?.message || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return payload?.data !== undefined ? payload.data : payload;
}

function buildQuery() {
  const params = new URLSearchParams({
    "filter[target_type][_eq]": "workflow_compare",
    "filter[target_id][_eq]": props.compareId,
    sort: "-date_created",
    limit: "20",
  });
  return `${notesEndpoint}?${params.toString()}`;
}

async function loadNotes() {
  if (!props.compareId) return;
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchJson(buildQuery());
    notes.value = Array.isArray(data) ? data : [];
  } catch (err) {
    error.value = err?.message || "加载评审记录失败。";
  } finally {
    loading.value = false;
  }
}

async function saveNote() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    await fetchJson(notesEndpoint, {
      method: "POST",
      body: JSON.stringify({
        target_type: "workflow_compare",
        target_id: props.compareId,
        run_id: props.candidateRunId || null,
        case_id: null,
        prompt_variant_id: null,
        verdict: draftVerdict.value || null,
        note: draftNote.value.trim(),
        promote_candidate: draftPromoteEnabled.value ? Boolean(draftPromote.value) : false,
        tags: [],
      }),
    });
    draftNote.value = "";
    await loadNotes();
  } catch (err) {
    error.value = err?.message || "保存评审记录失败。";
  } finally {
    saving.value = false;
  }
}

const NOTE_TEMPLATES = [
  {
    id: "win",
    label: "候选更优 · 证据骨架",
    insert: "判定 win：候选在 <填写句号/差异要点> 上明显优于 baseline，<填写 prompt 改动> 是关键原因。建议保留并继续在更大语料上验证。",
  },
  {
    id: "loss",
    label: "Baseline 更优 · 证据骨架",
    insert: "判定 loss：候选在 <填写句号> 出现回归，<填写具体现象>，与 baseline 相比稳定性更差。建议回滚并继续观察。",
  },
  {
    id: "needs_review",
    label: "需复查 · 证据骨架",
    insert: "需复查：<填写不确定项>。目前两侧都有可接受结果，但 <填写变量> 仍未稳定，需要再补一次 compare。",
  },
  {
    id: "promote",
    label: "建议晋升 · 理由骨架",
    insert: "建议晋升候选版本：<填写对比优势>，已满足 <填写 gating 条件>。注意：<填写仍需关注的副作用或 follow-up>。",
  },
];

function applyTemplate(insertText) {
  if (!insertText) return;
  draftNote.value = draftNote.value
    ? `${draftNote.value.trim()}\n${insertText}`
    : insertText;
}

function shortId(value, max = 8) {
  const raw = String(value || "");
  if (!raw) return "—";
  if (raw.length <= max) return raw;
  return `${raw.slice(0, max)}…`;
}

function formatAbsoluteTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatRelativeTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天前`;
  return formatAbsoluteTime(value);
}
</script>

<template>
  <section class="wf-review">
    <header class="wf-review-head">
      <div class="wf-review-title">
        <p class="eyebrow">Compare Review · 决策面板</p>
        <h3>本次 Compare 的人工判断</h3>
        <p class="wf-review-sub">
          这条 note 挂在 compare 记录上，表达 compare-scope 判断；不是逐句 review。
        </p>
      </div>
      <div class="wf-review-context">
        <span class="ctx-chip" :title="`compare id · ${compareId}`">
          <span class="ctx-chip-label">compare</span>
          <span class="ctx-chip-value">{{ shortId(compareId, 12) }}</span>
        </span>
        <span v-if="candidateRunId" class="ctx-chip" :title="`candidate run · ${candidateRunId}`">
          <span class="ctx-chip-label">candidate run</span>
          <span class="ctx-chip-value">{{ shortId(candidateRunId, 12) }}</span>
        </span>
        <span v-if="compareSummary" class="ctx-chip is-summary" :title="`${compareSummary.wins ?? 0} 优 / ${compareSummary.losses ?? 0} 差 / ${compareSummary.ties ?? 0} 持平`">
          <span class="ctx-chip-label">deterministic</span>
          <span class="ctx-chip-value">
            {{ compareSummary.wins ?? 0 }} 优 / {{ compareSummary.losses ?? 0 }} 差 / {{ compareSummary.ties ?? 0 }} 持平
          </span>
        </span>
        <span v-if="totalDecisionCount" class="ctx-chip is-history" :title="`已记录 ${totalDecisionCount} 条决策`">
          <span class="ctx-chip-label">决策数</span>
          <span class="ctx-chip-value">{{ totalDecisionCount }}</span>
        </span>
      </div>
    </header>

    <p v-if="error" class="wf-review-error" role="alert">{{ error }}</p>

    <!-- ── 1. 决策层 ─────────────────────────────────── -->
    <section class="decision-layer">
      <header class="layer-head">
        <strong>1 · 决策</strong>
        <small>先定 verdict，再考虑是否推荐晋升</small>
      </header>

      <div class="verdict-group" role="radiogroup" aria-label="verdict">
        <button
          v-for="option in VERDICT_OPTIONS"
          :key="option.value"
          type="button"
          class="verdict-chip"
          :class="[`is-${option.tone}`, { 'is-active': draftVerdict === option.value }]"
          :aria-pressed="draftVerdict === option.value"
          @click="draftVerdict = option.value"
        >
          <span class="verdict-label">{{ option.label }}</span>
          <span class="verdict-hint">{{ option.hint }}</span>
        </button>
      </div>

      <div class="promote-action" :class="`is-${promoteGating.tone}`">
        <label class="promote-toggle">
          <input
            v-model="draftPromote"
            type="checkbox"
            :disabled="!draftPromoteEnabled"
            :aria-describedby="`promote-hint-${compareId}`"
          />
          <span class="promote-text">
            <span class="promote-label">{{ promoteGating.label }}</span>
            <span :id="`promote-hint-${compareId}`" class="promote-hint">{{ promoteGating.hint }}</span>
          </span>
        </label>
        <span v-if="draftPromote && draftPromoteEnabled" class="promote-flag">推荐动作已勾选</span>
        <span v-else-if="!draftPromoteEnabled" class="promote-flag is-locked">晋升推荐已锁定</span>
      </div>
    </section>

    <!-- ── 2. 证据 / 上下文层 ──────────────────────────── -->
    <section class="evidence-layer">
      <header class="layer-head">
        <strong>2 · 决策说明</strong>
        <small>用于回看阶段补充 compare-scope 人工结论</small>
      </header>

      <div class="note-scaffold">
        <textarea
          v-model="draftNote"
          rows="4"
          placeholder="评审依据至少 6 字。建议先描述：(1) 关键差异句号或差异类型；(2) 为什么选择该 verdict；(3) 晋升 / 不晋升 / 需复查的理由。"
        />
        <div class="template-row">
          <span class="template-label">快速套用：</span>
          <button
            v-for="template in NOTE_TEMPLATES"
            :key="template.id"
            type="button"
            class="template-chip"
            :title="`点击追加到当前 note 末尾：${template.insert}`"
            @click="applyTemplate(template.insert)"
          >{{ template.label }}</button>
        </div>
      </div>

      <div class="decision-summary" v-if="decisionSummary">
        <header>
          <strong>当前最新决策</strong>
          <span class="summary-meta">{{ formatRelativeTime(decisionSummary.date) }} · {{ formatAbsoluteTime(decisionSummary.date) }}</span>
        </header>
        <div class="summary-row">
          <span :class="`summary-verdict is-${decisionSummary.tone}`">{{ decisionSummary.label }}</span>
          <span v-if="decisionSummary.promote" class="summary-promote">已勾选推荐晋升</span>
          <span v-else class="summary-promote is-muted">未勾选推荐晋升</span>
        </div>
        <p v-if="decisionSummary.body" class="summary-body">{{ decisionSummary.body }}</p>
      </div>
    </section>

    <!-- ── 3. 决策时间线 ────────────────────────────── -->
    <section class="timeline-layer">
      <header class="layer-head">
        <strong>3 · 历史决策</strong>
        <small>只读 timeline，用于回看与审计</small>
      </header>

      <div v-if="loading && !notes.length" class="timeline-loading">正在加载决策历史…</div>
      <div v-else-if="!orderedNotes.length" class="timeline-empty">
        还没有 compare-scope 决策记录。
      </div>
      <ol v-else class="timeline">
        <li
          v-for="(item, index) in orderedNotes"
          :key="item.id"
          class="timeline-item"
          :class="`is-${(VERDICT_LABEL_MAP[item.verdict]?.tone) || 'neutral'}`, { 'is-latest': index === 0 }"
        >
          <div class="timeline-rail" aria-hidden="true">
            <span class="rail-dot"></span>
            <span v-if="index !== orderedNotes.length - 1" class="rail-line"></span>
          </div>
          <div class="timeline-card">
            <header class="timeline-head">
              <div class="timeline-meta">
                <span :class="`timeline-verdict is-${(VERDICT_LABEL_MAP[item.verdict]?.tone) || 'neutral'}`">
                  {{ VERDICT_LABEL_MAP[item.verdict]?.label || item.verdict || "未设 verdict" }}
                </span>
                <span v-if="item.promote_candidate" class="timeline-promote">建议晋升</span>
                <span class="timeline-time" :title="formatAbsoluteTime(item.date_created)">
                  {{ formatRelativeTime(item.date_created) }}
                </span>
                <span v-if="index === 0" class="timeline-latest">最新</span>
              </div>
            </header>
            <p v-if="item.note" class="timeline-body">{{ item.note }}</p>
            <p v-else class="timeline-body is-empty">（决策时未写 evidence）</p>
          </div>
        </li>
      </ol>
    </section>

    <footer class="wf-review-foot">
      <div class="foot-actions">
        <button
          type="button"
          class="primary-cta"
          :disabled="!canSave"
          @click="saveNote"
        >
          {{ saving ? "保存决策中…" : "保存决策" }}
        </button>
        <span v-if="!canSave && !saving && canSaveHint" class="foot-hint">
          {{ canSaveHint }}
        </span>
      </div>
      <span class="foot-target">compare · {{ shortId(compareId, 12) }}</span>
    </footer>
  </section>
</template>

<style scoped>
.wf-review {
  display: grid;
  gap: 14px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 16px 18px;
}

.wf-review-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.wf-review-title h3 {
  margin: 2px 0 0;
  font-size: 16px;
  font-weight: 700;
}

.eyebrow {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.wf-review-sub {
  margin: 4px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.5;
  max-width: 60ch;
}

.wf-review-context {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.ctx-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 0 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background-subdued);
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.ctx-chip-label {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: 9px;
}

.ctx-chip-value {
  color: var(--theme--foreground);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
}

.ctx-chip.is-summary .ctx-chip-value {
  color: var(--theme--foreground);
}

.ctx-chip.is-history .ctx-chip-value {
  color: var(--theme--primary);
}

.wf-review-error {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--theme--danger) 35%, var(--theme--border-color));
  border-radius: 6px;
  background: color-mix(in srgb, var(--theme--danger) 6%, var(--theme--background));
  color: var(--theme--danger);
  font-size: 12px;
}

/* ── Layer shared head ────────────────────────────── */
.layer-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.layer-head strong {
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.layer-head small {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

/* ── 1. 决策层 ─────────────────────────────────── */
.decision-layer {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px 14px;
  display: grid;
  gap: 12px;
}

.verdict-group {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}

.verdict-chip {
  display: grid;
  gap: 4px;
  text-align: left;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.15s ease;
  color: var(--theme--foreground);
}

.verdict-chip:hover {
  border-color: color-mix(in srgb, var(--theme--primary) 35%, var(--theme--border-color));
}

.verdict-chip.is-active {
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 5%, var(--theme--background));
}

.verdict-label {
  font-size: 13px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.verdict-hint {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  line-height: 1.5;
}

.verdict-chip.is-active.is-success .verdict-label {
  color: var(--theme--success);
}
.verdict-chip.is-active.is-danger .verdict-label {
  color: var(--theme--danger);
}
.verdict-chip.is-active.is-warning .verdict-label {
  color: var(--theme--warning);
}
.verdict-chip.is-active.is-neutral .verdict-label {
  color: var(--theme--foreground);
}

.promote-action {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 10px 12px;
  flex-wrap: wrap;
}

.promote-action.is-primary {
  border-color: color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
}

.promote-action.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 4%, var(--theme--background));
}

.promote-action.is-muted {
  opacity: 0.85;
}

.promote-toggle {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
  user-select: none;
  flex: 1 1 auto;
  min-width: 0;
}

.promote-toggle input[type="checkbox"] {
  width: 16px;
  height: 16px;
  margin-top: 2px;
  cursor: pointer;
  accent-color: var(--theme--primary);
}

.promote-toggle input[type="checkbox"]:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.promote-text {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.promote-label {
  font-size: 13px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.promote-action.is-primary .promote-label {
  color: var(--theme--primary);
}

.promote-action.is-danger .promote-label {
  color: var(--theme--danger);
}

.promote-hint {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  line-height: 1.5;
}

.promote-flag {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--theme--primary) 12%, var(--theme--background));
  color: var(--theme--primary);
  border: 1px solid color-mix(in srgb, var(--theme--primary) 35%, var(--theme--border-color));
  white-space: nowrap;
}

.promote-flag.is-locked {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  border-color: var(--theme--border-color);
}

/* ── 2. 证据 / 上下文层 ────────────────────────── */
.evidence-layer {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}

.note-scaffold {
  display: grid;
  gap: 8px;
}

.note-scaffold textarea {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 13px;
  line-height: 1.55;
  padding: 10px 12px;
  resize: vertical;
  min-height: 96px;
}

.note-scaffold textarea:focus {
  border-color: var(--theme--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--theme--primary) 12%, transparent);
  outline: none;
}

.template-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.template-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.template-chip {
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.template-chip:hover {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.decision-summary {
  border: 1px dashed var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 10px 12px;
  display: grid;
  gap: 6px;
}

.decision-summary header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.decision-summary strong {
  font-size: 12px;
  color: var(--theme--foreground);
}

.summary-meta {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.summary-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.summary-verdict {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: var(--theme--background);
  color: var(--theme--foreground);
}

.summary-verdict.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.summary-verdict.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.summary-verdict.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.summary-promote {
  font-size: 11px;
  font-weight: 600;
  color: var(--theme--primary);
}

.summary-promote.is-muted {
  color: var(--theme--foreground-subdued);
}

.summary-body {
  margin: 0;
  font-size: 12px;
  color: var(--theme--foreground);
  line-height: 1.6;
  white-space: pre-wrap;
}

/* ── 3. 决策时间线 ──────────────────────────── */
.timeline-layer {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px 14px;
}

.timeline-loading,
.timeline-empty {
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  padding: 8px 0;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.timeline-item {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
}

.timeline-rail {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.rail-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--theme--foreground-subdued);
  border: 2px solid var(--theme--background);
  margin-top: 6px;
  flex-shrink: 0;
}

.timeline-item.is-success .rail-dot {
  background: var(--theme--success);
}

.timeline-item.is-danger .rail-dot {
  background: var(--theme--danger);
}

.timeline-item.is-warning .rail-dot {
  background: var(--theme--warning);
}

.timeline-item.is-neutral .rail-dot {
  background: var(--theme--foreground-subdued);
}

.timeline-item.is-latest .rail-dot {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--theme--primary) 25%, transparent);
}

.rail-line {
  flex: 1;
  width: 1px;
  background: var(--theme--border-color);
  margin-top: 2px;
}

.timeline-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 8px 10px;
  display: grid;
  gap: 6px;
}

.timeline-item.is-latest .timeline-card {
  border-color: color-mix(in srgb, var(--theme--primary) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 3%, var(--theme--background));
}

.timeline-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.timeline-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.timeline-verdict {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  background: var(--theme--background);
  color: var(--theme--foreground);
}

.timeline-verdict.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.timeline-verdict.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.timeline-verdict.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.timeline-promote {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 35%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
  color: var(--theme--primary);
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
}

.timeline-time {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  font-variant-numeric: tabular-nums;
}

.timeline-latest {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.timeline-body {
  margin: 0;
  font-size: 12px;
  color: var(--theme--foreground);
  line-height: 1.6;
  white-space: pre-wrap;
}

.timeline-body.is-empty {
  color: var(--theme--foreground-subdued);
  font-style: italic;
}

/* ── footer ─────────────────────────── */
.wf-review-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px dashed var(--theme--border-color);
  padding-top: 12px;
  flex-wrap: wrap;
}

.foot-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.primary-cta {
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border: 1px solid var(--theme--primary);
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.primary-cta:hover:not(:disabled) {
  opacity: 0.92;
}

.primary-cta:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.foot-hint {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.foot-target {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
}

@media (max-width: 720px) {
  .verdict-group {
    grid-template-columns: 1fr;
  }
}
</style>
