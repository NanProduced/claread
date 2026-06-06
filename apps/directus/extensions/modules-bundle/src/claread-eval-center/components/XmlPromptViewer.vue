<script setup>
import { computed } from "vue";

defineOptions({ name: "XmlPromptViewer" });

const props = defineProps({
  text: {
    type: String,
    default: "",
  },
});

const parsedBlocks = computed(() => {
  const content = String(props.text || "");
  if (!content) return [];

  // Match <tag>content</tag>
  const regex = /<([a-zA-Z0-9_-]+)>([\s\S]*?)<\/\1>/g;
  const blocks = [];
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      const plainText = content.substring(lastIndex, match.index).trim();
      if (plainText) {
        blocks.push({ type: "text", content: plainText });
      }
    }
    blocks.push({ type: "tag", tag: match[1], content: match[2].trim() });
    lastIndex = match.index + match[0].length;
  }

  const remaining = content.substring(lastIndex).trim();
  if (remaining) {
    blocks.push({ type: "text", content: remaining });
  }

  if (blocks.length === 0) {
    blocks.push({ type: "text", content: content.trim() });
  }

  return blocks;
});
</script>

<template>
  <div class="xml-prompt-viewer">
    <template v-for="(block, index) in parsedBlocks" :key="index">
      <div v-if="block.type === 'tag'" class="xml-block">
        <div class="xml-tag-header">
          <span class="xml-tag-bracket">&lt;</span><span class="xml-tag-name">{{ block.tag }}</span><span class="xml-tag-bracket">&gt;</span>
        </div>
        <div class="xml-tag-content">
          <pre class="xml-pre">{{ block.content }}</pre>
        </div>
      </div>
      <div v-else class="xml-text-block">
        <pre class="xml-pre">{{ block.content }}</pre>
      </div>
    </template>
  </div>
</template>

<style scoped>
.xml-prompt-viewer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-family: var(--theme--fonts--sans, system-ui, -apple-system, sans-serif);
}

.xml-block {
  border: 1px solid var(--theme--border-color, #e0e0e0);
  border-radius: 6px;
  background: var(--theme--background, #fff);
  overflow: hidden;
}

.xml-tag-header {
  background: var(--theme--background-page, #f8f9fa);
  padding: 6px 12px;
  border-bottom: 1px solid var(--theme--border-color, #e0e0e0);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 13px;
  font-weight: 600;
  color: var(--theme--primary, #206bc4);
}

.xml-tag-bracket {
  color: var(--theme--foreground-subdued, #888);
  opacity: 0.6;
}

.xml-tag-name {
  color: var(--theme--primary, #206bc4);
}

.xml-tag-content {
  padding: 12px;
  background: var(--theme--background, #fff);
}

.xml-text-block {
  padding: 12px;
  background: var(--theme--background-page, #f8f9fa);
  border-radius: 6px;
  border: 1px dashed var(--theme--border-color-subdued, #d0d0d0);
}

.xml-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground, #333);
}
</style>
