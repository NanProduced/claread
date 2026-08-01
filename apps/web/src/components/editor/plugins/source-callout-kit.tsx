/**
 * Source Callout Kit — 输入编辑器与 Content Check 编辑器的 source_callout Plate 插件。
 *
 * 注册 `source_callout` element type，提供：
 * - Notion 风格可编辑 callout component（淡色表面、图标、非斜体正文）
 * - HTML deserializer：识别剪贴板 `<aside>` DOM 元素 → `source_callout` Plate element
 *
 * 与 reader-blocks-kit 中的 `ReaderStableSourceCalloutPlugin` 共享同一 element type
 * (`source_callout`)，但组件不同：输入端需要可编辑，reader 端需要导航属性。
 *
 * 安全：HTML deserializer 仅匹配 `<aside>` 元素，由 prepareClipboardHtml 清洗后
 * 进入。危险属性（onclick/style/script 等）在 clipboard 清洗阶段已移除。
 *
 * R-Aside-1R B: kind 统一为 "note"。canonical `<aside>` 不携带 class 属性，
 * kind 无法安全持久化到 Stable Document / Reader reload。所有输入路径
 * （HTML aside / GFM alert / 剪贴板 HTML）产出的 source_callout element
 * 均设置 `kind: "note"`，不再从 class 推断视觉差异。classifyCalloutKind
 * 函数保留在 adapter 中用于未来扩展，但当前不驱动视觉差异化。
 */
import { createPlatePlugin, type PlateElementProps } from "platejs/react";
import type { Descendant } from "platejs";
import {
  CALLOUT_CSS_CLASSES,
  CALLOUT_ICONS,
  CALLOUT_ICON_COLORS,
  DEFAULT_CALLOUT_KIND,
} from "@/lib/source-callout/source-callout-adapter";
import { isSafeCalloutEmoji } from "@/lib/source-callout/source-callout-display-icon";

/** source_callout Plate element 类型。 */
export interface SourceCalloutElement {
  type: "source_callout";
  id?: string;
  kind?: typeof DEFAULT_CALLOUT_KIND;
  displayIcon?: string | null;
  children: Descendant[];
}

/**
 * 输入编辑器 source_callout 组件 — Notion 风格可编辑 callout。
 *
 * R-Aside-1R B: 统一使用 note 视觉（amber 配色 + 💡 图标）。不再从 element.kind
 * 推断视觉差异，因为 kind 无法安全持久化到 Stable Document / Reader reload。
 * element.kind 字段保留用于未来扩展，但当前所有路径均产出 "note"。
 *
 * - 淡色表面（note 配色）
 * - 明确边界
 * - 图标（💡）
 * - 正文不强制斜体
 * - 使用 `<aside role="note">` 语义结构
 */
function SourceCalloutComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  // R-Aside-1R B: 统一为 note，忽略 element.kind（所有路径均产出 "note"）。
  const kind = DEFAULT_CALLOUT_KIND;
  const displayIcon = (element as SourceCalloutElement).displayIcon;
  const icon =
    displayIcon && isSafeCalloutEmoji(displayIcon)
      ? displayIcon
      : CALLOUT_ICONS[kind];
  const cssClass = CALLOUT_CSS_CLASSES[kind];
  const iconColor = CALLOUT_ICON_COLORS[kind];

  return (
    <aside
      {...attributes}
      role="note"
      className={`source-callout flex gap-3 rounded-lg border px-4 py-3 not-italic ${cssClass}`}
    >
      <span
        aria-hidden="true"
        className={`select-none text-base leading-relaxed ${iconColor}`}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </aside>
  );
}

/**
 * Source Callout Plate 插件。
 *
 * HTML deserializer 匹配 `<aside>` DOM 元素，将其转为 `source_callout` Plate element。
 * R-Aside-1R B: 不再从 class 属性推断 callout kind，统一设置 `kind: "note"`。
 * 其余属性已在 prepareClipboardHtml 中清除。
 *
 * 注意：此 deserializer 仅在 Plate HTML 反序列化路径（剪贴板 text/html 粘贴）中触发。
 * 纯 Markdown 路径由 MARKDOWN_PLUGIN_OPTIONS.rules.html 处理。
 */
export const SourceCalloutPlugin = createPlatePlugin({
  key: "source_callout",
  node: {
    isElement: true,
    component: SourceCalloutComponent,
  },
  parsers: {
    html: {
      deserializer: {
        rules: [{ validNodeName: "ASIDE" }],
        parse: ({ type }: { element: HTMLElement; type: string }) => ({
          type,
          // R-Aside-1R B: 统一为 note，不从 class 推断 kind。
          // class 属性由 prepareClipboardHtml 保留但此处不再消费。
          kind: DEFAULT_CALLOUT_KIND,
        }),
      },
    },
  },
});
