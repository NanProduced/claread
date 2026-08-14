/**
 * Markdown Kit — 配置 MarkdownPlugin + remarkGfm
 *
 * 用于 Reader Plate surface 的 markdown deserialize / serialize：
 * - `editor.getApi(MarkdownPlugin).markdown.deserialize(markdown)` → `Descendant[]`
 * - `editor.api.markdown.deserializeInline(markdown)` → 行内节点
 * - `editor.getApi(MarkdownPlugin).markdown.serialize()` → Markdown 字符串
 *
 * remarkGfm 提供 GFM 扩展：表格、删除线、任务列表、脚注。
 * 不加 remarkMath（阅读场景不需要 LaTeX）。
 * 不加 remarkMdx（callout 内不需要自定义元素）。
 *
 * 配置校正（按 platejs.org/docs/markdown 与项目安全子集）：
 * - `allowedNodes` 锁定 Plate↔mdast 转换的安全节点类型，避免未知节点
 *   静默穿透进编辑器（如 raw HTML / mdx / footnote definition 等）。
 *   覆盖范围：heading/list/table/code/blockquote/thematic_break 段级 +
 *   emphasis/strong/strikethrough/inline_code/link 行内 + paragraph/text 基础。
 *   未知节点会被 remark-stringify / plate-mdast deserializer 丢弃并降级。
 *
 *   `allowedNodes` 在 **serialize 方向按 Plate 节点的原始 type 过滤**
 *   （`@platejs/markdown` 的 shouldIncludeNode 检查 `node.type`），因此
 *   除了泛型键（`heading` / `list`，服务 deserialize 方向的 mdast 类型名），
 *   还必须列出编辑器实际使用的具体节点 type（`h1`–`h6` / `ul` / `ol`），
 *   否则标题与列表在 serialize 往返中被静默丢弃（已验证：编辑后提交载荷
 *   失去全部 heading）。这不是扩展支持矩阵，而是让 serialize 与已声明的
 *   安全子集一致。
 * - `remarkStringifyOptions` 显式锁定序列化风格（值为单字符，remark-stringify
 *   自动重复为完整 marker）：
 *   - `bullet: "-"` 与项目粘贴入口的 `- item` 风格一致；
 *   - `emphasis: "*"`、`strong: "*"` 与 GFM 习惯一致（输出 `*italic*` / `**bold**`）；
 *   - `fence: "`"`、`rule: "-"`、`incrementListMarker: true` 保证
 *     有序列表 `1. 2. 3.` 而非 `1. 1. 1.`；
 *   - `tightDefinitions: true` 让链接定义紧凑（减少空行）。
 *   这套配置锁定 serialize→deserialize 的 round-trip parity，避免默认值漂移。
 *
 * 注：basic-blocks-kit / basic-marks-kit 不需要单独创建。
 * 测试验证 MarkdownPlugin 单独使用即可正确 deserialize 列表、引用、标题、
 * 加粗、斜体、代码等 markdown 语法为标准 Plate 节点树。
 * 渲染层在 reader-blocks-kit 中为这些标准节点注册本地薄 Plate plugins，
 * 不走手动 React DOM renderer。
 */
import {
  MarkdownPlugin,
  type DeserializeMdOptions,
  type MdBlockquote,
  type MdDecoration,
  type MdHtml,
  type MdRootContent,
  type SerializeMdOptions,
} from "@platejs/markdown";
import {
  convertChildrenDeserialize,
  markdownToAstProcessor,
} from "@platejs/markdown";
import remarkGfm from "remark-gfm";
import type { Descendant } from "platejs";
import {
  buildCanonicalAsideMarkdown,
  extractGfmAlertMarker,
  matchAsideBlock,
  remarkMergeAsideHtml,
  stripGfmAlertMarker,
} from "@/lib/source-callout/source-callout-adapter";
import {
  extractCalloutDisplayIcon,
  normalizeCalloutDisplayIcons,
} from "@/lib/source-callout/source-callout-display-icon";

/**
 * 允许在 Plate↔mdast 之间转换的节点类型（安全子集）。
 *
 * 命名沿用 Plate 的 PlateType 字面量（不是 mdast 类型名）：
 *   - 段级：p / heading / blockquote / list / li / lic / code_block / code_line /
 *           hr / table / tr / td / th
 *   - 行内：a / bold / italic / code / strikethrough / text
 *
 * 注意：`a` 是 Plate element 类型（mdast `link` → Plate `{type:"a", url, children}`），
 * 由 `reader-blocks-kit.tsx` 的 `ReaderMarkdownLinkElement` 渲染；
 * 而 inline-marks 投影路径使用 `link` leaf（`ReaderMarkdownLinkLeaf`）。
 * 两者并存且不冲突：不同的 node kind（element vs leaf）+ 不同的 plugin key。
 *
 * 任何不在此列表的节点（callout / toggle / mention / equation / image /
 * footnote_definition / mdx / html 等）在 deserialize 时被丢弃，serialize 时
 * 被忽略，由调用方按需自行处理（输入区 / projection 层均不会产生这些节点）。
 */
const ALLOWED_MARKDOWN_NODES = [
  // 段级基础
  "p",
  "text",
  "heading",
  // serialize 方向按原始 node.type 过滤，必须列出具体标题 type。
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  // 段级容器
  "blockquote",
  "list",
  // 同上，列表容器原始 type（classic ul/ol → mdast list）。
  "ul",
  "ol",
  "li",
  "lic",
  // 代码与分隔
  "code_block",
  "code_line",
  "hr",
  // 表格（GFM）
  "table",
  "tr",
  "td",
  "th",
  // 行内 marks
  "a",
  "bold",
  "italic",
  "code",
  "strikethrough",
  // source_callout（Notion 风格 aside 提示框）
  "source_callout",
  // html（用于 source_callout 序列化为 `<aside>` raw HTML）
  "html",
] as const;

/**
 * MarkdownPlugin 的完整 options（输入端可在此基础上扩展 remarkPlugins，
 * 例如 MarkdownTextInput 追加 remarkPreserveUnsupported）。
 *
 * 注：不要通过 `MarkdownKit[0].configure({ options: ...MarkdownKit[0].options })`
 * 复制配置——Plate configure 的 options 解析会丢 remarkStringifyOptions
 * 等字段（已实测）。统一从本对象展开。
 */
/**
 * 自定义 mdast↔Plate 转换规则。
 *
 * 三条归一路径都通过这些 rules 在 deserializer 层完成：
 * 1. 纯 Markdown `<aside>...</aside>` → `html` rule 识别 → `source_callout` Plate element
 * 2. GFM alert `> [!NOTE]` → `blockquote` rule 识别 marker → `source_callout` Plate element
 * 3. 剪贴板 HTML `<aside>` → Plate HTML deserializer (source-callout-kit 插件)
 *
 * 非 aside 的 html 节点降级为 text（与原 remarkPreserveUnsupported 行为一致）。
 * 非 GFM alert 的 blockquote 保持默认 blockquote 行为。
 *
 * R-Aside-1R B: kind 统一为 "note"。canonical `<aside>` 不携带 class 属性，
 * kind 无法安全持久化到 Stable Document / Reader reload。所有输入路径
 * （HTML aside / GFM alert / 剪贴板 HTML）产出的 source_callout element
 * 均设置 `kind: "note"`，不再从 class 或 GFM marker 推断视觉差异。
 * classifyCalloutKind / classifyCalloutKindFromGfmMarker 函数保留用于
 * 未来扩展，但当前不驱动视觉差异化。
 *
 * R-Aside-1R C: source_callout serializer 使用 `serializeMd(editor, {value})`
 * 序列化内部 children，不修改 live editor.children / selection / history。
 */
type SourceCalloutHtmlNode = MdHtml & {
  _asideChildren?: MdRootContent[];
};

type SourceCalloutElement = {
  type: "source_callout";
  kind: "note";
  displayIcon?: string | null;
  children: Descendant[];
};

const SOURCE_CALLOUT_RULES = {
  html: {
    deserialize: (
      mdastNode: SourceCalloutHtmlNode,
      deco: MdDecoration,
      options: DeserializeMdOptions,
    ) => {
      const value = mdastNode.value ?? "";
      const asideMatch = matchAsideBlock(value);
      if (asideMatch) {
        const editor = options.editor;
        if (!editor) {
          return { text: value };
        }
        let children: Descendant[];
        if (mdastNode._asideChildren && mdastNode._asideChildren.length > 0) {
          // 使用 remarkMergeAsideHtml 携带的原始 mdast 子节点，
          // 保留所有 inline marks / 嵌套结构 / 代码块，不进行损失性 round-trip。
          // opening html 节点 value 中的内部文本仍需通过 markdownToAstProcessor
          // 重新解析（因为 commonmark 将 html_block 内文本视为 raw text，不解析
          // markdown marks）。middle nodes 已是 mdast 节点，直接转换。
          const innerAst = markdownToAstProcessor(
            editor,
            asideMatch.innerContent,
            options,
          );
          const innerChildren = convertChildrenDeserialize(
            innerAst.children,
            deco,
            options,
          );
          const middleChildren = convertChildrenDeserialize(
            mdastNode._asideChildren,
            deco,
            options,
          );
          children = [...innerChildren, ...middleChildren];
        } else {
          // 单个 html 节点（无合并）— 重新解析内部内容
          const innerAst = markdownToAstProcessor(
            editor,
            asideMatch.innerContent,
            options,
          );
          children = convertChildrenDeserialize(innerAst.children, deco, options);
        }
        const normalizedChildren = normalizeCalloutDisplayIcons(children);
        const extracted = extractCalloutDisplayIcon(normalizedChildren);
        return {
          type: "source_callout" as const,
          // R-Aside-1R B: 统一为 note，不从 class 推断 kind
          kind: "note" as const,
          ...(extracted.displayIcon
            ? { displayIcon: extracted.displayIcon }
            : {}),
          children: extracted.children,
        };
      }
      // 非 aside html：降级为字面文本（与原 remarkPreserveUnsupported 行为一致）
      return { text: value };
    },
  },
  blockquote: {
    deserialize: (
      mdastNode: MdBlockquote,
      deco: MdDecoration,
      options: DeserializeMdOptions,
    ) => {
      // 检查是否为 GFM alert blockquote（`> [!NOTE]` 等）
      // remark-parse 可能把 marker 行和后续内容合并到同一个 text 节点
      // （如 `[!NOTE]\nContent`），因此用 extractGfmAlertMarker 而非
      // GFM_ALERT_MARKER_RE 进行宽松前缀匹配。
      const firstChild = mdastNode.children?.[0];
      if (firstChild?.type === "paragraph") {
        const firstText = firstChild.children?.[0];
        if (firstText?.type === "text") {
          const marker = extractGfmAlertMarker(firstText.value);
          if (marker) {
            // 移除 marker 文本（含尾部空白/换行）
            const stripped = stripGfmAlertMarker(firstText.value);
            if (stripped) {
              // marker 后还有内容，保留为首段文本
              firstText.value = stripped;
            } else if (firstChild.children.length > 1) {
              // 首段只剩空文本节点且有其他节点，移除空文本节点
              firstChild.children.shift();
            } else {
              // 整个首段只有 marker，移除首段
              mdastNode.children.shift();
            }
            const children = convertChildrenDeserialize(mdastNode.children, deco, options);
            const normalizedChildren = normalizeCalloutDisplayIcons(children);
            const extracted = extractCalloutDisplayIcon(normalizedChildren);
            return {
              type: "source_callout" as const,
              // R-Aside-1R B: 统一为 note，不从 GFM marker 推断 kind
              kind: "note" as const,
              ...(extracted.displayIcon
                ? { displayIcon: extracted.displayIcon }
                : {}),
              children: extracted.children,
            };
          }
        }
      }
      // 默认 blockquote 行为
      return {
        type: "blockquote" as const,
        children: convertChildrenDeserialize(mdastNode.children ?? [], deco, options),
      };
    },
  },
  source_callout: {
    serialize: (
      slateNode: SourceCalloutElement,
      options: SerializeMdOptions,
    ) => {
      // R-Aside-1R C: 使用 serializeMd(editor, {value}) 序列化内部 children，
      // 不修改 live editor.children / selection / history。
      // 旧实现临时替换 editor.children 导致 selection/onChange 副作用。
      const editor = options.editor;
      if (!editor) {
        throw new Error("Markdown source_callout serialization requires an editor");
      }
        const innerMd = editor
        .getApi(MarkdownPlugin)
        .markdown.serialize({
          value: slateNode.children,
        });
      return {
        type: "html" as const,
        value: buildCanonicalAsideMarkdown(
          innerMd,
          undefined,
          slateNode.displayIcon,
        ),
      };
    },
  },
};

export const MARKDOWN_PLUGIN_OPTIONS = {
  // remarkMergeAsideHtml 必须在 remarkGfm 之后，确保 GFM 扩展节点已生成。
  // 它合并被 commonmark 空行拆分的 <aside> html 节点，使 html 规则能
  // 匹配完整的 <aside>...</aside>。
  // 注：传 plugin 工厂（不带括号），由 unified 负责调用。
  remarkPlugins: [remarkGfm, remarkMergeAsideHtml],
  allowedNodes: [...ALLOWED_MARKDOWN_NODES],
  rules: SOURCE_CALLOUT_RULES,
  remarkStringifyOptions: {
    // 与粘贴入口约定：无序列表用 `-`，避免 `*` 与 emphasis 歧义。
    bullet: "-" as const,
    // GFM 习惯：斜体用 `*`，粗体也用 `*`（remark-stringify 自动重复为 `**`）。
    emphasis: "*" as const,
    strong: "*" as const,
    // 代码围栏用 `` ` ``（remark-stringify 自动重复为 ``` ``` ```）。
    fence: "`" as const,
    // 分隔线用 `-`（remark-stringify 自动重复为 `---`）。
    rule: "-" as const,
    // 有序列表 marker 递增（1. 2. 3.），而非全 1.
    incrementListMarker: true,
    // 链接定义紧凑，减少多余空行。
    tightDefinitions: true,
  },
};

export const MarkdownKit = [
  MarkdownPlugin.configure({
    options: MARKDOWN_PLUGIN_OPTIONS,
  }),
];
