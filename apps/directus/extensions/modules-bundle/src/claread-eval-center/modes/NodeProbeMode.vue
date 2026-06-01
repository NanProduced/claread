<script setup>
import { computed, onMounted, ref, watch } from "vue";
import ResultBlock from "../components/ResultBlock.vue";
import ReviewNotesPanel from "../components/ReviewNotesPanel.vue";
import NodeProbeOutputView from "../components/NodeProbeOutputView.vue";

const endpoint = "/eval-center/article-analysis/node-probe";
const modelProfilesEndpoint = "/eval-center/article-analysis/model-profiles";
const nodeProbeRunsEndpoint = "/items/eval_node_probe_runs";
const emit = defineEmits(["open-run-history"]);

const nodeOptions = [
  { text: "Grammar", value: "grammar" },
  { text: "Vocabulary", value: "vocabulary" },
  { text: "Translation", value: "translation" },
];

const readingVariants = [
  { text: "初阶阅读", value: "beginner_reading" },
  { text: "中阶阅读", value: "intermediate_reading" },
  { text: "精读", value: "intensive_reading" },
];

const promptModes = [
  { text: "业务主线 Baseline", value: "baseline" },
  { text: "关闭 Few-shot", value: "no_few_shot" },
  { text: "按 Variant ID 引用", value: "custom" },
];

const fewShotModes = [
  { text: "关闭", value: "off" },
  { text: "使用 baseline", value: "baseline" },
  { text: "使用 variant examples", value: "variant" },
];

const sourceTypes = [
  { text: "用户输入", value: "user_input" },
  { text: "每日文章", value: "daily_article" },
  { text: "导入文章", value: "imported" },
  { text: "OCR", value: "ocr" },
];

const sectionLinks = [
  { id: "eval-summary", label: "结果概览" },
  { id: "eval-prompt-packet", label: "Prompt Packet" },
  { id: "eval-output", label: "节点输出" },
  { id: "eval-evidence", label: "运行证据" },
  { id: "eval-raw", label: "原始数据" },
];

const activeNode = ref("grammar");
const text = ref("Although the plan looked simple, it required careful coordination across several teams.");
const readingVariant = ref("intermediate_reading");
const sourceType = ref("user_input");
const promptMode = ref("baseline");
const customVariantId = ref("");
const customFewShotMode = ref("off");
const modelProfile = ref("");
const modelProfiles = ref([]);
const modelProfilesLoading = ref(false);
const modelProfilesError = ref("");
const timeoutSeconds = ref(60);
const traceScope = ref("off");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const saveMessage = ref("");
const result = ref(null);
const lastRunMode = ref("");
const savedRecordId = ref("");

const canRun = computed(() => text.value.trim().length > 0 && !loading.value);
const status = computed(() => result.value?.status || "");
const runtime = computed(() => result.value?.runtime_summary || {});
const tokenUsage = computed(() => runtime.value?.aggregate || {});
const exampleSummary = computed(() => result.value?.example_summary || null);
const examples = computed(() => Array.isArray(exampleSummary.value?.examples) ? exampleSummary.value.examples : []);
const promptPreview = computed(() => result.value?.prompt_preview || "");
const agentInstructions = computed(() => result.value?.agent_instructions || "");
const preparedSentences = computed(() => Array.isArray(result.value?.prepared_sentences) ? result.value.prepared_sentences : []);
const nodeOutput = computed(() => result.value?.node_output || null);
const traceRefs = computed(() => result.value?.trace_refs || null);
const ragDebug = computed(() => result.value?.rag_debug || null);
const promptIdentity = computed(() => result.value?.prompt_identity || null);
const modelIdentity = computed(() => result.value?.model_identity || null);
const warnings = computed(() => Array.isArray(result.value?.warnings) ? result.value.warnings : []);
const preprocessSummary = computed(() => result.value?.preprocess_summary || null);
const workflowIdentity = computed(() => result.value?.workflow_identity || null);
const schemaIdentity = computed(() => result.value?.schema_identity || null);
const isDryRunResult = computed(() => lastRunMode.value === "dry_run");
const modelProfileOptions = computed(() => modelProfiles.value.map((item) => ({
  text: `${item.profile_name} · ${item.provider} / ${item.model_name}${item.annotation_route_default ? " · annotation 默认" : ""}`,
  value: item.profile_name,
})));
const selectedModelProfileSummary = computed(() => {
  const profile = modelProfiles.value.find((item) => item.profile_name === modelProfile.value.trim());
  if (profile) return profile;
  const currentProfile = modelIdentity.value?.profile_name;
  return modelProfiles.value.find((item) => item.profile_name === currentProfile) || null;
});
const defaultAnnotationProfile = computed(() =>
  modelProfiles.value.find((item) => item.annotation_route_default) || null,
);

const nodeLabel = computed(() => {
  const map = { grammar: "Grammar", vocabulary: "Vocabulary", translation: "Translation" };
  return map[activeNode.value] || activeNode.value;
});

watch(activeNode, () => {
  result.value = null;
  error.value = "";
  saveMessage.value = "";
  lastRunMode.value = "";
  savedRecordId.value = "";
});

watch([text, readingVariant, sourceType, promptMode, customVariantId, customFewShotMode, modelProfile], () => {
  saveMessage.value = "";
  savedRecordId.value = "";
});

onMounted(() => {
  void loadModelProfiles();
});

const runModeLabel = computed(() => {
  if (!lastRunMode.value) return "未运行";
  return lastRunMode.value === "dry_run" ? "Dry Run（仅预览 Prompt）" : "Real Run（调用 LLM）";
});

const baselineConsistency = computed(() => {
  if (promptMode.value === "baseline") {
    return {
      level: "baseline",
      label: "与业务主线一致",
      detail: activeNode.value === "grammar"
        ? "使用业务 grammar prompt policy、baseline examples 和 baseline agent instructions。"
        : `使用业务 ${activeNode.value} prompt policy、baseline examples 和 baseline agent instructions。`,
    };
  }
  if (promptMode.value === "no_few_shot") {
    return {
      level: "variant",
      label: "业务主线减去 Few-shot",
      detail: activeNode.value === "grammar"
        ? "保留业务 grammar policy 和 agent instructions，只关闭 examples 注入。"
        : `保留业务 ${activeNode.value} policy 和 agent instructions，只关闭 examples 注入。`,
    };
  }
  return {
    level: "custom",
    label: "Eval-only Variant",
    detail: activeNode.value === "grammar"
      ? "通过 prompt_override 做实验，不代表业务主线配置。"
      : "通过 prompt_override 做实验，不代表业务主线配置。当前 UI 只传 variant id 与 few-shot mode，完整 policy/examples 编辑后续在 Prompt Variant 模式接入。",
  };
});

const preprocessMetrics = computed(() => ({
  sentences: preprocessSummary.value?.sentence_count ?? preprocessSummary.value?.sentences_count ?? preparedSentences.value.length,
  paragraphs: preprocessSummary.value?.paragraph_count ?? preprocessSummary.value?.paragraphs_count ?? "—",
  words: preprocessSummary.value?.word_count ?? preprocessSummary.value?.words_count ?? "—",
}));

const warningSummary = computed(() => {
  if (!warnings.value.length) return "无";
  const first = warnings.value[0];
  return first?.code || first?.message || `${warnings.value.length} warnings`;
});

function help(textValue) {
  return textValue;
}

function buildPromptOverride() {
  if (promptMode.value === "baseline") return {};

  if (promptMode.value === "no_few_shot") {
    return {
      prompt_variant_id: "no-few-shot-v1",
      prompt_override: {
        variant_id: "no-few-shot-v1",
        target: "article_analysis",
        few_shot_mode: "off",
      },
    };
  }

  const variantId = customVariantId.value.trim();
  if (!variantId) return {};
  return {
    prompt_variant_id: variantId,
    prompt_override: {
      variant_id: variantId,
      target: "article_analysis",
      few_shot_mode: customFewShotMode.value,
    },
  };
}

function buildModelSelection() {
  const profile = modelProfile.value.trim();
  return profile ? { default_profile: profile } : null;
}

function buildRequest(dryRun) {
  return {
    node_name: activeNode.value,
    text: text.value,
    reading_goal: "daily_reading",
    reading_variant: readingVariant.value,
    source_type: sourceType.value,
    rag_mode: "off",
    trace_scope: traceScope.value,
    timeout_seconds: Number(timeoutSeconds.value) || 60,
    dry_run: dryRun,
    model_selection: buildModelSelection(),
    ...buildPromptOverride(),
  };
}

function promptModeForSave() {
  if (promptMode.value === "custom") return "variant";
  return promptMode.value;
}

function inputExcerpt() {
  const normalized = text.value.replace(/\s+/g, " ").trim();
  return normalized.length > 280 ? `${normalized.slice(0, 280)}...` : normalized;
}

const SENSITIVE_FIELD_NAMES = new Set([
  "api_key",
  "apikey",
  "access_token",
  "refresh_token",
  "auth_token",
  "bearer_token",
  "authorization",
  "auth_header",
  "cookie",
  "session_id",
  "password",
  "passwd",
  "secret",
  "client_secret",
  "app_secret",
  "private_key",
  "private_key_pem",
  "credential",
  "credentials",
  "extra_headers",
]);

function stripSensitiveJson(value) {
  if (Array.isArray(value)) return value.map(stripSensitiveJson);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !SENSITIVE_FIELD_NAMES.has(String(key).toLowerCase()))
        .map(([key, item]) => [key, stripSensitiveJson(item)]),
    );
  }
  return value;
}

function summarizeExampleSummary(summary) {
  if (!summary) return {};
  const examples = Array.isArray(summary.examples) ? summary.examples : [];
  return {
    selection_mode: summary.selection_mode || null,
    example_count: Number.isFinite(summary.example_count)
      ? summary.example_count
      : examples.length,
    examples_saved: false,
  };
}

function sanitizeTraceRefs(traceRefsValue) {
  if (!traceRefsValue) return {};
  return {
    request_id: traceRefsValue.request_id || null,
    langsmith_enabled: Boolean(traceRefsValue.langsmith_enabled),
    langsmith_project: traceRefsValue.langsmith_project || null,
    workflow_run_id: traceRefsValue.workflow_run_id || null,
  };
}

function sanitizeError(errorValue) {
  if (!errorValue) return null;
  return {
    code: errorValue.code || "Error",
    message: errorValue.message || "Node probe failed.",
  };
}

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
    const message = payload?.errors?.[0]?.message || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return payload?.data !== undefined ? payload.data : payload;
}

async function loadModelProfiles() {
  modelProfilesLoading.value = true;
  modelProfilesError.value = "";
  try {
    const data = await fetchJson(modelProfilesEndpoint, { method: "GET" });
    modelProfiles.value = Array.isArray(data) ? data : [];
  } catch (err) {
    modelProfiles.value = [];
    modelProfilesError.value = err?.message || "读取模型列表失败。";
  } finally {
    modelProfilesLoading.value = false;
  }
}

function buildSavePayload() {
  const snapshot = result.value?.request_snapshot || {};
  return {
    status: result.value?.status || "failed",
    node_name: result.value?.node_name || activeNode.value,
    dry_run: isDryRunResult.value,
    reading_goal: snapshot.reading_goal || "daily_reading",
    reading_variant: snapshot.reading_variant || readingVariant.value,
    source_type: snapshot.source_type || sourceType.value,
    input_text_hash: snapshot.source_text_hash || "",
    input_excerpt: inputExcerpt(),
    input_text: text.value,
    prompt_mode: promptModeForSave(),
    prompt_variant_id: promptIdentity.value?.prompt_variant_id || null,
    prompt_identity_json: promptIdentity.value || {},
    prompt_preview: promptPreview.value || null,
    agent_instructions: agentInstructions.value || null,
    model_profile: modelProfile.value.trim() || null,
    model_identity_json: stripSensitiveJson(modelIdentity.value || {}),
    workflow_identity_json: workflowIdentity.value || {},
    schema_identity_json: schemaIdentity.value || {},
    prepared_sentences_json: preparedSentences.value,
    example_summary_json: summarizeExampleSummary(exampleSummary.value),
    preprocess_summary_json: preprocessSummary.value || {},
    node_output_json: nodeOutput.value,
    rag_debug_json: ragDebug.value || {},
    warnings_json: warnings.value,
    runtime_summary_json: stripSensitiveJson(runtime.value || {}),
    trace_refs_json: sanitizeTraceRefs(traceRefs.value),
    error_json: sanitizeError(result.value?.error),
    tags: [],
    promote_candidate: false,
  };
}

async function runProbe(dryRun) {
  if (!canRun.value) return;
  loading.value = true;
  error.value = "";
  lastRunMode.value = dryRun ? "dry_run" : "run";

  const controller = new AbortController();
  const requestTimeoutMs = Math.min((Number(timeoutSeconds.value) || 60) * 1000 + 15000, 195000);
  const timeout = setTimeout(() => controller.abort(), requestTimeoutMs);

  try {
    result.value = await fetchJson(endpoint, {
      method: "POST",
      body: JSON.stringify(buildRequest(dryRun)),
      signal: controller.signal,
    });
    saveMessage.value = "";
    savedRecordId.value = "";
  } catch (err) {
    error.value =
      err?.name === "AbortError"
        ? "节点探针请求超时。"
        : err?.message || "节点探针运行失败。";
  } finally {
    clearTimeout(timeout);
    loading.value = false;
  }
}

async function saveNodeProbeRun() {
  if (!result.value || saving.value) return;
  saving.value = true;
  error.value = "";
  saveMessage.value = "";

  try {
    const payload = await fetchJson(nodeProbeRunsEndpoint, {
      method: "POST",
      body: JSON.stringify(buildSavePayload()),
    });
    savedRecordId.value = payload?.id || "";
    saveMessage.value = `已保存：${savedRecordId.value || "eval_node_probe_runs"}`;
  } catch (err) {
    error.value = err?.message || "保存 Node Probe 结果失败。";
  } finally {
    saving.value = false;
  }
}

function formatJson(value) {
  if (value == null) return "";
  return JSON.stringify(value, null, 2);
}

function formatMs(value) {
  return typeof value === "number" ? `${value} ms` : "—";
}

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function scrollToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function copyResultJson() {
  if (!result.value) return;
  await navigator.clipboard.writeText(formatJson(result.value));
}

function openSavedRecord() {
  if (!savedRecordId.value) return;
  emit("open-run-history", {
    source: "node_probe",
    recordId: savedRecordId.value,
  });
}
</script>

<template>
  <section class="mode-shell">
    <div class="node-tabs">
      <button
        v-for="node in nodeOptions"
        :key="node.value"
        class="node-tab"
        :class="{ 'is-active': activeNode === node.value }"
        type="button"
        @click="activeNode = node.value"
      >
        {{ node.text }}
      </button>
    </div>

    <div class="grammar-lab">
      <section class="lab-pane controls-pane">
        <div class="section-heading">
          <div>
            <h2>输入与运行配置</h2>
            <span>当前只开放 learning topology。完整 policy/examples 编辑后续在 Prompt Variant 模式接入。</span>
          </div>
        </div>

        <label class="field-label">
          原文
          <span class="help-dot" :title="help('会先经过 services/api 的 prepare_input() 清洗、分段和切句。')">?</span>
        </label>
        <v-textarea
          v-model="text"
          class="source-input"
          :rows="9"
          placeholder="粘贴英文文章、段落或用于调试的句子..."
        />

        <div class="control-grid">
          <label class="field-block">
            <span>阅读变体 <i :title="help('只展示 daily_reading 下的 learning variants。不同 variant 最终仍要兼容同一 render schema。')">?</i></span>
            <v-select v-model="readingVariant" :items="readingVariants" />
          </label>
          <label class="field-block">
            <span>来源类型</span>
            <v-select v-model="sourceType" :items="sourceTypes" />
          </label>
          <label class="field-block">
            <span>Prompt 模式 <i :title="help('Baseline 与业务主线一致；关闭 Few-shot 只移除 examples；Variant by ID 是 eval-only override。')">?</i></span>
            <v-select v-model="promptMode" :items="promptModes" />
          </label>
          <label v-if="promptMode === 'custom'" class="field-block">
            <span>Variant ID</span>
            <v-input v-model="customVariantId" placeholder="my-grammar-variant" />
          </label>
          <label v-if="promptMode === 'custom'" class="field-block">
            <span>Few-shot 模式</span>
            <v-select v-model="customFewShotMode" :items="fewShotModes" />
          </label>
          <label class="field-block">
            <span>模型 Profile <i :title="help('默认使用 annotation_generation route 的默认 profile。这里提供安全摘要下拉，支持保留手动输入。')">?</i></span>
            <v-select
              v-model="modelProfile"
              :items="modelProfileOptions"
              :loading="modelProfilesLoading"
              :allow-other="true"
              placeholder="使用 annotation 默认模型"
            />
            <small class="field-hint">
              {{
                selectedModelProfileSummary
                  ? `${selectedModelProfileSummary.provider} / ${selectedModelProfileSummary.model_name}`
                  : defaultAnnotationProfile
                    ? `默认 annotation route: ${defaultAnnotationProfile.profile_name} · ${defaultAnnotationProfile.provider} / ${defaultAnnotationProfile.model_name}`
                    : "未加载到模型摘要，将继续沿用 services/api 当前默认路由。"
              }}
            </small>
          </label>
          <label class="field-block">
            <span>超时秒数</span>
            <v-input v-model="timeoutSeconds" type="number" min="1" />
          </label>
        </div>

        <div class="action-row">
          <v-button
            secondary
            :disabled="!canRun"
            :loading="loading && lastRunMode === 'dry_run'"
            @click="runProbe(true)"
          >
            预览 Prompt
          </v-button>
          <v-button
            :disabled="!canRun"
            :loading="loading && lastRunMode === 'run'"
            @click="runProbe(false)"
          >
            运行 {{ nodeLabel }} 节点
          </v-button>
        </div>

        <p class="persistence-note">“预览 Prompt”会执行真实预处理和 example 选择，但不会调用目标节点 LLM；“运行节点”才会产出节点输出与对应 token 统计。</p>
        <p class="persistence-note">当前运行结果不会自动保存。需要保留时点击“保存本次结果”，它会写入 eval_node_probe_runs；正式 workflow eval 仍保存在 evals/runs。</p>
        <p v-if="modelProfilesError" class="error-message">{{ modelProfilesError }}</p>
        <p v-if="error" class="error-message">{{ error }}</p>
      </section>

      <section class="lab-pane result-pane">
        <div class="section-heading">
          <div>
            <h2>运行结果</h2>
            <span>{{ runModeLabel }}</span>
          </div>
          <v-button
            v-if="result"
            small
            secondary
            icon
            title="复制完整结果 JSON"
            @click="copyResultJson"
          >
            content_copy
          </v-button>
        </div>

        <div v-if="result" class="save-panel">
          <div class="save-copy">
            <strong>保存当前 probe 结果</strong>
            <p>保存后可直接跳转运行历史，并使用统一的 Review Notes 机制记录 verdict、观察和 promote candidate 决策。</p>
          </div>
          <div class="save-actions">
            <v-button small :loading="saving" @click="saveNodeProbeRun">保存本次结果</v-button>
            <v-button v-if="savedRecordId" small secondary @click="openSavedRecord">前往运行历史</v-button>
            <span v-if="saveMessage">{{ saveMessage }}</span>
          </div>
        </div>

        <ReviewNotesPanel
          v-if="savedRecordId"
          title="Node Probe Review Notes"
          target-type="node_probe_run"
          :target-id="savedRecordId"
        />

        <nav v-if="result" class="section-nav" aria-label="结果分区">
          <button v-for="link in sectionLinks" :key="link.id" type="button" @click="scrollToSection(link.id)">
            {{ link.label }}
          </button>
        </nav>

        <div id="eval-summary" class="summary-grid">
          <div>
            <span>状态</span>
            <strong>{{ status || "—" }}</strong>
          </div>
          <div>
            <span>耗时</span>
            <strong>{{ formatMs(runtime.latency_ms) }}</strong>
          </div>
          <div>
            <span>Warnings</span>
            <strong>{{ warnings.length ? warnings.length : "0" }}</strong>
            <small>{{ warningSummary }}</small>
          </div>
          <div>
            <span>预处理</span>
            <strong>{{ preprocessMetrics.sentences }} 句</strong>
            <small>{{ preprocessMetrics.words }} words</small>
          </div>
          <div>
            <span>Examples</span>
            <strong>{{ exampleSummary?.example_count ?? "—" }}</strong>
            <small>{{ exampleSummary?.selection_mode || "—" }}</small>
          </div>
          <div class="token-card">
            <span>Tokens</span>
            <strong>{{ tokenUsage.total_tokens ?? "—" }}</strong>
            <small>in {{ tokenUsage.input_tokens ?? "—" }} / out {{ tokenUsage.output_tokens ?? "—" }}</small>
          </div>
        </div>

        <div class="identity-grid">
          <section>
            <h3>模型</h3>
            <dl>
              <dt>Provider</dt><dd>{{ dash(modelIdentity?.provider) }}</dd>
              <dt>Model</dt><dd>{{ dash(modelIdentity?.model_name) }}</dd>
              <dt>Profile</dt><dd>{{ dash(modelIdentity?.profile_name) }}</dd>
              <dt>Route</dt><dd>{{ dash(modelIdentity?.route) }}</dd>
            </dl>
          </section>
          <section>
            <h3>Prompt</h3>
            <dl>
              <dt>Version</dt><dd>{{ dash(promptIdentity?.prompt_version) }}</dd>
              <dt>Variant</dt><dd>{{ dash(promptIdentity?.prompt_variant_id) }}</dd>
              <dt>Snapshot</dt><dd>{{ dash(promptIdentity?.prompt_snapshot_hash) }}</dd>
              <dt>一致性</dt><dd><span class="status-pill" :class="baselineConsistency.level" :title="baselineConsistency.detail">{{ baselineConsistency.label }}</span></dd>
            </dl>
          </section>
          <section>
            <h3>Workflow</h3>
            <dl>
              <dt>Name</dt><dd>{{ dash(workflowIdentity?.workflow_name || workflowIdentity?.name) }}</dd>
              <dt>Version</dt><dd>{{ dash(workflowIdentity?.workflow_version || workflowIdentity?.version) }}</dd>
              <dt>Node</dt><dd>{{ activeNode }}</dd>
              <dt>RAG</dt><dd>off</dd>
            </dl>
          </section>
          <section>
            <h3>Schema</h3>
            <dl>
              <dt>Version</dt><dd>{{ dash(schemaIdentity?.schema_version || schemaIdentity?.version) }}</dd>
              <dt>Result</dt><dd>{{ dash(schemaIdentity?.result_schema || schemaIdentity?.name) }}</dd>
              <dt>Goal</dt><dd>daily_reading</dd>
              <dt>Variant</dt><dd>{{ readingVariant }}</dd>
            </dl>
          </section>
        </div>

        <ResultBlock id="eval-prompt-packet" :title="`${nodeLabel} Prompt Packet`" :open="Boolean(result)">
          <div class="packet-layout">
            <section class="packet-card">
              <header>
                <h4>System Prompt / Agent Instructions</h4>
                <small>业务 agent instructions</small>
              </header>
              <pre>{{ agentInstructions || "运行后展示。当前 eval override v1 不覆盖 agent instructions。" }}</pre>
            </section>

            <section class="packet-card">
              <header>
                <h4>Runtime Prompt</h4>
                <small>{{ isDryRunResult ? "已预览，不调用节点 LLM" : "节点实际发送 prompt" }}</small>
              </header>
              <pre>{{ promptPreview || `点击"预览 Prompt"或"运行 ${nodeLabel} 节点"后展示。` }}</pre>
            </section>

            <section class="packet-card">
              <header>
                <h4>Examples</h4>
                <small>{{ exampleSummary?.selection_mode || "—" }} / {{ exampleSummary?.example_count ?? 0 }} 条</small>
              </header>
              <pre>{{ formatJson({ summary: exampleSummary, examples }) || "暂无 examples。" }}</pre>
            </section>

            <section class="packet-card">
              <header>
                <h4>Prepared Sentences</h4>
                <small>prepare_input() 真实切句结果</small>
              </header>
              <div v-if="preparedSentences.length" class="sentence-list">
                <div v-for="sentence in preparedSentences" :key="sentence.sentence_id" class="sentence-row">
                  <code>{{ sentence.sentence_id }}</code>
                  <span>{{ sentence.text }}</span>
                </div>
              </div>
              <p v-else class="muted-line">运行后展示 prepare_input() 产出的句子。</p>
            </section>
          </div>
        </ResultBlock>

        <ResultBlock id="eval-output" :title="`${nodeLabel} Draft Output`" :open="Boolean(result) && !isDryRunResult">
          <p v-if="isDryRunResult" class="muted-line">Dry run 已完成预处理与 prompt 组装，但没有调用目标节点 LLM。</p>
          <NodeProbeOutputView
            v-else
            :node-name="activeNode"
            :output="nodeOutput"
            :prepared-sentences="preparedSentences"
          />
        </ResultBlock>

        <ResultBlock id="eval-evidence" title="Observations / Trace">
          <div class="observation-grid">
            <div>
              <span>Warnings</span>
              <strong>{{ warnings.length }}</strong>
              <small>{{ warningSummary }}</small>
            </div>
            <div>
              <span>Sentences</span>
              <strong>{{ preprocessMetrics.sentences }}</strong>
              <small>Paragraphs {{ preprocessMetrics.paragraphs }}</small>
            </div>
            <div>
              <span>Trace</span>
              <strong>{{ traceRefs ? "有" : "无" }}</strong>
              <small>{{ dash(traceRefs?.langsmith_url || traceRefs?.run_id) }}</small>
            </div>
          </div>
          <pre>{{ formatJson({ warnings, preprocess_summary: preprocessSummary, runtime_summary: runtime, rag_debug: ragDebug, trace_refs: traceRefs }) }}</pre>
        </ResultBlock>

        <ResultBlock title="Raw Node Output JSON">
          <pre>{{ formatJson(nodeOutput) || "暂无节点输出。" }}</pre>
        </ResultBlock>

        <ResultBlock id="eval-raw" title="完整结果 JSON">
          <pre>{{ formatJson(result) || "暂无结果。" }}</pre>
        </ResultBlock>
      </section>
    </div>
  </section>
</template>

<style scoped>
.node-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.node-tab {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 8px 12px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  transition: border-color 160ms ease, color 160ms ease;
}

.node-tab.is-active {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.grammar-lab {
  display: grid;
  grid-template-columns: minmax(320px, 0.78fr) minmax(0, 1.22fr);
  gap: 24px;
}

.lab-pane {
  box-sizing: border-box;
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 20px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.section-heading h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.3;
}

.section-heading span,
.persistence-note,
.muted-line,
.save-actions span {
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.field-label,
.field-block span {
  display: block;
  margin-bottom: 6px;
  color: var(--theme--foreground);
  font-size: 13px;
  font-weight: 700;
}

.field-block i,
.help-dot {
  display: inline-flex;
  width: 16px;
  height: 16px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-style: normal;
  font-size: 11px;
}

.field-hint {
  display: block;
  margin-top: 6px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.5;
}

.source-input {
  margin-bottom: 16px;
}

.control-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

.error-message {
  margin: 14px 0 0;
  color: var(--theme--danger);
  font-size: 13px;
}

.save-panel {
  display: grid;
  gap: 12px;
  margin-bottom: 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px;
}

.save-copy strong {
  display: block;
  margin-bottom: 4px;
  font-size: 14px;
}

.save-copy p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  line-height: 1.6;
}

.save-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.section-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 14px;
}

.section-nav button {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  padding: 5px 10px;
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
}

.section-nav button:hover {
  color: var(--theme--primary);
  border-color: var(--theme--primary);
}

.summary-grid,
.identity-grid,
.observation-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.identity-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.summary-grid div,
.identity-grid section,
.observation-grid div {
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
}

.summary-grid span,
.identity-grid dt,
.observation-grid span {
  display: block;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.2;
}

.summary-grid strong,
.identity-grid dd,
.observation-grid strong {
  display: block;
  margin: 4px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 700;
}

.summary-grid small,
.observation-grid small {
  display: block;
  margin-top: 3px;
  overflow: hidden;
  color: var(--theme--foreground-subdued);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
}

.identity-grid h3 {
  margin: 0 0 10px;
  font-size: 14px;
}

.identity-grid dl {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 7px 8px;
  margin: 0;
}

.identity-grid dd {
  margin-top: 0;
}

.status-pill {
  display: inline-flex;
  max-width: 100%;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 700;
}

.status-pill.baseline {
  background: var(--theme--success-background);
}

.status-pill.variant,
.status-pill.custom {
  background: var(--theme--warning-background);
}

.packet-layout {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}

.packet-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px;
}

.packet-card header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.packet-card h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}

.packet-card small {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

pre {
  max-height: 420px;
  overflow: auto;
  margin: 12px 0 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 12px;
  color: var(--theme--foreground);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.sentence-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.sentence-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
  font-size: 13px;
  line-height: 1.6;
}

.sentence-row code {
  color: var(--theme--foreground-subdued);
}

@media (max-width: 1100px) {
  .grammar-lab {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 840px) {
  .summary-grid,
  .identity-grid,
  .observation-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .lab-pane {
    padding: 16px;
  }

  .section-heading {
    flex-direction: column;
    gap: 8px;
  }

  .control-grid {
    grid-template-columns: 1fr;
  }

  .save-panel {
    grid-template-columns: 1fr;
  }

  .action-row {
    flex-direction: column;
  }

  .action-row :deep(.v-button) {
    width: 100%;
  }
}
</style>
