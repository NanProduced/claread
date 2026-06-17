<script setup>
import { ref } from "vue";

const copiedId = ref(null);

function copyCommand(cmd) {
  navigator.clipboard.writeText(cmd.command).then(() => {
    copiedId.value = cmd.id;
    setTimeout(() => { copiedId.value = null; }, 1500);
  }).catch(() => {});
}

const commands = [
  {
    id: "sync-metadata",
    phase: "Setup",
    label: "Sync Metadata",
    command: "pnpm directus:llm-config:sync-metadata",
    description: "同步 Directus collection/field 元数据。首次部署或 schema 变更后必须运行。",
  },
  {
    id: "import",
    phase: "Import",
    label: "Import Bundle",
    command: "pnpm directus:llm-config:import-bundle",
    description: "从 services/api/config/ 读取 3 个源 JSON，幂等 upsert 到 Directus。收敛同步：JSON 中省略的字段会显式写 null。",
  },
  {
    id: "validate",
    phase: "Validate",
    label: "Validate Bundle",
    command: "node apps/directus/scripts/validate-llm-config-bundle.mjs",
    description: "校验 LLM 配置 bundle 的完整性和引用链。",
  },
  {
    id: "export",
    phase: "Export",
    label: "Export Bundle",
    command: "pnpm directus:llm-config:export-bundle",
    description: "从 Directus 读取 active 记录，生成 3 个 JSON bundle 文件。",
  },
];

const workflowSteps = [
  {
    phase: "Setup",
    title: "同步元数据",
    description: "首次部署或 schema 变更后运行。",
    command: "pnpm directus:llm-config:sync-metadata",
  },
  {
    phase: "Import",
    title: "导入配置",
    description: "将 JSON 真源导入 Directus。收敛同步：省略的字段会写 null。",
    command: "pnpm directus:llm-config:import-bundle",
  },
  {
    phase: "Edit",
    title: "编辑配置",
    description: "在 Directus UI 中编辑 LLM 配置。",
    command: null,
  },
  {
    phase: "Validate",
    title: "校验配置",
    description: "校验配置 bundle 的完整性和引用链。",
    command: "node apps/directus/scripts/validate-llm-config-bundle.mjs",
  },
  {
    phase: "Export",
    title: "导出配置",
    description: "将 Directus 中的 active 配置导出为 JSON bundle。",
    command: "pnpm directus:llm-config:export-bundle",
  },
  {
    phase: "Publish",
    title: "发布上线",
    description: "将导出的 JSON 文件复制到 services/api/config/。",
    command: null,
  },
];
</script>

<template>
  <div class="validation-mode">
    <!-- Risk note -->
    <div class="risk-note">
      <span class="risk-icon">⚠</span>
      Import 是收敛同步：JSON 中省略的字段会在 Directus 中被显式写 null。Export 会用当前 active 记录覆盖目标目录。
    </div>

    <!-- Commands reference -->
    <section class="commands-section">
      <h3>可用命令</h3>
      <dl class="command-list">
        <div v-for="cmd in commands" :key="cmd.id" class="command-row">
          <dt>
            <span class="command-phase">{{ cmd.phase }}</span>
            <strong>{{ cmd.label }}</strong>
          </dt>
          <dd>
            <span class="command-desc">{{ cmd.description }}</span>
            <span class="command-code-wrap">
              <code class="command-code">{{ cmd.command }}</code>
              <button class="copy-hint" type="button" title="复制命令" @click="copyCommand(cmd)">{{ copiedId === cmd.id ? '✓' : '⧉' }}</button>
            </span>
          </dd>
        </div>
      </dl>
    </section>

    <!-- Workflow -->
    <section class="workflow-section">
      <h3>工作流程</h3>
      <ol class="workflow-list">
        <li v-for="(step, i) in workflowSteps" :key="i" class="workflow-step">
          <span class="step-num">{{ i + 1 }}</span>
          <div class="step-body">
            <span class="step-phase">{{ step.phase }}</span>
            <span class="step-title">{{ step.title }}</span>
            <span class="step-desc">{{ step.description }}</span>
            <code v-if="step.command" class="step-code">{{ step.command }}</code>
          </div>
        </li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.validation-mode {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* --- Risk note --- */

.risk-note {
  padding: 12px 16px;
  background: color-mix(in srgb, var(--theme--warning) 14%, var(--theme--background));
  border: 1px solid color-mix(in srgb, var(--theme--warning) 36%, transparent);
  border-radius: 4px;
  color: var(--theme--foreground);
  font-size: 12px;
  line-height: 1.5;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.risk-icon {
  flex: 0 0 auto;
  font-size: 14px;
  line-height: 1.5;
}

/* --- Section headings --- */

.commands-section h3,
.workflow-section h3 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--theme--primary);
  display: inline-block;
}

.commands-section,
.workflow-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* --- Commands: definition list --- */

.command-list {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.command-row {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 8px 16px;
  align-items: baseline;
}

.command-row dt {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.command-phase {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--theme--foreground-subdued);
}

.command-row dt strong {
  font-size: 13px;
}

.command-row dd {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.command-desc {
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  line-height: 1.5;
}

.command-code-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: color-mix(in srgb, var(--theme--background-subdued) 88%, var(--theme--foreground));
  border-radius: 4px;
  padding: 4px 8px;
}

.command-code {
  font-size: 13px;
  font-family: monospace;
  word-break: break-all;
  color: var(--theme--foreground);
}

.copy-hint {
  flex: 0 0 auto;
  border: 0;
  background: transparent;
  padding: 2px 4px;
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  opacity: 0.4;
  transition: opacity 150ms ease, color 150ms ease;
  user-select: none;
  border-radius: 2px;
}

.copy-hint:hover,
.copy-hint:focus-visible {
  opacity: 1;
  color: var(--theme--primary);
}

.copy-hint:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
}

/* --- Workflow: ordered list --- */

.workflow-list {
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.workflow-step {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.step-num {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--theme--primary);
  color: var(--theme--background);
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  margin-top: 1px;
}

.step-body {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  min-width: 0;
}

.step-phase {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 12%, var(--theme--background));
  border-radius: 8px;
  padding: 1px 8px;
  margin-right: 4px;
}

.step-title {
  font-size: 13px;
  font-weight: 600;
}

.step-desc {
  font-size: 12px;
  color: var(--theme--foreground-subdued);
}

.step-code {
  display: inline-block;
  background: color-mix(in srgb, var(--theme--background-subdued) 88%, var(--theme--foreground));
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 13px;
  font-family: monospace;
  word-break: break-all;
  color: var(--theme--foreground);
  flex-basis: 100%;
}
</style>
