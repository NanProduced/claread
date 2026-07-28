"use client";

/**
 * L0 paste spike harness — 真实浏览器验证官方 @platejs 行为插件的
 * HTML/Markdown 反序列化能力。
 *
 * 挂载一个装配了候选行为插件（basic-nodes / list-classic / link /
 * code-block / table）+ 最小渲染壳的 Plate editor，暴露：
 * - `window.__pasteSpikeReady` — harness 就绪信号
 * - `window.__pasteSpikeEditor` — 挂载的 editor（真实 paste 目标）
 * - `window.__pasteSpike.getChildren()` — 当前 editor.children
 * - `window.__pasteSpike.deserializeHtml(html, variant)` — 用全新 editor
 *   走 html deserializer（真实 DOMParser），variant: "candidate" | "todo"
 * - `window.__pasteSpike.deserializeMarkdown(md, variant)` — 同上，走
 *   MarkdownPlugin deserialize
 *
 * Boundary: 测试专用，不修改生产输入路径。
 */

import { useEffect } from "react";
import type { Descendant } from "platejs";
import {
  createPlateEditor,
  createPlatePlugin,
  type PlateElementProps,
  type PlateLeafProps,
  Plate,
  PlateContent,
  usePlateEditor,
} from "platejs/react";
import {
  BaseBlockquotePlugin,
  BaseBoldPlugin,
  BaseCodePlugin,
  BaseH1Plugin,
  BaseH2Plugin,
  BaseH3Plugin,
  BaseH4Plugin,
  BaseH5Plugin,
  BaseH6Plugin,
  BaseHorizontalRulePlugin,
  BaseItalicPlugin,
  BaseStrikethroughPlugin,
} from "@platejs/basic-nodes";
import { BaseCodeBlockPlugin, BaseCodeLinePlugin } from "@platejs/code-block";
import { BaseLinkPlugin } from "@platejs/link";
import {
  BaseBulletedListPlugin,
  BaseListItemContentPlugin,
  BaseListItemPlugin,
  BaseListPlugin,
  BaseNumberedListPlugin,
  BaseTaskListPlugin,
  BaseTodoListPlugin,
} from "@platejs/list-classic";
import {
  BaseTableCellHeaderPlugin,
  BaseTableCellPlugin,
  BaseTablePlugin,
  BaseTableRowPlugin,
} from "@platejs/table";
import { MarkdownPlugin } from "@platejs/markdown";
import remarkGfm from "remark-gfm";

// ---------------------------------------------------------------------------
// 最小渲染壳（自包含，与 plate-text-input-spike.tsx 同一风格）
// ---------------------------------------------------------------------------

function ShellParagraph({ children, attributes }: PlateElementProps) {
  return <p {...attributes}>{children}</p>;
}

function ShellHeading({ children, element, attributes }: PlateElementProps) {
  const type = (element as { type?: string }).type ?? "h6";
  const Tag = type as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
  const Component = Tag as React.ElementType;
  return <Component {...attributes}>{children}</Component>;
}

function ShellBlockquote({ children, attributes }: PlateElementProps) {
  return (
    <blockquote {...attributes} className="border-l-2 border-gray-300 pl-3 italic">
      {children}
    </blockquote>
  );
}

function ShellUl({ children, attributes }: PlateElementProps) {
  return <ul {...attributes}>{children}</ul>;
}

function ShellOl({ children, attributes }: PlateElementProps) {
  return <ol {...attributes}>{children}</ol>;
}

function ShellLi({ children, attributes }: PlateElementProps) {
  return <li {...attributes}>{children}</li>;
}

function ShellLic({ children, attributes }: PlateElementProps) {
  return <span {...attributes}>{children}</span>;
}

function ShellCodeBlock({ children, attributes }: PlateElementProps) {
  return (
    <pre {...attributes} className="overflow-x-auto rounded bg-gray-100 p-2">
      <code>{children}</code>
    </pre>
  );
}

function ShellCodeLine({ children, attributes }: PlateElementProps) {
  return <div {...attributes}>{children}</div>;
}

function ShellHr({ children, attributes }: PlateElementProps) {
  return (
    <div {...attributes}>
      <hr className="my-2 border-gray-300" />
      {children}
    </div>
  );
}

function ShellTable({ children, attributes }: PlateElementProps) {
  return (
    <table {...attributes} className="border-collapse border border-gray-400">
      <tbody>{children}</tbody>
    </table>
  );
}

function ShellTableRow({ children, attributes }: PlateElementProps) {
  return <tr {...attributes}>{children}</tr>;
}

function ShellTableCell({ children, attributes, element }: PlateElementProps) {
  const isHeader = (element as { type?: string }).type === "th";
  const Tag = (isHeader ? "th" : "td") as React.ElementType;
  return (
    <Tag {...attributes} className="border border-gray-400 px-2 py-1">
      {children}
    </Tag>
  );
}

function ShellLink({ children, element, attributes }: PlateElementProps) {
  const url = (element as { url?: string }).url ?? "#";
  return (
    <a {...attributes} href={url} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

function ShellBold({ children, attributes }: PlateLeafProps) {
  return <strong {...attributes}>{children}</strong>;
}

function ShellItalic({ children, attributes }: PlateLeafProps) {
  return <em {...attributes}>{children}</em>;
}

function ShellCode({ children, attributes }: PlateLeafProps) {
  return (
    <code {...attributes} className="rounded bg-gray-100 px-1">
      {children}
    </code>
  );
}

function ShellStrikethrough({ children, attributes }: PlateLeafProps) {
  return <span {...attributes} className="line-through">{children}</span>;
}

// ---------------------------------------------------------------------------
// 行为插件装配（component 通过 .configure 挂到官方插件上）
// ---------------------------------------------------------------------------

function buildPlugins(variant: "candidate" | "todo") {
  const plugins = [
    // MarkdownPlugin（不带 allowedNodes 白名单，观察官方默认行为）
    MarkdownPlugin.configure({
      options: { remarkPlugins: [remarkGfm] },
    }),
    // basic-nodes：标题/引用/分隔线 + marks
    BaseH1Plugin.configure({ node: { component: ShellHeading } }),
    BaseH2Plugin.configure({ node: { component: ShellHeading } }),
    BaseH3Plugin.configure({ node: { component: ShellHeading } }),
    BaseH4Plugin.configure({ node: { component: ShellHeading } }),
    BaseH5Plugin.configure({ node: { component: ShellHeading } }),
    BaseH6Plugin.configure({ node: { component: ShellHeading } }),
    BaseBlockquotePlugin.configure({ node: { component: ShellBlockquote } }),
    BaseHorizontalRulePlugin.configure({ node: { component: ShellHr } }),
    BaseBoldPlugin.configure({ node: { component: ShellBold } }),
    BaseItalicPlugin.configure({ node: { component: ShellItalic } }),
    BaseCodePlugin.configure({ node: { component: ShellCode } }),
    BaseStrikethroughPlugin.configure({ node: { component: ShellStrikethrough } }),
    // list-classic：BaseListPlugin 自带 ul/ol/taskList/li/lic 子插件与
    // withList 行为；重复的 key 会被合并（已验证 first-wins + merge），
    // 这里用独立子插件 configure 挂 component。
    BaseListPlugin,
    BaseBulletedListPlugin.configure({ node: { component: ShellUl } }),
    BaseNumberedListPlugin.configure({ node: { component: ShellOl } }),
    BaseTaskListPlugin.configure({ node: { component: ShellUl } }),
    BaseListItemPlugin.configure({ node: { component: ShellLi } }),
    BaseListItemContentPlugin.configure({ node: { component: ShellLic } }),
    // link / code-block / table
    BaseLinkPlugin.configure({ node: { component: ShellLink } }),
    BaseCodeBlockPlugin.configure({ node: { component: ShellCodeBlock } }),
    BaseCodeLinePlugin.configure({ node: { component: ShellCodeLine } }),
    BaseTablePlugin.configure({ node: { component: ShellTable } }),
    BaseTableRowPlugin.configure({ node: { component: ShellTableRow } }),
    BaseTableCellPlugin.configure({ node: { component: ShellTableCell } }),
    BaseTableCellHeaderPlugin.configure({ node: { component: ShellTableCell } }),
    // paragraph component 壳（merge 到 core ParagraphPlugin）
    createPlatePlugin({
      key: "p",
      node: { isElement: true, component: ShellParagraph },
    }),
  ];

  if (variant === "todo") {
    plugins.push(
      BaseTodoListPlugin.configure({ node: { component: ShellLi } }) as never,
    );
  }

  return plugins;
}

// ---------------------------------------------------------------------------
// Window globals
// ---------------------------------------------------------------------------

declare global {
  interface Window {
    __pasteSpikeReady?: boolean;
    __pasteSpikeEditor?: unknown;
    __pasteSpikeDebug?: unknown[];
    __pasteSpike?: {
      getChildren: () => Descendant[];
      deserializeHtml: (
        html: string,
        variant?: "candidate" | "todo",
      ) => Descendant[];
      deserializeMarkdown: (
        md: string,
        variant?: "candidate" | "todo",
      ) => Descendant[];
    };
  }
}

function PasteSpikeHarness() {
  const editor = usePlateEditor(
    {
      plugins: buildPlugins("candidate"),
      value: [{ type: "p", children: [{ text: "" }] }],
    },
    [],
  );

  useEffect(() => {
    if (!editor) return;
    window.__pasteSpikeEditor = editor;
    window.__pasteSpikeDebug = [];
    const probe = (e: Event) => {
      const ce = e as globalThis.ClipboardEvent;
      window.__pasteSpikeDebug!.push({
        phase: "captured",
        defaultPrevented: ce.defaultPrevented,
        isTrusted: ce.isTrusted,
        hasHtml: Boolean(ce.clipboardData?.getData("text/html")),
        hasPlain: Boolean(ce.clipboardData?.getData("text/plain")),
        target: (ce.target as HTMLElement | null)?.tagName ?? null,
        selection: (editor as { selection?: unknown }).selection,
      });
    };
    document.addEventListener("paste", probe, true);
    window.__pasteSpike = {
      getChildren: () => editor.children,
      deserializeHtml: (html, variant = "candidate") => {
        const e = createPlateEditor({ plugins: buildPlugins(variant) });
        const doc = new DOMParser().parseFromString(html, "text/html");
        return e.api.html.deserialize({
          element: doc.body,
        }) as Descendant[];
      },
      deserializeMarkdown: (md, variant = "candidate") => {
        const e = createPlateEditor({ plugins: buildPlugins(variant) });
        return e.getApi(MarkdownPlugin).markdown.deserialize(md) as Descendant[];
      },
    };
    window.__pasteSpikeReady = true;
    return () => document.removeEventListener("paste", probe, true);
  }, [editor]);

  return (
    <Plate editor={editor}>
      <div className="min-h-[200px] border border-gray-200 bg-white">
        <PlateContent
          data-testid="paste-spike-editor"
          className="space-y-2 px-3 py-2 text-sm outline-none"
        />
      </div>
    </Plate>
  );
}

export default function E2EPlatePasteSpikeHarness() {
  return (
    <main className="min-h-screen bg-background px-8 py-8">
      <h1 className="mb-4 text-lg font-semibold text-ink">
        E2E Plate Paste Spike (L0)
      </h1>
      <p className="mb-6 text-sm text-muted-foreground">
        Test-only harness. Official behavior plugins + minimal shells. Editor
        exposed on <code>window.__pasteSpikeEditor</code>.
      </p>
      <div className="mx-auto max-w-[72ch]">
        <PasteSpikeHarness />
      </div>
    </main>
  );
}
