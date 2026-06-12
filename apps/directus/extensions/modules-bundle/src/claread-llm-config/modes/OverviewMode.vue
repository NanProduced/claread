<script setup>
import { ref, computed } from "vue";
import { useRouter } from "vue-router";
import { useApi } from "@directus/extensions-sdk";

const emit = defineEmits(["switch-mode"]);
const router = useRouter();
const api = useApi();

const collections = [
  { id: "llm_providers", label: "Providers", icon: "dns", filter: { status: { _eq: "active" } } },
  { id: "llm_models", label: "Models", icon: "memory", filter: { status: { _eq: "active" } } },
  { id: "llm_profiles", label: "Profiles", icon: "tune", filter: { status: { _eq: "active" } } },
  { id: "llm_presets", label: "Presets", icon: "playlist_add_check", filter: { status: { _eq: "active" } } },
  { id: "llm_ask_options", label: "Ask Options", icon: "smart_toy", filter: { enabled: { _eq: true } } },
  { id: "llm_ask_config", label: "Ask Config", icon: "settings", filter: null },
];

const stats = ref({});
const askConfig = ref(null);

const warnings = computed(() => {
  const list = [];
  const emptyCollections = collections.filter(
    (col) => col.id !== "llm_ask_config" && (stats.value[col.id] ?? 0) === 0,
  );
  if (emptyCollections.length > 0) {
    const names = emptyCollections.map((c) => c.label).join("、");
    list.push({ type: "warning", text: `${names} 无活跃记录` });
  }
  if (!askConfig.value) {
    list.push({ type: "danger", text: "Ask Config 缺失，Ask 功能无法运行" });
  } else if (!askConfig.value.default_option) {
    list.push({ type: "warning", text: "Ask Config 未设置 default_option" });
  }
  return list;
});

async function loadStats() {
  try {
    const results = await Promise.all(
      collections.map((col) => {
        const params = { limit: 0, meta: "total_count" };
        if (col.filter) params["filter"] = JSON.stringify(col.filter);
        return api
          .get(`/items/${col.id}`, { params })
          .then((r) => ({ id: col.id, total: r.data.meta?.total_count ?? 0 }))
          .catch(() => ({ id: col.id, total: 0 }));
      }),
    );
    const map = {};
    for (const r of results) map[r.id] = r.total;
    stats.value = map;
  } catch {
    // ignore
  }

  try {
    const resp = await api.get("/items/llm_ask_config", { params: { limit: 1 } });
    askConfig.value = resp.data.data?.[0] ?? null;
  } catch {
    askConfig.value = null;
  }
}

loadStats();

function openCollection(id) {
  router.push(`/content/${id}`);
}
</script>

<template>
  <div class="overview">
    <!-- Risk indicators -->
    <div v-if="warnings.length" class="warnings">
      <div
        v-for="(w, i) in warnings"
        :key="i"
        class="warning-banner"
        :class="'warning-banner--' + w.type"
      >
        <v-icon :name="w.type === 'danger' ? 'error' : 'warning'" small class="warning-icon" />
        <span>{{ w.text }}</span>
      </div>
    </div>

    <!-- System status -->
    <section class="status-section">
      <h3 class="section-heading">系统状态</h3>
      <ul class="status-list">
        <li
          v-for="col in collections"
          :key="col.id"
          class="status-row"
          tabindex="0"
          @click="openCollection(col.id)"
          @keydown.enter="openCollection(col.id)"
        >
          <v-icon :name="col.icon" small class="status-icon" />
          <span class="status-label">{{ col.label }}</span>
          <span
            class="status-count"
            :class="{ 'status-count--zero': (stats[col.id] ?? 0) === 0 }"
          >
            {{ stats[col.id] ?? 0 }}
          </span>
        </li>
      </ul>
    </section>

    <!-- Quick actions -->
    <section class="actions-section">
      <h3 class="section-heading">快捷操作</h3>
      <div class="actions-row">
        <button type="button" class="action-link" @click="emit('switch-mode', 'ask')">
          管理 Ask 配置
        </button>
        <button type="button" class="action-link" @click="emit('switch-mode', 'catalog')">
          浏览 Catalog
        </button>
        <button type="button" class="action-link" @click="emit('switch-mode', 'validation')">
          校验与发布
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.overview {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* Warnings */
.warnings {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.warning-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.4;
}

.warning-icon {
  flex: 0 0 auto;
}

.warning-banner--warning .warning-icon {
  color: var(--theme--warning);
}

.warning-banner--danger .warning-icon {
  color: var(--theme--danger);
}

.warning-banner--warning {
  background: color-mix(in srgb, var(--theme--warning) 12%, transparent);
  color: var(--theme--foreground);
  border: 1px solid color-mix(in srgb, var(--theme--warning) 30%, transparent);
}

.warning-banner--danger {
  background: color-mix(in srgb, var(--theme--danger) 12%, transparent);
  color: var(--theme--foreground);
  border: 1px solid color-mix(in srgb, var(--theme--danger) 30%, transparent);
}

/* Section heading */
.section-heading {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 700;
  color: var(--theme--foreground);
  text-transform: none;
  letter-spacing: normal;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--theme--primary);
}

/* System status */
.status-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  overflow: hidden;
}

.status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--theme--border-color);
  transition: background-color 120ms ease;
}

.status-row:last-child {
  border-bottom: none;
}

.status-row:hover {
  background: var(--theme--background-subdued);
}

.status-row:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: -2px;
  border-radius: 2px;
}

.status-icon {
  color: var(--theme--foreground-subdued);
  flex: 0 0 auto;
}

.status-label {
  flex: 1 1 auto;
  font-size: 14px;
}

.status-count {
  flex: 0 0 auto;
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.status-count--zero {
  color: var(--theme--foreground-subdued);
  font-weight: 700;
}

/* Quick actions */
.actions-row {
  display: flex;
  gap: 8px;
}

.action-link {
  background: none;
  border: 1px solid var(--theme--border-color);
  padding: 8px 16px;
  font: inherit;
  font-size: 14px;
  color: var(--theme--primary);
  cursor: pointer;
  text-decoration: none;
  border-radius: 4px;
  transition: background-color 120ms ease, border-color 120ms ease;
}

.action-link:hover {
  background: color-mix(in srgb, var(--theme--primary) 8%, transparent);
  border-color: color-mix(in srgb, var(--theme--primary) 40%, transparent);
}

.action-link:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
}
</style>
