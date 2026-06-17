<script setup>
import { useRouter } from "vue-router";

const router = useRouter();

const collections = [
  { id: "llm_providers", label: "llm_providers", description: "供应商连接配置" },
  { id: "llm_models", label: "llm_models", description: "远端模型定义" },
  { id: "llm_profiles", label: "llm_profiles", description: "场景级配置" },
  { id: "llm_presets", label: "llm_presets", description: "Route → Profile 映射" },
  { id: "llm_ask_options", label: "llm_ask_options", description: "Ask 用户可选档位" },
  { id: "llm_ask_config", label: "llm_ask_config", description: "Ask 顶层配置（单例）" },
];

function openCollection(id) {
  router.push(`/content/${id}`);
}
</script>

<template>
  <div class="advanced-mode">
    <div class="callout callout-warning">
      此区域提供 6 个原始 Directus collection 的直接访问入口。通常应通过 Overview / Catalog / Ask Claread 等工作台操作，仅在需要直接编辑原始数据时使用。
    </div>

    <div class="collection-list">
      <button
        v-for="col in collections"
        :key="col.id"
        class="collection-row"
        type="button"
        @click="openCollection(col.id)"
      >
        <code class="collection-name">{{ col.label }}</code>
        <span class="collection-desc">{{ col.description }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.advanced-mode {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.callout {
  padding: 10px 14px;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.5;
}

.callout-warning {
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 8%, var(--theme--background));
  color: var(--theme--foreground);
}

.collection-list {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  overflow: hidden;
}

.collection-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  border: 0;
  background: transparent;
  padding: 8px 14px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  text-align: left;
  border-bottom: 1px solid var(--theme--border-color);
  transition: background-color 160ms ease;
}

.collection-row:last-child {
  border-bottom: 0;
}

.collection-row:hover {
  background: var(--theme--background-subdued);
}

.collection-name {
  font-size: 12px;
  white-space: nowrap;
}

.collection-desc {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
</style>
