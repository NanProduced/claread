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
  { value: "", label: "使用默认模型方案" },
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
    error.value = "请先粘贴一段要验证的英文文章。";
    return;
  }

  let modelSelection = {};
  try {
    modelSelection = JSON.parse(form.value.model_selection_json || "{}");
    if (!modelSelection || typeof modelSelection !== "object" || Array.isArray(modelSelection)) {
      throw new Error("高级模型设置需要是 JSON 对象。");
    }
  } catch (err) {
    error.value = err?.message || "高级模型设置无法解析。";
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
        <p>单篇验证</p>
        <h2>先跑一篇文章，确认候选版本是否值得继续批量回归</h2>
      </div>
      <span title="立即同步运行完整 workflow，结果只用于当前页面验证。">不入队列</span>
    </header>

    <p class="hint">
      推荐路径：贴文章，选择一个已发布候选版本，点“开始验证”。
      <span v-if="candidateOptions.length === 0">当前还没有已发布候选版本，先用 baseline 验证或到“候选版本”发布一个版本。</span>
    </p>
    <p v-if="error" class="error" aria-live="assertive">{{ error }}</p>

    <label class="source-field">
      <span title="这里会跑完整 learning workflow。">待验证文章</span>
      <textarea v-model="form.text" rows="10" placeholder="粘贴英文文章或段落..." />
    </label>

    <div class="form-grid">
      <label>
        <span title="不选时直接使用 baseline。">候选版本</span>
        <select v-model="form.prompt_variant_id">
          <option value="">使用 baseline</option>
          <option v-for="candidate in candidateOptions" :key="candidate.value" :value="candidate.value">{{ candidate.label }}</option>
        </select>
      </label>
      <label>
        <span title="优先选择模型方案，只有少数排障场景才需要写高级 JSON。">模型方案</span>
        <select v-model="form.model_profile">
          <option v-for="option in modelOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
    </div>

    <details class="advanced">
      <summary>更多设置</summary>
      <div class="advanced-grid">
        <label>
          <span title="决定默认的阅读解释风格。">阅读目标</span>
          <select v-model="form.reading_goal">
            <option value="daily_reading">日常阅读</option>
            <option value="exam">考试阅读</option>
          </select>
        </label>
        <label>
          <span title="细化当前阅读目标的输出密度。">阅读场景</span>
          <select v-model="form.reading_variant">
            <option v-for="option in variantOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label>
          <span title="仅在排查检索相关问题时需要修改。">检索增强</span>
          <select v-model="form.rag_mode" :disabled="Boolean(form.prompt_variant_id)">
            <option value="off">关闭</option>
            <option value="baseline">沿用 baseline</option>
            <option value="rag">强制使用 RAG</option>
            <option value="rag_fallback">RAG 失败时回退</option>
            <option value="settings">沿用运行时设置</option>
          </select>
        </label>
        <label>
          <span title="只在排障时需要保留更详细的执行记录。">调试记录</span>
          <select v-model="form.trace_scope">
            <option value="off">关闭</option>
            <option value="isolated">仅保留当前验证</option>
            <option value="inherit">沿用上游设置</option>
          </select>
        </label>
        <label>
          <span title="单篇 workflow 最长等待时间。">超时（秒）</span>
          <input v-model.number="form.timeout_seconds" type="number" min="1" />
        </label>
        <label class="span-2">
          <span title="默认情况下不需要填写。只有在排查模型路由问题时再展开。">高级模型设置 JSON</span>
          <textarea v-model="form.model_selection_json" rows="4" spellcheck="false" />
        </label>
      </div>
    </details>

    <footer>
      <p>{{ form.prompt_variant_id ? "本次将使用已发布候选版本进行验证。" : "本次将使用 baseline 进行验证。" }}</p>
      <button type="button" :disabled="submitting" title="立即验证这篇文章的完整 workflow 输出。" @click="submit">
        {{ submitting ? "验证中..." : "开始验证" }}
      </button>
    </footer>
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
  line-height: 1.45;
}

header > div {
  flex: 1 1 auto;
  min-width: 0;
}

header > span {
  flex: 0 0 auto;
  align-self: flex-start;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  padding: 4px 8px;
  white-space: nowrap;
}

.hint {
  margin-top: 12px;
  line-height: 1.6;
}

.hint span {
  display: block;
  margin-top: 4px;
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
