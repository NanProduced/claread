/**
 * Markdown deserialize utility
 *
 * 提供 markdown 字符串 → Plate 节点树（Descendant[]）的转换函数，
 * 供 projection 层的 enhancement block builder 调用。
 *
 * 使用单例 editor 实例避免每次 deserialize 都创建新 editor。
 *
 * 失败处理：
 * - `deserializeMarkdownToBlocksWithStatus` 返回 `{ blocks, status, error? }`，
 *   调用方可据此向用户显示"Markdown 解析失败，已按纯文本处理"提示态，
 *   禁止原始标记静默上屏。
 * - `deserializeMarkdownToBlocks` 保留旧签名（返回 `Descendant[]`），向后兼容，
 *   内部委托给 `deserializeMarkdownToBlocksWithStatus`；失败时同样兜底为
 *   纯文本段落（旧行为不变，不向调用方暴露 status）。
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
 * Deserialize 结果状态。
 *
 * - `success`：markdown 成功解析为 Plate 节点树。
 * - `degraded`：解析抛出异常，blocks 兜底为单段落纯文本（原始 markdown 作为
 *   文本内容），调用方应向用户显示可见的降级提示，禁止原始标记静默上屏。
 * - `empty`：输入为空或仅空白字符，blocks 为单段落空文本节点（非错误态）。
 */
export type DeserializeMarkdownStatus = "success" | "degraded" | "empty";

export interface DeserializeMarkdownResult {
  blocks: Descendant[];
  status: DeserializeMarkdownStatus;
  /** 仅 `status === "degraded"` 时存在，记录异常消息供 UI 诊断展示。 */
  error?: string;
}

/**
 * 块级 markdown → Plate Value（带状态）
 *
 * 用于需要区分"成功解析"与"降级兜底"的调用方（如输入区 MarkdownTextInput），
 * 以便在 UI 显示可见降级提示，避免 `### foo` 等原始标记静默上屏。
 *
 * 空输入返回 `{ status: "empty", blocks: [空段落] }`。
 * 解析失败返回 `{ status: "degraded", blocks: [纯文本段落], error }`，
 * 其中纯文本段落的 text 为原始 markdown 字符串，保留用户可编辑内容。
 */
export function deserializeMarkdownToBlocksWithStatus(
  markdown: string,
): DeserializeMarkdownResult {
  if (!markdown?.trim()) {
    return {
      blocks: [{ type: "p", children: [{ text: "" }] }],
      status: "empty",
    };
  }
  try {
    const editor = getDeserializerEditor();
    const blocks = editor.getApi(MarkdownPlugin).markdown.deserialize(markdown);
    return { blocks, status: "success" };
  } catch (error) {
    return {
      blocks: [{ type: "p", children: [{ text: markdown }] }],
      status: "degraded",
      error: error instanceof Error ? error.message : String(error),
    };
  }
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
 * deserialize 失败时兜底为纯文本段落（向后兼容旧行为，不暴露 status；
 *   需要区分成功/降级的调用方应改用 `deserializeMarkdownToBlocksWithStatus`）。
 */
export function deserializeMarkdownToBlocks(markdown: string): Descendant[] {
  return deserializeMarkdownToBlocksWithStatus(markdown).blocks;
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
