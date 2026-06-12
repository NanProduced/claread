<script setup>
import { computed, ref } from "vue";
import OverviewMode from "./modes/OverviewMode.vue";
import CatalogMode from "./modes/CatalogMode.vue";
import AskClareadMode from "./modes/AskClareadMode.vue";
import ValidationMode from "./modes/ValidationMode.vue";
import AdvancedMode from "./modes/AdvancedMode.vue";

const modes = [
  {
    id: "overview",
    label: "Overview",
    kicker: "总览",
    description: "6 个 collection 的统计、Ask 配置概览和快捷入口。",
  },
  {
    id: "catalog",
    label: "Catalog",
    kicker: "配置目录",
    description: "浏览 Providers / Models / Profiles / Presets 的说明与入口。",
  },
  {
    id: "ask",
    label: "Ask Claread",
    kicker: "Ask 配置",
    description: "管理 Ask 选项和顶层配置（default_option / billing / runtime）。",
  },
  {
    id: "validation",
    label: "Validation & Publish",
    kicker: "校验发布",
    description: "校验、导入、导出 LLM 配置 bundle。",
  },
  {
    id: "advanced",
    label: "Advanced",
    kicker: "高级",
    description: "6 个原始 Directus collection 的直接访问入口。",
  },
];

const activeMode = ref("overview");
const currentMode = computed(() => modes.find((m) => m.id === activeMode.value) ?? modes[0]);

function switchMode(modeId) {
  if (modes.some((m) => m.id === modeId)) {
    activeMode.value = modeId;
  }
}
</script>

<template>
  <private-view title="LLM Config">
    <template #headline>
      Claread Console
    </template>

    <template #navigation>
      <nav class="llm-nav" aria-label="LLM Config modes">
        <div class="llm-nav-label">配置工作台</div>
        <button
          v-for="mode in modes"
          :key="mode.id"
          class="llm-nav-item"
          :class="{ 'is-active': activeMode === mode.id }"
          type="button"
          @click="activeMode = mode.id"
        >
          <span>
            <strong>{{ mode.label }}</strong>
            <small>{{ mode.kicker }}</small>
          </span>
        </button>
      </nav>
    </template>

    <main class="llm-config">
      <h1 class="llm-title">{{ currentMode.label }}</h1>

      <OverviewMode v-if="activeMode === 'overview'" @switch-mode="switchMode" />
      <CatalogMode v-else-if="activeMode === 'catalog'" />
      <AskClareadMode v-else-if="activeMode === 'ask'" />
      <ValidationMode v-else-if="activeMode === 'validation'" />
      <AdvancedMode v-else-if="activeMode === 'advanced'" />
    </main>
  </private-view>
</template>

<style scoped>
.llm-nav {
  padding: 16px 12px;
}

.llm-nav-label {
  margin: 0 0 8px 8px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.llm-nav-item {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  border: 0;
  border-radius: 4px;
  background: transparent;
  padding: 8px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: background-color 160ms ease, color 160ms ease;
}

.llm-nav-item:hover,
.llm-nav-item.is-active {
  background: var(--theme--background-subdued);
}

.llm-nav-item strong,
.llm-nav-item small {
  display: block;
}

.llm-nav-item small {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.llm-config {
  padding: 32px 24px;
}

.llm-title {
  margin: 0 0 24px;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.25;
}

@media (max-width: 720px) {
  .llm-config {
    padding: 16px;
  }
}
</style>
