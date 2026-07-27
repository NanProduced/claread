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
 * C1.1 配置校正（按 platejs.org/docs/markdown 与项目安全子集）：
 * - `allowedNodes` 锁定 Plate↔mdast 转换的安全节点类型，避免未知节点
 *   静默穿透进编辑器（如 raw HTML / mdx / footnote definition 等）。
 *   覆盖范围：heading/list/table/code/blockquote/thematic_break 段级 +
 *   emphasis/strong/strikethrough/inline_code/link 行内 + paragraph/text 基础。
 *   未知节点会被 remark-stringify / plate-mdast deserializer 丢弃并降级。
 *
 *   R1：`allowedNodes` 在 **serialize 方向按 Plate 节点的原始 type 过滤**
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
import { MarkdownPlugin } from "@platejs/markdown";
import remarkGfm from "remark-gfm";

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
 * 而 B3 inline-marks 投影路径使用 `link` leaf（`ReaderMarkdownLinkLeaf`）。
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
  // R1：serialize 方向按原始 node.type 过滤，必须列出具体标题 type。
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  // 段级容器
  "blockquote",
  "list",
  // R1：同上，列表容器原始 type（classic ul/ol → mdast list）。
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
] as const;

export const MarkdownKit = [
  MarkdownPlugin.configure({
    options: {
      remarkPlugins: [remarkGfm],
      allowedNodes: [...ALLOWED_MARKDOWN_NODES],
      remarkStringifyOptions: {
        // 与粘贴入口约定：无序列表用 `-`，避免 `*` 与 emphasis 歧义。
        bullet: "-",
        // GFM 习惯：斜体用 `*`，粗体也用 `*`（remark-stringify 自动重复为 `**`）。
        emphasis: "*",
        strong: "*",
        // 代码围栏用 `` ` ``（remark-stringify 自动重复为 ``` ``` ```）。
        fence: "`",
        // 分隔线用 `-`（remark-stringify 自动重复为 `---`）。
        rule: "-",
        // 有序列表 marker 递增（1. 2. 3.），而非全 1.
        incrementListMarker: true,
        // 链接定义紧凑，减少多余空行。
        tightDefinitions: true,
      },
    },
  }),
];
