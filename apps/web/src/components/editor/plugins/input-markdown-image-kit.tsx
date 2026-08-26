/**
 * Input Markdown Image Kit — 输入端 Markdown 图片 typed 表示与安全预览（G1′-A）。
 *
 * 输入端 = MarkdownTextInput（主输入框 + Content Check 共用编辑器）。
 * 非输入端（Reader projection / 默认 deserialize）不 import 本文件，
 * MARKDOWN_PLUGIN_OPTIONS 的行为不变（默认路径不产生 img 节点）。
 *
 * 单一 Plate 图片表示（冻结合同 §7 / G1′）：
 * - 只有一个 image element 类型 `img`（inline void）；
 * - url / alt（caption）/ title / children 完整保留，URL、alt、title
 *   永不进入普通 text leaf（word count / 正文分析不受污染）；
 * - 不安装 @platejs/media，复用 @platejs/markdown 既有 img rule 语义
 *   （caption 承载 alt、children 为 void 内层 text leaf）。
 *
 * 位置合同（§5）：
 * - stock p rule 会把 img 拆成块级元素（splitBlockTypes=["img"]），导致
 *   inline 图片 round-trip 时被移动成独立段落；本 kit 的 p rule 覆盖为
 *   不拆分——图片作为 inline void 留在原段落/容器内，standalone 图片
 *   （段落内唯一内容）天然独占块级视觉位置。
 * - style wrapper（**bold** / *italic* / ~~delete~~）在 stock img rule 中
 *   被静默丢弃；本 kit 把 deserialize 的 deco marks 捕获到 void 内层
 *   text leaf，serialize 时重新包回 strong/emphasis/delete。
 *
 * URL trust boundary（§10.1 八规则，赋 img.src 前 fail-closed）：
 * 见 isLoadableImageUrl；reject 项永不赋给 img.src，原始 URL 原样保留
 * 在 Plate node 与 serialize 输出中。
 */
import { useLayoutEffect, useRef, useState } from "react";
import { KEYS, getPluginType } from "platejs";
import {
  convertChildrenDeserialize,
  defaultRules,
  type DeserializeMdOptions,
  type MdDecoration,
  type MdImage,
  type MdParagraph,
  type SerializeMdOptions,
} from "@platejs/markdown";

import {
  createPlatePlugin,
  useEditorRef,
  type PlateElementProps,
} from "platejs/react";
import type { Descendant } from "platejs";

import katex from "katex";
import remarkMath from "remark-math";

import { MARKDOWN_PLUGIN_OPTIONS } from "@/components/editor/plugins/markdown-kit";
import { remarkPreserveUnsupported } from "@/lib/reader-plate/markdown/remark-preserve-unsupported";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// URL loadability validator（§10.1 八规则，Web 端窄范围实现）
// ---------------------------------------------------------------------------

/**
 * 判定图片 URL 是否允许赋给 `<img src>`（fail-closed，依次执行）：
 *
 * 1. 输入必须是 string；
 * 2. trim 后必须与原串相等（首尾空白拒绝）；
 * 3. 拒绝码位 U+0000–U+0020（含裸空格）与 U+007F（词法扫描，不依赖 parser）；
 * 4. 拒绝原始反斜杠 U+005C（编码 `%5C` 不受影响）；
 * 5. 必须有大小写不敏感的 `http://` / `https://` 前缀（词法检查先于 parser）；
 * 6. `new URL()` 必须成功（覆盖非法端口 :bad/:65536/:99999/:-1）；
 * 7. hostname 非空；
 * 8. username/password 均为空。
 *
 * 通过时返回 true 并由调用方使用**原始 URL**（不使用 parser 规范化结果）。
 * 允许 / 拒绝参数矩阵与后端共享（合同 §10.2）。
 */
export function isLoadableImageUrl(raw: unknown): boolean {
  if (typeof raw !== "string") return false;
  if (raw !== raw.trim()) return false;
  for (let i = 0; i < raw.length; i += 1) {
    const code = raw.charCodeAt(i);
    if (code <= 0x20 || code === 0x7f) return false;
  }
  if (raw.includes("\\")) return false;
  const lower = raw.toLowerCase();
  if (!lower.startsWith("http://") && !lower.startsWith("https://")) {
    return false;
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return false;
  }
  if (!parsed.hostname) return false;
  if (parsed.username || parsed.password) return false;
  return true;
}

// ---------------------------------------------------------------------------
// img / p Markdown rules（输入端专用）
// ---------------------------------------------------------------------------

/** 输入端图片 Plate element（单一 image 表示）。 */
export interface InputImageNode {
  type: "img";
  url?: string;
  title?: string | null;
  /** alt 文本（stock @platejs/markdown img rule 的承载位置）。 */
  caption?: Descendant[];
  /** void 内层 text leaf；wrapped image 的 style marks 捕获在此。 */
  children: Descendant[];
}

/**
 * serialize 侧的图片节点结构类型（不窄化 type/caption：
 * MdNodeParser<"img"> 的参数逆变要求接受 TImageElement &
 * TCaptionProps，caption 按 Descendant[] 处理，text 提取在运行时做）。
 */
type InputImageSerializeNode = {
  url?: string;
  title?: string | null;
  caption?: Descendant[];
  children?: Descendant[];
};

type InputImageMdastNode = MdImage & {
  title?: string | null;
};

/** 从 void 内层 text leaf 读取 wrapper marks（bold/italic/strikethrough）。 */
function imageWrapMarks(node: InputImageSerializeNode): {
  bold?: boolean;
  italic?: boolean;
  strikethrough?: boolean;
} {
  const first = Array.isArray(node.children) ? node.children[0] : undefined;
  if (!first || typeof first !== "object") return {};
  const leaf = first as {
    bold?: boolean;
    italic?: boolean;
    strikethrough?: boolean;
  };
  return {
    ...(leaf.bold ? { bold: true } : {}),
    ...(leaf.italic ? { italic: true } : {}),
    ...(leaf.strikethrough ? { strikethrough: true } : {}),
  };
}

/** 拼接 img caption 文本（alt）。 */
function imageAltText(node: InputImageSerializeNode): string {
  if (!Array.isArray(node.caption)) return "";
  return node.caption
    .map((c) =>
      c && typeof c === "object" && typeof (c as { text?: unknown }).text === "string"
        ? (c as { text: string }).text
        : "",
    )
    .join("");
}

/**
 * 输入端 img/p Markdown rules。
 *
 * 覆盖 stock 规则的两处行为（其余与 stock 一致）：
 * - p.deserialize：不把 img 拆成块级节点（inline 位置合同）；
 * - img.deserialize/serialize：捕获并还原 style wrapper marks。
 */
const INPUT_IMAGE_MD_RULES = {
  img: {
    deserialize: (
      mdastNode: InputImageMdastNode,
      deco: MdDecoration,
      options: DeserializeMdOptions,
    ) => {
      const marks = imageMarksFromDeco(deco);
      return {
        type: options.editor
          ? getPluginType(options.editor, KEYS.img)
          : KEYS.img,
        url: mdastNode.url,
        ...(typeof mdastNode.title === "string"
          ? { title: mdastNode.title }
          : {}),
        caption: [{ text: mdastNode.alt ?? "" }],
        children: [{ text: "", ...marks }],
      };
    },
    serialize: (slateNode: InputImageSerializeNode): MdImage => {
      const image: MdImage = {
        type: "image",
        alt: imageAltText(slateNode),
        ...(typeof slateNode.title === "string"
          ? { title: slateNode.title }
          : {}),
        url: slateNode.url ?? "",
      };
      const marks = imageWrapMarks(slateNode);
      if (!marks.bold && !marks.italic && !marks.strikethrough) {
        return image;
      }
      // 与源 mdast 形态一致：strong(emphasis(image))，delete 在最内层。
      // 库声明的 serialize 返回类型为 Image（stock 自身实际返回
      // paragraph），wrapped 变体按运行时合同返回，这里按声明类型收口。
      let wrapped: unknown = image;
      if (marks.strikethrough) {
        wrapped = { type: "delete", children: [wrapped] };
      }
      if (marks.italic) {
        wrapped = { type: "emphasis", children: [wrapped] };
      }
      if (marks.bold) {
        wrapped = { type: "strong", children: [wrapped] };
      }
      return wrapped as MdImage;
    },
  },
  p: {
    deserialize: (
      node: MdParagraph,
      deco: MdDecoration,
      options: DeserializeMdOptions,
    ) => {
      const children = convertChildrenDeserialize(
        node.children ?? [],
        deco,
        options,
      );
      // 与 stock p rule 的差异：不把 img 拆成块级节点（stock
      // splitBlockTypes=["img"]）。图片作为 inline void 留在段落内，
      // inline 图片 round-trip 不漂移位置、不引入额外空段；standalone
      // 图片（段落内唯一内容）仍独占块级视觉位置。
      // 保留 stock 的 ZWSP 清理与尾部孤立软换行丢弃行为。
      const inlineChildren: Descendant[] = [];
      for (const child of children) {
        if ("text" in child && typeof child.text === "string") {
          if (child.text === "\u200B") {
            inlineChildren.push({ ...child, text: "" });
            continue;
          }
          if (
            child.text === "\n" &&
            children.length > 1 &&
            child === children[children.length - 1]
          ) {
            continue;
          }
        }
        inlineChildren.push(child);
      }
      return {
        type: options.editor
          ? getPluginType(options.editor, KEYS.p)
          : KEYS.p,
        children:
          inlineChildren.length > 0 ? inlineChildren : [{ text: "" }],
      };
    },
    serialize: (
      node: { children?: Array<Record<string, unknown>> },
      options: SerializeMdOptions,
    ): MdParagraph => {
      // 与 stock（defaultRules.p.serialize）的唯一差异：段落含其他内容时，
      // 丢弃 Slate 核心 normalize 在 inline void 周围插入的裸 `{text:""}`
      // （slate 要求 inline 节点必须被 text 节点包围）。stock 会把这些
      // 结构性空文本转成 U+200B 输出，污染提交的 markdown，并破坏后端
      // standalone/inline 图片分类语义（ZWSP 不是 CommonMark 空白）。
      // 其余行为（空段 ZWSP 占位 / 换行拆 break / 尾部 break → <br />）
      // 委托 defaultRules.p.serialize——已证
      // buildRules(editor).p.serialize === defaultRules.p.serialize 且
      // 委托输出与原复制实现逐字节一致。
      const children = node.children ?? [];
      const hasContent = children.some(
        (child) =>
          (typeof child.text === "string" && child.text !== "") ||
          (typeof (child as { type?: unknown }).type === "string"),
      );
      const filtered = hasContent
        ? children.filter((child) => {
            const text = (child as { text?: string }).text;
            return !(
              typeof text === "string" &&
              text === "" &&
              Object.keys(child).length === 1
            );
          })
        : children;
      // defaultRules.p / .serialize 类型为 Nullable（运行时必有，
      // 已证 buildRules(editor).p.serialize ===
      // defaultRules.p.serialize）。
      const stockSerialize = defaultRules.p?.serialize as (
        node: Record<string, unknown>,
        options: SerializeMdOptions,
      ) => MdParagraph;
      return stockSerialize({ ...node, children: filtered }, options);
    },
  },
};

/** 从 deserialize deco 提取 style wrapper marks（wrapped image 合同）。 */
function imageMarksFromDeco(deco: MdDecoration): {
  bold?: boolean;
  italic?: boolean;
  strikethrough?: boolean;
} {
  const marks: {
    bold?: boolean;
    italic?: boolean;
    strikethrough?: boolean;
  } = {};
  if (deco?.bold) marks.bold = true;
  if (deco?.italic) marks.italic = true;
  if (deco?.strikethrough) marks.strikethrough = true;
  return marks;
}

/**
 * 输入端唯一的 Markdown options（MarkdownTextInput 与
 * deserialize.ts 的 preserveUnsupported 路径共用，不各自复制）。
 *
 * 在 MARKDOWN_PLUGIN_OPTIONS（非输入端默认）之上：
 * - allowedNodes 追加 "img"（默认 projection 白名单不含 img，行为不变）与
 *   math 节点 "equation"/"inline_equation"（输入预览渲染）；
 * - rules 追加输入端 img/p 覆盖（source_callout 规则原样保留）；
 * - remarkPlugins 追加 remarkMath（$..$ / $$..$$）与
 *   remarkPreserveUnsupported（footnote/task-list 降级）。
 * 输入预览复用编辑端 katex 依赖与 fail-closed 判例；remarkMath 由
 * @platejs/markdown 已声明的 math 规则（equation/inline_equation）承载，
 * 无需第二套 AST。
 */
export const INPUT_MARKDOWN_PLUGIN_OPTIONS = {
  ...MARKDOWN_PLUGIN_OPTIONS,
  allowedNodes: [...MARKDOWN_PLUGIN_OPTIONS.allowedNodes, "img", "equation", "inline_equation"],
  rules: {
    ...MARKDOWN_PLUGIN_OPTIONS.rules,
    img: INPUT_IMAGE_MD_RULES.img,
    p: INPUT_IMAGE_MD_RULES.p,
  },
  remarkPlugins: [
    ...MARKDOWN_PLUGIN_OPTIONS.remarkPlugins,
    remarkMath,
    remarkPreserveUnsupported,
  ],
};

// ---------------------------------------------------------------------------
// 图片 UI 组件：四态（loading / loaded / unsafe / load_failed）+ URL 编辑
// ---------------------------------------------------------------------------

type ImageLoadState = "loading" | "loaded" | "failed";

const IMAGE_PLACEHOLDER_CLASS =
  "inline-flex max-w-full min-h-[4.5rem] flex-col items-start gap-1.5 rounded-[8px] border border-hairline/70 bg-surface-raised/55 px-3 py-2.5 align-top text-[0.85rem] text-ink-soft";
const IMAGE_BUTTON_CLASS =
  "rounded border border-hairline px-2 py-0.5 text-[0.78rem] text-lens-blue hover:bg-surface-raised";

/**
 * 输入端图片元素组件（inline void）。
 *
 * 四态合同（§11.1）：
 * - safe URL：loading 占位（最小高度防跳动）→ onLoad 显示图片；
 *   onError 显示失败占位（alt / 空时「图片加载失败」+ 复制链接 + 修改链接）。
 * - unsafe URL：不渲染带 src 的 img（永不发起请求），显示「链接不安全」
 *   占位 + 原始 URL 可见 + 修改链接；不提供会加载原 URL 的退路。
 * - 修改链接：冻结前本地编辑态；保存只更新当前 image node 的 URL
 *   （alt/title/顺序不变，触发正常 serialize/onChange）；取消零变化。
 *   编辑控件 contentEditable={false}，控件文本永不进入 Markdown serialize。
 * - 原生 `<img loading="lazy" decoding="async" referrerPolicy="no-referrer">`，
 *   不用 next/image。
 *
 * Slate void 合同：必须渲染 {children}（void 内层 text
 * leaf）。slate 的 Editor.point/range 把选区 points 解析到该 leaf，
 * slate-react 的 toDOMPoint 靠它定位 DOM（未渲染时 throw，selection-sync
 * 吞错后 removeAllRanges → tf.select/reveal 的选区静默失效）。渲染后
 * slate-react 自动把 void leaf 画成 ZeroWidthString（不可见、不含正文），
 * 位于 contentEditable=false 的 UI chrome 之外。
 */
function InputImageElement({
  attributes,
  children,
  element,
}: PlateElementProps) {
  const editor = useEditorRef();
  const node = element as InputImageNode;
  const url = typeof node.url === "string" ? node.url : "";
  const altText = imageAltText(node);
  const title = typeof node.title === "string" ? node.title : undefined;
  const safe = isLoadableImageUrl(url);

  const [loadState, setLoadState] = useState<ImageLoadState>("loading");
  const [editing, setEditing] = useState(false);
  const [draftUrl, setDraftUrl] = useState(url);

  // URL 变化（含「修改链接」保存后）重置加载态与编辑草稿：render 期按
  // 前值调整（React props-change 模式，避免 effect 内 setState 级联渲染）。
  const [prevUrl, setPrevUrl] = useState(url);
  if (prevUrl !== url) {
    setPrevUrl(url);
    setLoadState("loading");
    setDraftUrl(url);
  }

  const startEdit = () => {
    setDraftUrl(url);
    setEditing(true);
  };

  const cancelEdit = () => {
    setDraftUrl(url);
    setEditing(false);
  };

  const saveEdit = () => {
    const path = editor.api.findPath(element);
    if (path) {
      // 只更新 URL；alt（caption）/title/children 与节点顺序不动，
      // setNodes 触发正常 onChange → Markdown serialize。
      editor.tf.setNodes({ url: draftUrl } as Partial<InputImageNode>, {
        at: path,
      });
    }
    setEditing(false);
  };

  const copyLink = () => {
    try {
      // 异步 rejection 同步 try/catch 捕不到；显式 .catch 静默吞掉
      //（权限拒绝/文档失焦），占位与「修改链接」仍是手动恢复入口。
      void navigator.clipboard?.writeText(url).catch(() => {});
    } catch {
      // 剪贴板不可用（环境）时同步 no-op；占位与入口仍在。
    }
  };

  if (editing) {
    return (
      <span
        {...attributes}
        data-image-input="true"
        className="inline-block max-w-full align-top"
      >
        <span contentEditable={false} className={IMAGE_PLACEHOLDER_CLASS}>
          <label className="flex w-full items-center gap-2">
            <span className="shrink-0 text-[0.78rem]">图片链接</span>
            <input
              aria-label="图片链接"
              className="min-w-[16rem] flex-1 rounded border border-hairline bg-surface px-2 py-1 font-mono text-[0.8rem] text-ink outline-none"
              value={draftUrl}
              onChange={(event) => setDraftUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  saveEdit();
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  cancelEdit();
                }
              }}
            />
          </label>
          <span className="flex items-center gap-2">
            <button type="button" className={IMAGE_BUTTON_CLASS} onClick={saveEdit}>
              保存
            </button>
            <button type="button" className={IMAGE_BUTTON_CLASS} onClick={cancelEdit}>
              取消
            </button>
          </span>
        </span>
        {children}
      </span>
    );
  }

  if (!safe) {
    return (
      <span
        {...attributes}
        data-image-input="true"
        className="inline-block max-w-full align-top"
      >
        <span contentEditable={false} className={IMAGE_PLACEHOLDER_CLASS}>
          <span className="font-medium text-ink">链接不安全</span>
          <span className="break-all font-mono text-[0.78rem]">{url}</span>
          <button type="button" className={IMAGE_BUTTON_CLASS} onClick={startEdit}>
            修改链接
          </button>
        </span>
        {children}
      </span>
    );
  }

  return (
    <span
      {...attributes}
      data-image-input="true"
      className="inline-block max-w-full align-top"
    >
      <span
        contentEditable={false}
        className="inline-flex max-w-full flex-col items-start gap-1 align-top"
      >
        {loadState === "loading" ? (
          <span
            data-image-state="loading"
            className={IMAGE_PLACEHOLDER_CLASS}
          >
            图片加载中…
          </span>
        ) : null}
        {loadState === "failed" ? (
          <span
            data-image-state="load_failed"
            className={IMAGE_PLACEHOLDER_CLASS}
          >
            <span className="break-all">{altText || "图片加载失败"}</span>
            <span className="flex items-center gap-2">
              <button type="button" className={IMAGE_BUTTON_CLASS} onClick={copyLink}>
                复制链接
              </button>
            </span>
          </span>
        ) : null}
        {loadState !== "failed" ? (
          <img
            data-image-state={loadState === "loaded" ? "loaded" : undefined}
            src={url}
            alt={altText}
            title={title}
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onLoad={() => setLoadState("loaded")}
            onError={() => setLoadState("failed")}
            className={cn(
              "max-w-full rounded-[8px]",
              loadState === "loaded" ? "" : "hidden",
            )}
          />
        ) : null}
        <button type="button" className={IMAGE_BUTTON_CLASS} onClick={startEdit}>
          修改链接
        </button>
      </span>
      {children}
    </span>
  );
}

/**
 * 输入端图片 Plate plugin：inline void element + 四态预览组件。
 * 注册于 MarkdownTextInput 的 plugins（编辑器渲染需要）；
 * deserialize.ts 的输入端 deserializer 不需要组件，仅用 options。
 *
 * HTML deserializer（G1P-B-A）：只匹配原生 `<img>`，从 sanitizer 后 DOM 读
 * `src/alt/title`，直接产出唯一 typed `img` 节点（不再经 generic link 降级）。
 * getAttribute 的 absent 与 explicit empty 不合并：alt absent/空都保持空（不
 * 回退 URL 文本）；title absent 不伪造字段，title="" 保持显式空值。src 被
 * sanitize 摘除时不产出 url，后续由 isLoadableImageUrl 判定进占位；不做任何
 * 网络探测。
 */
export const InputMarkdownImagePlugin = createPlatePlugin({
  key: KEYS.img,
  node: {
    isElement: true,
    isInline: true,
    isVoid: true,
    component: InputImageElement,
  },
  parsers: {
    html: {
      deserializer: {
        rules: [{ validNodeName: "IMG" }],
        parse: ({
          element,
          type,
        }: {
          element: HTMLElement;
          type: string;
        }) => {
          // Plate 静态规则会以 class="slate-img" 命中任意元素；
          // 非 IMG 不认领，回退子节点遍历，避免可见内容被空 typed img 替换。
          if (element.nodeName !== "IMG") return undefined;
          const src = element.getAttribute("src");
          const alt = element.getAttribute("alt") ?? "";
          const title = element.getAttribute("title");
          return {
            type,
            ...(src !== null ? { url: src } : {}),
            ...(title !== null ? { title } : {}),
            caption: [{ text: alt }],
            children: [{ text: "" }],
          };
        },
      },
    },
  },
});

// ---------------------------------------------------------------------------
// Input Math Kit — 输入预览与 Content Check 共用
// 复用编辑端 katex 依赖与 fail-closed 判例；无第二套 AST。
// Plate 类型与 @platejs/markdown 默认 math 规则一致：
// - inline_equation (mdast inlineMath, $..$) — inline void
// - equation (mdast math, $$..$$) — block
// 序列化由默认规则按 texExpression ↔ value 往返，无需覆盖；
// 仅提供渲染组件与 allowlist 接入（见 INPUT_MARKDOWN_PLUGIN_OPTIONS 上方）。
// ---------------------------------------------------------------------------

const INPUT_MATH_FALLBACK_CLASS =
  "inline-flex max-w-full items-center rounded border border-hairline/60 bg-surface-raised/40 px-1.5 py-0.5 font-mono text-xs text-ink-soft break-all";
const INPUT_MATH_DISPLAY_WRAPPER_CLASS =
  "my-3 block w-full overflow-x-auto text-center";

function InputMathInlineElement({ attributes, children, element }: PlateElementProps) {
  const node = element as unknown as { texExpression?: unknown };
  const latex = typeof node.texExpression === "string" ? node.texExpression : "";
  const katexRef = useRef<HTMLSpanElement>(null);
  let html: string | null = null;
  let isError = false;
  try {
    html = katex.renderToString(latex, {
      displayMode: false,
      throwOnError: true,
      strict: false,
      trust: false,
      output: "html",
    });
  } catch {
    isError = true;
    html = null;
  }
  useLayoutEffect(() => {
    if (isError || !html || !katexRef.current) return;
    katexRef.current.innerHTML = html;
  }, [html, isError]);
  if (isError || !html) {
    return (
      <span
        {...attributes}
        data-input-math="true"
        data-math-display="false"
        data-math-state="error"
        className="inline-block align-baseline"
      >
        <span data-input-math-fallback="true" className={INPUT_MATH_FALLBACK_CLASS}>
          {latex}
        </span>
        {children}
      </span>
    );
  }
  return (
    <span
      {...attributes}
      data-input-math="true"
      data-math-display="false"
      data-math-state="ok"
      className="inline-block align-baseline"
    >
      <span ref={katexRef} className="inline-flex align-baseline" data-input-math-content="true" />
      {children}
    </span>
  );
}

function InputMathBlockElement({ attributes, children, element }: PlateElementProps) {
  const node = element as unknown as { texExpression?: unknown };
  const latex = typeof node.texExpression === "string" ? node.texExpression : "";
  const katexRef = useRef<HTMLDivElement>(null);
  let html: string | null = null;
  let isError = false;
  try {
    html = katex.renderToString(latex, {
      displayMode: true,
      throwOnError: true,
      strict: false,
      trust: false,
      output: "html",
    });
  } catch {
    isError = true;
    html = null;
  }
  useLayoutEffect(() => {
    if (isError || !html || !katexRef.current) return;
    katexRef.current.innerHTML = html;
  }, [html, isError]);
  if (isError || !html) {
    return (
      <div
        {...attributes}
        data-input-math="true"
        data-math-display="true"
        data-math-state="error"
        className="my-3"
      >
        <div data-input-math-fallback="true" className={INPUT_MATH_FALLBACK_CLASS}>
          {latex}
        </div>
        {children}
      </div>
    );
  }
  return (
    <div
      {...attributes}
      data-input-math="true"
      data-math-display="true"
      data-math-state="ok"
      className="my-3"
    >
      <div ref={katexRef} className={INPUT_MATH_DISPLAY_WRAPPER_CLASS} data-input-math-content="true" />
      {children}
    </div>
  );
}

export const InputMarkdownMathInlinePlugin = createPlatePlugin({
  key: "inline_equation",
  node: { isElement: true, isInline: true, isVoid: true, component: InputMathInlineElement },
});

export const InputMarkdownMathBlockPlugin = createPlatePlugin({
  key: "equation",
  node: { isElement: true, component: InputMathBlockElement },
});
