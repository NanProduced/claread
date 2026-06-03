<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  candidates: { type: Array, default: () => [] },
  modelProfiles: { type: Array, default: () => [] },
  submitting: { type: Boolean, default: false },
});
const emit = defineEmits(["submit"]);

const form = ref({
  text: "",
  reading_goal: "daily_reading",
  reading_variant: "intermediate_reading",
  prompt_variant_id: "",
  model_profile: "",
  rag_mode: "off",
  trace_scope: "off",
  timeout_seconds: 120,
  model_selection_json: "{}",
});
const error = ref("");

const variantOptions = computed(() => form.value.reading_goal === "exam"
  ? [
    { value: "gaokao", label: "高考" },
    { value: "cet", label: "CET" },
    { value: "kaoyan", label: "考研" },
    { value: "tem", label: "TEM" },
    { value: "ielts_toefl", label: "IELTS / TOEFL" },
  ]
  : [
    { value: "beginner_reading", label: "入门阅读" },
    { value: "intermediate_reading", label: "进阶阅读" },
    { value: "intensive_reading", label: "精读" },
  ]);

const candidateOptions = computed(() => props.candidates
  .filter((candidate) => candidate.prompt_bundle_summary?.topology_mode === "learning")
  .map((candidate) => ({
    value: candidate.variant_id,
    label: `${candidate.variant_id} / ${candidate.prompt_bundle_summary?.reading_variant || "learning"}`,
  })));

const modelOptions = computed(() => [
  { value: "", label: "使用 Claread 默认模型" },
  ...props.modelProfiles.map((profile) => ({
    value: profile.profile_name,
    label: `${profile.profile_name} · ${profile.model_name}`,
  })),
]);

watch(
  () => form.value.reading_goal,
  (value) => {
    form.value.reading_variant = value === "exam" ? "gaokao" : "intermediate_reading";
  },
);

watch(
  () => form.value.prompt_variant_id,
  (value) => {
    if (value) form.value.rag_mode = "off";
  },
);

function submit() {
  error.value = "";
  const text = form.value.text.trim();
  if (!text) {
    error.value = "请先输入要解析的文章文本。";
    return;
  }

  let modelSelection = {};
  try {
    modelSelection = JSON.parse(form.value.model_selection_json || "{}");
    if (!modelSelection || typeof modelSelection !== "object" || Array.isArray(modelSelection)) {
      throw new Error("模型选择 JSON 必须是对象。");
    }
  } catch (err) {
    error.value = err?.message || "模型选择 JSON 无效。";
    return;
  }
  if (form.value.model_profile) {
    modelSelection = {
      ...modelSelection,
      default_profile: form.value.model_profile,
    };
  }

  emit("submit", {
    text,
    reading_goal: form.value.reading_goal,
    reading_variant: form.value.reading_variant,
    source_type: "user_input",
    rag_mode: form.value.rag_mode,
    trace_scope: form.value.trace_scope,
    timeout_seconds: Number(form.value.timeout_seconds) || 120,
    model_selection: modelSelection,
    ...(form.value.prompt_variant_id ? { prompt_variant_id: form.value.prompt_variant_id } : {}),
  });
}
</script>

<template>
  <section class="single-run-panel">
    <header>
      <div>
        <p>单条调试</p>
        <h2>粘贴一篇文章验证 Workflow Candidate</h2>
      </div>
      <span title="同步调用 services/api workflow eval，不进入 dataset runner 队列。">Single Run</span>
    </header>

    <p class="hint">先用单条调试验证 candidate；确认有效后再做数据集批跑。</p>
    <p v-if="error" class="error">{{ error }}</p>

    <label class="source-field">
      <span title="待解析的原文。这里会跑完整 learning workflow，而不是单个 node。">待解析文章</span>
      <textarea v-model="form.text" rows="9" placeholder="粘贴英文文章或段落..." />
    </label>

    <div class="form-grid">
      <label>
        <span title="当前 Workflow Lab 只支持 learning；academic 不在本模块内。">阅读目标</span>
        <select v-model="form.reading_goal">
          <option value="daily_reading">日常阅读</option>
          <option value="exam">考试阅读</option>
        </select>
      </label>
      <label>
        <span title="影响 baseline prompt policy focus 和输出密度。">阅读场景</span>
        <select v-model="form.reading_variant">
          <option v-for="option in variantOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <label>
        <span title="选择 ready_for_eval 的 workflow prompt bundle；不选则使用 baseline prompt。">Candidate</span>
        <select v-model="form.prompt_variant_id">
          <option value="">Baseline prompt</option>
          <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">{{ candidate.label }}</option>
        </select>
      </label>
      <label>
        <span title="像 Node Lab 一样选择模型 profile。留空表示使用 Claread 默认模型路由。">模型 Profile</span>
        <select v-model="form.model_profile">
          <option v-for="option in modelOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <label>
        <span title="Candidate 与 RAG 互斥；选择 Candidate 后固定为 off。">RAG 模式</span>
        <select v-model="form.rag_mode" :disabled="Boolean(form.prompt_variant_id)">
          <option value="off">off</option>
          <option value="baseline">baseline</option>
          <option value="rag">rag</option>
          <option value="rag_fallback">rag_fallback</option>
          <option value="settings">settings</option>
        </select>
      </label>
      <label>
        <span title="调试真实 workflow 时可使用 isolated；日常快速验证保持 off。">Trace</span>
        <select v-model="form.trace_scope">
          <option value="off">off</option>
          <option value="isolated">isolated</option>
          <option value="inherit">inherit</option>
        </select>
      </label>
      <label>
        <span title="单条 workflow 最大等待时间，单位秒。">超时时间（秒）</span>
        <input v-model.number="form.timeout_seconds" type="number" min="1" />
      </label>
    </div>

    <details class="advanced">
      <summary>高级模型选择 JSON</summary>
      <textarea v-model="form.model_selection_json" rows="4" spellcheck="false" />
    </details>

    <footer>
      <p>{{ form.prompt_variant_id ? "将使用 Candidate prompt snapshot 运行。" : "将使用 Claread baseline prompt 运行。" }}</p>
      <button type="button" :disabled="submitting" title="立即同步运行完整 workflow。" @click="submit">
        {{ submitting ? "运行中..." : "运行 Single Run" }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.single-run-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
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
label span,
.hint {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
  margin: 2px 0 0;
  font-size: 18px;
}
header > span {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  padding: 4px 8px;
}
.hint {
  margin-top: 12px;
}
.error {
  color: var(--theme--danger);
  margin: 12px 0 0;
}
label {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.source-field {
  margin-top: 14px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
input,
select,
textarea,
button {
  min-height: 36px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
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
.advanced textarea {
  margin-top: 8px;
  width: 100%;
}
footer {
  margin-top: 14px;
}
@media (max-width: 760px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
  header,
  footer {
    display: grid;
  }
}
</style>
