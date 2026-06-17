<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";

const router = useRouter();

const tabs = {
  providers: {
    collection: "llm_providers",
    label: "Providers",
    description: "供应商连接配置：定义 LLM 供应商的 adapter 类型、base_url、API key 环境变量、兼容性声明（openai_profile）和供应商级默认参数（model_settings）。",
    fields: [
      { name: "adapter", desc: "供应商适配器类型" },
      { name: "base_url", desc: "API 端点" },
      { name: "api_key_env", desc: "API key 环境变量名" },
      { name: "openai_profile", desc: "OpenAI 兼容性声明" },
      { name: "model_settings", desc: "供应商级默认参数" },
    ],
    role: "链路第 1 层：连接与兼容性。Provider 是整个配置链的起点，决定连哪个供应商、用什么协议。",
    scenarios: [
      "新增供应商：填写 adapter + base_url + api_key_env",
      "切换兼容模式：调整 openai_profile",
      "供应商级默认参数：修改 model_settings（影响该供应商下所有 model）",
    ],
  },
  models: {
    collection: "llm_models",
    label: "Models",
    description: "远端模型定义：每个 model 属于一个 provider，声明远端模型名（model_name），可覆盖 provider 级参数。",
    fields: [
      { name: "provider", desc: "所属供应商（FK）" },
      { name: "model_name", desc: "远端模型标识" },
      { name: "provider_options", desc: "模型级供应商参数覆盖" },
      { name: "openai_profile", desc: "模型级兼容性覆盖" },
      { name: "model_settings", desc: "模型级参数覆盖" },
    ],
    role: "链路第 2 层：远端模型标识。Model 声明供应商的哪个模型，可覆盖供应商级默认值。",
    scenarios: [
      "新增模型：选择 provider + 填写 model_name",
      "模型级参数覆盖：修改 provider_options 或 model_settings",
      "切换模型兼容模式：覆盖 openai_profile",
    ],
  },
  profiles: {
    collection: "llm_profiles",
    label: "Profiles",
    description: "场景级配置：每个 profile 绑定一个 model，可附加场景专属 model_settings 覆盖。Profile 是 Preset 路由映射的目标。",
    fields: [
      { name: "model", desc: "所属模型（FK）" },
      { name: "model_settings", desc: "场景级参数覆盖" },
    ],
    role: "链路第 3 层：业务场景 settings。Profile 是同一个 model 在不同场景下的参数集，是 Preset 路由映射的目标。",
    scenarios: [
      "新增场景配置：选择 model + 填写 model_settings",
      "调整场景参数：修改 model_settings（如 temperature、max_tokens）",
      "注意：修改 profile 会影响所有引用该 profile 的 preset route",
    ],
  },
  presets: {
    collection: "llm_presets",
    label: "Presets",
    description: "Route → Profile 映射集合：定义各路由（如 reader_ask、reader_translate）使用的 profile，支持 base_preset 继承和 default_profile 回退。",
    fields: [
      { name: "default_profile", desc: "默认 profile（FK）" },
      { name: "routes", desc: "路由 → profile 映射表" },
      { name: "base_preset", desc: "继承的父 preset（FK）" },
    ],
    role: "链路第 4 层：路由映射。Preset 决定哪个路由用哪个 profile，是运行时配置解析的入口。",
    scenarios: [
      "新增 preset：定义 routes 和 default_profile",
      "调整路由映射：修改 routes 中的 profile 引用",
      "继承关系：设置 base_preset 继承共享配置",
      "注意：修改 preset 的 routes 会直接影响对应路由的运行时行为",
    ],
  },
};

const tabKeys = Object.keys(tabs);
const activeTab = ref("providers");

function openCollection(id) {
  router.push(`/content/${id}`);
}
</script>

<template>
  <div class="catalog">
    <!-- Chain description -->
    <div class="chain-flow">
      <span class="chain-step">Provider</span>
      <span class="chain-arrow">→</span>
      <span class="chain-step">Model</span>
      <span class="chain-arrow">→</span>
      <span class="chain-step">Profile</span>
      <span class="chain-arrow">→</span>
      <span class="chain-step">Preset</span>
    </div>

    <!-- Tabs -->
    <div class="catalog-tabs">
      <button
        v-for="key in tabKeys"
        :key="key"
        class="catalog-tab"
        :class="{ 'is-active': activeTab === key }"
        type="button"
        @click="activeTab = key"
      >
        {{ tabs[key].label }}
      </button>
    </div>

    <!-- Tab content -->
    <div v-for="key in tabKeys" :key="key" v-show="activeTab === key" class="tab-content">
      <p class="tab-desc">{{ tabs[key].description }}</p>

      <p class="tab-role"><strong>角色：</strong>{{ tabs[key].role }}</p>

      <dl class="tab-fields">
        <template v-for="field in tabs[key].fields" :key="field.name">
          <dt><code>{{ field.name }}</code></dt>
          <dd>{{ field.desc }}</dd>
        </template>
      </dl>

      <ol class="tab-scenarios">
        <li v-for="(scenario, i) in tabs[key].scenarios" :key="i">{{ scenario }}</li>
      </ol>

      <button class="tab-link" type="button" @click="openCollection(tabs[key].collection)">
        在 Directus 中打开 <span class="tab-link-arrow">arrow_forward</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.catalog {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* --- Chain flow --- */

.chain-flow {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chain-step {
  font-size: 13px;
  font-weight: 700;
  color: var(--theme--foreground);
  letter-spacing: 0.02em;
}

.chain-arrow {
  font-size: 13px;
  color: var(--theme--foreground-subdued);
  font-weight: 400;
  user-select: none;
}

/* --- Tabs --- */

.catalog-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--theme--border-color);
}

.catalog-tab {
  border: 0;
  border-bottom: 3px solid transparent;
  border-radius: 0;
  background: transparent;
  padding: 8px 16px;
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  transition: color 150ms ease, border-color 150ms ease;
}

.catalog-tab:hover {
  color: var(--theme--foreground);
}

.catalog-tab:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: -2px;
}

.catalog-tab.is-active {
  color: var(--theme--primary);
  border-bottom-color: var(--theme--primary);
  font-size: 14px;
}

/* --- Tab content --- */

.tab-content {
  padding: 16px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  animation: tab-fade-in 150ms ease;
}

@keyframes tab-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.tab-desc {
  margin: 0;
  color: var(--theme--foreground);
  font-size: 13px;
  line-height: 1.5;
  max-width: 640px;
}

.tab-role {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  line-height: 1.5;
}

.tab-role strong {
  color: var(--theme--foreground);
  font-weight: 600;
}

/* --- Fields definition list --- */

.tab-fields {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0;
}

.tab-fields dt {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.5;
}

.tab-fields dt code {
  font-family: monospace;
  font-size: 12px;
  color: var(--theme--foreground);
  background: var(--theme--background-normal);
  padding: 2px 4px;
  border-radius: 2px;
}

.tab-fields dd {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.5;
  opacity: 0.85;
}

/* --- Scenarios --- */

.tab-scenarios {
  margin: 0;
  padding-left: 24px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.8;
}

/* --- Link button --- */

.tab-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 8px 0;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  transition: opacity 150ms ease;
}

.tab-link:hover {
  opacity: 0.8;
}

.tab-link:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
  border-radius: 4px;
}

.tab-link-arrow {
  font-family: 'Material Icons', sans-serif;
  font-size: 16px;
  line-height: 1;
}
</style>
