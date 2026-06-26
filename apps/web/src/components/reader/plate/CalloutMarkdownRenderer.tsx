/**
 * CalloutMarkdownRenderer — 轻量递归渲染 Plate Descendant[] 节点树
 *
 * 用于 CalloutBlock 内嵌渲染 `deserializeMarkdownToBlocks` 产生的节点树。
 * 不依赖 @platejs/basic-* 包，不使用 PlateStatic（项目未安装）。
 *
 * 支持的节点类型（覆盖 MarkdownPlugin + remarkGfm 的 deserialize 输出）：
 * - 块级：p、h1-h6、blockquote、ul、ol、li（含 lic 子节点）、code_block、hr
 * - 行内 marks：bold、italic、strikethrough、code、纯文本
 *
 * 未知块级类型兜底为 <div> + 递归 children；未知行内节点兜底为文本拼接。
 */
import * as React from "react";
import type { Descendant } from "platejs";

interface CalloutMarkdownRendererProps {
  nodes: Descendant[];
  className?: string;
}

type AnyNode = Record<string, unknown> & {
  type?: string;
  children?: AnyNode[];
  text?: string;
  bold?: boolean;
  italic?: boolean;
  strikethrough?: boolean;
  code?: boolean;
};

function isTextNode(node: AnyNode): boolean {
  return typeof node.text === "string";
}

function renderInline(nodes: AnyNode[] | undefined, keyPrefix: string): React.ReactNode {
  if (!nodes || nodes.length === 0) {
    return null;
  }
  return nodes.map((node, index) => {
    const key = `${keyPrefix}-inline-${index}`;
    if (isTextNode(node)) {
      const text = node.text as string;
      if (text === "") {
        return null;
      }
      let element: React.ReactNode = text;
      if (node.code) {
        element = (
          <code
            key={key}
            className="rounded bg-muted/50 px-1 py-0.5 font-mono text-[0.85em]"
          >
            {element}
          </code>
        );
      }
      if (node.strikethrough) {
        element = (
          <span key={key} className="line-through">
            {element}
          </span>
        );
      }
      if (node.italic) {
        element = (
          <em key={key} className="italic">
            {element}
          </em>
        );
      }
      if (node.bold) {
        element = (
          <strong key={key} className="font-semibold">
            {element}
          </strong>
        );
      }
      // 如果没有任何 mark，element 仍是字符串；需要包一层 span 以稳定 key
      if (typeof element === "string") {
        return <span key={key}>{element}</span>;
      }
      return element;
    }
    // 非文本行内节点（如 link）兜底：递归 children
    if (node.children) {
      return (
        <span key={key}>{renderInline(node.children, key)}</span>
      );
    }
    return null;
  });
}

function renderNode(node: AnyNode, key: string): React.ReactNode {
  const type = typeof node.type === "string" ? node.type : "";
  const children = node.children;

  switch (type) {
    case "p":
      return (
        <p key={key} className="my-1 leading-6">
          {renderInline(children, key)}
        </p>
      );
    case "h1":
      return (
        <h1 key={key} className="my-2 text-lg font-semibold leading-snug">
          {renderInline(children, key)}
        </h1>
      );
    case "h2":
      return (
        <h2 key={key} className="my-2 text-base font-semibold leading-snug">
          {renderInline(children, key)}
        </h2>
      );
    case "h3":
      return (
        <h3 key={key} className="my-2 text-[0.95rem] font-semibold leading-snug">
          {renderInline(children, key)}
        </h3>
      );
    case "h4":
    case "h5":
    case "h6":
      return (
        <h4 key={key} className="my-2 text-[0.9rem] font-semibold leading-snug">
          {renderInline(children, key)}
        </h4>
      );
    case "blockquote":
      return (
        <blockquote
          key={key}
          className="my-1 border-l-2 border-current/30 pl-3 italic text-ink-soft"
        >
          {(children ?? []).map((child, index) =>
            renderNode(child, `${key}-bq-${index}`),
          )}
        </blockquote>
      );
    case "ul":
      return (
        <ul key={key} className="my-1 list-disc pl-5 leading-6">
          {(children ?? []).map((child, index) =>
            renderNode(child, `${key}-ul-${index}`),
          )}
        </ul>
      );
    case "ol":
      return (
        <ol key={key} className="my-1 list-decimal pl-5 leading-6">
          {(children ?? []).map((child, index) =>
            renderNode(child, `${key}-ol-${index}`),
          )}
        </ol>
      );
    case "li":
      return (
        <li key={key}>
          {(children ?? []).map((child, index) => {
            if (isTextNode(child)) {
              return renderInline([child], `${key}-li-${index}`);
            }
            // lic 等子节点：直接渲染内容，不再包裹 <li>
            if (child.type === "lic") {
              return renderInline(child.children, `${key}-lic-${index}`);
            }
            return renderNode(child, `${key}-li-${index}`);
          })}
        </li>
      );
    case "lic":
      // 单独出现的 lic（不在 li 下）：渲染为 span
      return (
        <span key={key}>
          {renderInline(children, key)}
        </span>
      );
    case "code_block":
    case "pre": {
      const text = (children ?? [])
        .map((child) => (typeof child.text === "string" ? child.text : ""))
        .join("");
      return (
        <pre
          key={key}
          className="my-1 overflow-x-auto rounded bg-muted/40 p-2 text-xs leading-5"
        >
          <code>{text}</code>
        </pre>
      );
    }
    case "hr":
      return <hr key={key} className="my-2 border-current/15" />;
    default:
      // 未知块级节点兜底
      if (children && children.length > 0) {
        return (
          <div key={key}>
            {children.map((child, index) => {
              if (isTextNode(child)) {
                return renderInline([child], `${key}-div-${index}`);
              }
              return renderNode(child, `${key}-div-${index}`);
            })}
          </div>
        );
      }
      return <div key={key} />;
  }
}

export function CalloutMarkdownRenderer({
  nodes,
  className,
}: CalloutMarkdownRendererProps) {
  if (!nodes || nodes.length === 0) {
    return null;
  }
  return (
    <div className={className}>
      {nodes.map((node, index) =>
        renderNode(node as AnyNode, `callout-md-${index}`),
      )}
    </div>
  );
}

export default CalloutMarkdownRenderer;
