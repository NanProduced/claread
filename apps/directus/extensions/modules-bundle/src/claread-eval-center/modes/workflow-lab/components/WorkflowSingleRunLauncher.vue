<script setup>
import { computed, onMounted, ref, watch } from "vue";

// 双跑 single-run compare 启动器
// 同一篇文章并发跑 baseline + candidate,直接产出 compare workspace
const props = defineProps({
  candidates: { type: Array, default: () => [] },
  modelProfiles: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
  initialCandidateId: { type: String, default: "" },
});
const emit = defineEmits(["submit", "go-to-candidate"]);
const STORAGE_KEY = "claread-eval-center:workflow-lab:single-run-compare-form:v1";

// Must mirror the <select> options below. The deprecated "isolated" value
// is intentionally excluded — the backend no longer accepts it, and we
// must clamp any stale sessionStorage payload (left over from before that
// option was removed) so users with an old browser session don't keep
// submitting an upstream-rejected value. See docs/operations/langsmith.md.
const VALID_TRACE_SCOPES = Object.freeze(["off", "inherit"]);

function normalizeTraceScope(value) {
  return VALID_TRACE_SCOPES.includes(value) ? value : "off";
}

const form = ref({
  text: "",
  baseline_prompt_variant_id: "",
  candidate_prompt_variant_id: "",
  model_profile: "",
  rag_mode: "off",
  trace_scope: "off",
  timeout_seconds: 120,
});
const error = ref("");

const candidateOptions = computed(() => props.candidates
  .filter((candidate) => candidate.prompt_bundle_summary?.topology_mode === "learning")
  .map((candidate) => ({
    value: candidate.variant_id,
    label: `${candidate.variant_id} / ${candidate.prompt_bundle_summary?.reading_variant || "learning"}`,
  })));

const candidateById = computed(() => new Map(
  props.candidates
    .filter((candidate) => candidate?.variant_id)
    .map((candidate) => [candidate.variant_id, candidate]),
));

const modelOptions = computed(() => [
  { value: "", label: "使用默认模型方案" },
  ...props.modelProfiles.map((profile) => ({
    value: profile.profile_name,
    label: `${profile.profile_name} · ${profile.model_name}`,
  })),
]);

const hasPublishedCandidate = computed(() => candidateOptions.value.length > 0);
const selectedCandidate = computed(() => candidateById.value.get(form.value.candidate_prompt_variant_id) || null);
const selectedCandidateSummary = computed(() => selectedCandidate.value?.prompt_bundle_summary || {});

function readingGoalLabel(value) {
  return value === "exam" ? "考试阅读" : value === "daily_reading" ? "日常阅读" : value || "—";
}

function readingVariantLabel(value) {
  const map = {
    gaokao: "高考",
    cet: "CET",
    kaoyan: "考研",
    tem: "TEM",
    ielts_toefl: "IELTS / TOEFL",
    beginner_reading: "入门阅读",
    intermediate_reading: "进阶阅读",
    intensive_reading: "精读",
  };
  return map[value] || value || "—";
}

// 选 candidate 时强制 rag_mode=off(沿用 prompt variant 自带 RAG 配置)
watch(
  () => form.value.candidate_prompt_variant_id,
  (value) => {
    if (value) form.value.rag_mode = "off";
  },
);

watch(
  form,
  (value) => {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  },
  { deep: true },
);

// 进入页 / 候选数据变化时,确保 candidate_prompt_variant_id 默认指向已发布 candidate;
watch(
  () => candidateOptions.value.map((option) => option.value).join("|"),
  () => {
    if (form.value.candidate_prompt_variant_id) return;
    if (props.initialCandidateId && candidateOptions.value.some((option) => option.value === props.initialCandidateId)) {
      form.value.candidate_prompt_variant_id = props.initialCandidateId;
    } else if (candidateOptions.value.length > 0) {
      form.value.candidate_prompt_variant_id = candidateOptions.value[0].value;
    }
  },
  { immediate: true },
);

onMounted(() => {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      if (saved && typeof saved === "object") {
        form.value = {
          ...form.value,
          text: String(saved.text || ""),
          baseline_prompt_variant_id: String(saved.baseline_prompt_variant_id || ""),
          candidate_prompt_variant_id: String(saved.candidate_prompt_variant_id || ""),
          model_profile: String(saved.model_profile || ""),
          rag_mode: String(saved.rag_mode || "off"),
          trace_scope: normalizeTraceScope(saved.trace_scope),
          timeout_seconds: Number(saved.timeout_seconds) || 120,
        };
      }
    }
  } catch {
    // ignore malformed session state
  }
  if (!form.value.candidate_prompt_variant_id) {
    if (props.initialCandidateId && candidateOptions.value.some((option) => option.value === props.initialCandidateId)) {
      form.value.candidate_prompt_variant_id = props.initialCandidateId;
    } else if (candidateOptions.value.length > 0) {
      form.value.candidate_prompt_variant_id = candidateOptions.value[0].value;
    }
  }
});

function submit() {
  error.value = "";
  const text = form.value.text.trim();
  if (!text) {
    error.value = "请先粘贴一段要验证的英文文章。";
    return;
  }

  const modelSelection = {};
  if (form.value.model_profile) {
    Object.assign(modelSelection, {
      ...modelSelection,
      default_profile: form.value.model_profile,
    });
  }

  const readingGoal = selectedCandidateSummary.value?.reading_goal || "daily_reading";
  const readingVariant = selectedCandidateSummary.value?.reading_variant || "intermediate_reading";

  emit("submit", {
    text,
    reading_goal: readingGoal,
    reading_variant: readingVariant,
    source_type: "user_input",
    rag_mode: form.value.rag_mode,
    trace_scope: form.value.trace_scope,
    timeout_seconds: Number(form.value.timeout_seconds) || 120,
    model_selection: modelSelection,
    // baseline: 空 = 走 baseline 默认 prompt
    baseline: form.value.baseline_prompt_variant_id
      ? { prompt_variant_id: form.value.baseline_prompt_variant_id }
      : {},
    // candidate: 空 = 走 baseline(等同 baseline,后端会拒绝)
    candidate: form.value.candidate_prompt_variant_id
      ? { prompt_variant_id: form.value.candidate_prompt_variant_id }
      : {},
  });
}

function goToCandidate() {
  emit("go-to-candidate");
}
</script>

<template>
  <section class="single-run-panel">
    <header>
      <div>
        <p>单篇 baseline / candidate compare</p>
        <h2>同一篇文章,同时跑 baseline 与 candidate</h2>
      </div>
      <span class="tag">双跑 · 单次提交</span>
    </header>

    <p v-if="error" class="error" aria-live="assertive">{{ error }}</p>

    <div v-if="!hasPublishedCandidate" class="empty-state">
      <strong>还没有已发布候选版本</strong>
      <p>先到「候选版本」完成命名、创建、发布,再回来发起单篇双跑 compare。</p>
      <button type="button" class="empty-state-cta" @click="goToCandidate">去候选版本发布</button>
    </div>

    <template v-else>
      <label class="source-field">
        <span>待验证文章(双跑共享)</span>
        <textarea v-model="form.text" rows="10" placeholder="粘贴英文文章或段落...同一份输入会并发进入 baseline 与 candidate 两次 workflow execution" />
      </label>

      <div class="form-grid">
        <label>
          <span>Candidate(必选)</span>
          <select v-model="form.candidate_prompt_variant_id">
            <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">{{ candidate.label }}</option>
            <option value="">— 选择候选 —</option>
          </select>
        </label>
        <label>
          <span>Baseline prompt(可选,留空走 baseline 默认)</span>
          <select v-model="form.baseline_prompt_variant_id">
            <option value="">— baseline 默认 —</option>
            <option v-for="candidate in candidateOptions" :key="`base-${candidate.value}`" :value="candidate.value">{{ candidate.label }}</option>
          </select>
        </label>
      </div>

      <div class="form-grid">
        <label>
          <span>模型方案</span>
          <select v-model="form.model_profile">
            <option v-for="option in modelOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <div v-if="selectedCandidateSummary.reading_goal || selectedCandidateSummary.reading_variant" class="locked-context">
          <div>
            <span>阅读目标</span>
            <strong>{{ readingGoalLabel(selectedCandidateSummary.reading_goal) }}</strong>
          </div>
          <div>
            <span>阅读场景</span>
            <strong>{{ readingVariantLabel(selectedCandidateSummary.reading_variant) }}</strong>
          </div>
        </div>
      </div>

      <details class="advanced">
        <summary>更多设置</summary>
        <div class="advanced-grid">
          <label>
            <span>检索增强</span>
            <select v-model="form.rag_mode" :disabled="Boolean(form.candidate_prompt_variant_id)">
              <option value="off">关闭</option>
              <option value="baseline">沿用 baseline</option>
              <option value="rag">强制使用 RAG</option>
              <option value="rag_fallback">RAG 失败时回退</option>
              <option value="settings">沿用运行时设置</option>
            </select>
          </label>
          <label>
            <span>调试记录</span>
            <select v-model="form.trace_scope">
              <option value="off">关闭</option>
              <option value="inherit">沿用上游设置</option>
            </select>
          </label>
          <label>
            <span>超时（秒）</span>
            <input v-model.number="form.timeout_seconds" type="number" min="1" />
          </label>
        </div>
      </details>

      <footer>
        <p v-if="!form.candidate_prompt_variant_id" class="warn-text">Candidate 必选,否则无法构成 baseline / candidate compare。</p>
        <button type="button" class="primary-button" :disabled="submitting || !form.candidate_prompt_variant_id" @click="submit">
          {{ submitting ? "双跑中..." : "开始双跑 compare" }}
        </button>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.single-run-panel {
  container-type: inline-size;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 16px;
}

header,
footer {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

header p,
footer p,
label span {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

header h2 {
  margin: 2px 0 0;
  font-size: 18px;
  line-height: 1.45;
}

header > div {
  flex: 1 1 auto;
  min-width: 0;
}

.tag {
  flex: 0 0 auto;
  align-self: flex-start;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
  border-radius: 999px;
  color: var(--theme--primary);
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  white-space: nowrap;
}

.empty-state {
  margin-top: 14px;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 18px 20px;
  display: grid;
  gap: 8px;
}

.empty-state strong {
  font-size: 14px;
  color: var(--theme--foreground);
}

.empty-state p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.6;
}

.empty-state-cta {
  align-self: flex-start;
  margin-top: 4px;
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border: 1px solid var(--theme--primary);
  padding: 8px 16px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.warn-text {
  color: var(--theme--warning);
}

.error {
  margin: 12px 0 0;
  border: 1px solid var(--theme--danger);
  border-radius: 8px;
  background: var(--theme--danger-background);
  color: var(--theme--foreground);
  padding: 10px 12px;
}

label {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.source-field {
  margin-top: 14px;
}

.form-grid,
.advanced-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.form-grid {
  margin-top: 14px;
}

.locked-context {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 14px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background-subdued);
  align-self: end;
  min-height: 36px;
  box-sizing: border-box;
}

.locked-context > div {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.locked-context span {
  font-weight: 700;
  color: var(--theme--foreground-subdued);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 11px;
}

.locked-context strong {
  font-weight: 600;
  color: var(--theme--foreground);
  background: var(--theme--background);
  border: 1px solid var(--theme--border-color);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.primary-button {
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border: 1px solid var(--theme--primary) !important;
}

.primary-button:hover:not(:disabled) {
  opacity: 0.9;
}

input,
select,
textarea,
button {
  min-height: 36px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  padding: 7px 9px;
}

textarea {
  resize: vertical;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  line-height: 1.45;
}

button {
  cursor: pointer;
  font-weight: 700;
  white-space: nowrap;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.advanced {
  border-top: 1px solid var(--theme--border-color);
  margin-top: 14px;
  padding-top: 12px;
}

.advanced summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
}

.advanced-grid {
  margin-top: 12px;
}

.span-2 {
  grid-column: 1 / -1;
}

footer {
  margin-top: 14px;
}

@container (max-width: 700px) {
  .form-grid,
  .advanced-grid {
    grid-template-columns: 1fr;
  }

  header,
  footer {
    display: grid;
  }
}

@container (max-width: 560px) {
  .single-run-panel {
    padding: 14px;
  }

  header h2 {
    font-size: 16px;
  }

  footer button {
    width: 100%;
  }
}
</style>
