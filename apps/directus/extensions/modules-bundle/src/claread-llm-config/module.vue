<script setup>
import { computed, ref } from "vue";

const tabs = [
  { id: "providers", label: "Providers", collection: "llm_providers", icon: "dns" },
  { id: "models", label: "Models", collection: "llm_models", icon: "memory" },
  { id: "profiles", label: "Profiles", collection: "llm_profiles", icon: "tune" },
  { id: "presets", label: "Presets", collection: "llm_presets", icon: "playlist_add_check" },
  { id: "ask-options", label: "Ask Options", collection: "llm_ask_options", icon: "smart_toy" },
];

const activeTab = ref("providers");
const currentTab = computed(() => tabs.find((t) => t.id === activeTab.value) ?? tabs[0]);
</script>

<template>
  <div class="llm-config-module">
    <header class="llm-config-header">
      <h1 class="llm-config-title">LLM Config</h1>
      <p class="llm-config-subtitle">Provider / Model / Profile / Preset / Ask Option authoring</p>
    </header>

    <nav class="llm-config-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        :class="['llm-config-tab', { active: activeTab === tab.id }]"
        @click="activeTab = tab.id"
      >
        <v-icon :name="tab.icon" small />
        {{ tab.label }}
      </button>
    </nav>

    <div class="llm-config-content">
      <v-collection
        :collection="currentTab.collection"
        :icon="currentTab.icon"
      />
    </div>
  </div>
</template>

<style scoped>
.llm-config-module {
  padding: 20px;
}

.llm-config-header {
  margin-bottom: 16px;
}

.llm-config-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 4px 0;
}

.llm-config-subtitle {
  color: var(--foreground-subdued);
  font-size: 14px;
  margin: 0;
}

.llm-config-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border-subdued);
  padding-bottom: 0;
}

.llm-config-tab {
  padding: 8px 16px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
  color: var(--foreground-subdued);
  border-bottom: 2px solid transparent;
  transition: color 0.2s, border-color 0.2s;
  display: flex;
  align-items: center;
  gap: 6px;
}

.llm-config-tab:hover {
  color: var(--foreground-normal);
}

.llm-config-tab.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 500;
}

.llm-config-content {
  min-height: 400px;
}
</style>
