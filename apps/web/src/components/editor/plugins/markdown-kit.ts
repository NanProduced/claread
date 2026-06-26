/**
 * Markdown Kit — 配置 MarkdownPlugin + remarkGfm
 *
 * 用于 Reader Plate surface 的 markdown deserialize：
 * - `editor.getApi(MarkdownPlugin).markdown.deserialize(markdown)` → `Descendant[]`
 * - `editor.api.markdown.deserializeInline(markdown)` → 行内节点
 *
 * remarkGfm 提供 GFM 扩展：表格、删除线、任务列表、脚注。
 * 不加 remarkMath（阅读场景不需要 LaTeX）。
 * 不加 remarkMdx（callout 内不需要自定义元素）。
 *
 * 注：basic-blocks-kit / basic-marks-kit 不需要单独创建。
 * 测试验证 MarkdownPlugin 单独使用即可正确 deserialize 列表、引用、标题、
 * 加粗、斜体、代码等 markdown 语法为标准 Plate 节点树。
 * 渲染层（PlateStatic）使用自定义 component map，不依赖 @platejs/basic-* 包。
 */
import { MarkdownPlugin } from "@platejs/markdown";
import remarkGfm from "remark-gfm";

export const MarkdownKit = [
  MarkdownPlugin.configure({
    options: {
      remarkPlugins: [remarkGfm],
    },
  }),
];
