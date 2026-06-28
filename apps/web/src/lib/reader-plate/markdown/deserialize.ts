/**
 * Markdown deserialize utility
 *
 * 提供 markdown 字符串 → Plate 节点树（Descendant[]）的转换函数，
 * 供 projection 层的 enhancement block builder 调用。
 *
 * 使用单例 editor 实例避免每次 deserialize 都创建新 editor。
 * 失败时兜底为纯文本段落，不抛出异常。
 */
import { createPlateEditor } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";
import type { Descendant } from "platejs";

import { MarkdownKit } from "@/components/editor/plugins/markdown-kit";

let _deserializerEditor: ReturnType<typeof createPlateEditor> | null = null;

function getDeserializerEditor() {
  if (!_deserializerEditor) {
    _deserializerEditor = createPlateEditor({
      plugins: [...MarkdownKit],
    });
  }
  return _deserializerEditor;
}

/**
 * 块级 markdown → Plate Value（Descendant[]）
 *
 * 用于 enhancement block children 的 markdown 渲染：
 * - `grammar_note.note` → Plate 节点树
 * - `sentence_analysis.analysis` → Plate 节点树
 * - `ask_supplement.content_md` → Plate 节点树
 *
 * 支持的 markdown 语法：
 * - 加粗 `**text**`、斜体 `*text*`、行内代码 `` `code` ``
 * - 无序列表 `- item`、有序列表 `1. item`
 * - 引用块 `> quote`、代码块 ``` ``` ```
 * - 标题 `# H1` ~ `###### H6`
 * - GFM：表格、删除线 `~~text~~`、任务列表 `- [ ] item`
 *
 * 空字符串或仅空白字符返回单段落空文本节点。
 * deserialize 失败时兜底为纯文本段落。
 */
export function deserializeMarkdownToBlocks(markdown: string): Descendant[] {
  if (!markdown?.trim()) {
    return [{ type: "p", children: [{ text: "" }] }];
  }
  try {
    const editor = getDeserializerEditor();
    return editor.getApi(MarkdownPlugin).markdown.deserialize(markdown);
  } catch {
    return [{ type: "p", children: [{ text: markdown }] }];
  }
}

/**
 * 行内 markdown → Slate children（Descendant[]）
 *
 * 用于不需要块级结构的行内 markdown 渲染（如 callout 内单行说明）。
 * 空字符串返回空文本节点。
 */
export function deserializeMarkdownInline(markdown: string): Descendant[] {
  if (!markdown?.trim()) {
    return [{ text: "" }];
  }
  try {
    const editor = getDeserializerEditor();
    return editor.api.markdown.deserializeInline(markdown);
  } catch {
    return [{ text: markdown }];
  }
}

// re-export for convenience
export type { Descendant };
