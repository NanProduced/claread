"use client";

/**
 * Structured Source renderer (G0 frozen contract).
 *
 * Renders the parser-output block tree from
 * `services/api/tests/fixtures/markdown_structured_source/CONTRACT.md` into a
 * React component tree. This is a pure projection — it does NOT re-parse raw
 * Markdown and does NOT touch the Ask panel / SSE / transport / RAG sidecar.
 *
 * Truth sources (per G0 §3):
 *   - `block.text_content` — plain-text rendering of inline tokens
 *   - `block.payload_json` — per-block metadata (level / ordered / language / links / alignments ...)
 *   - `block.parent_block_id` — parent-child structure for list/table nesting
 *   - `block.inline_marks` — OPTIONAL inline span boundaries (reserved for future parser version)
 *
 * Safety:
 *   - Links are protocol-whitelist-filtered (http / https / mailto) by the
 *     parser before reaching the renderer. The renderer applies a defensive
 *     second whitelist check (fail-closed) so a malformed payload cannot
 *     inject `javascript:` / `data:` / `vbscript:` hrefs.
 *   - Mermaid code blocks are tagged `data-language="mermaid"` and NOT
 *     executed (static only, per G0 §5.1 `mermaid_static_only`).
 *   - Raw HTML is never rendered as a first-class block type (G0 §3.5).
 *
 * Reference: apps/web/docs/reader-ia.md 合同与 Fixture
 */

import { Fragment, type ReactNode } from "react";

import type {
  ReaderStructuredSourceBlock,
  ReaderStructuredSourceDiagnostic,
  ReaderStructuredSourceInlineMark,
  ReaderStructuredSourceLink,
  ReaderStructuredSourceStrippedLink,
  ReaderStructuredSourceWarning,
} from "@/types/api/reader-plate";

const SAFE_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

function isSafeHref(href: unknown): href is string {
  if (typeof href !== "string" || href.length === 0) {
    return false;
  }
  // Allow relative + anchor hrefs (parser whitelist already filters, but be
  // defensive). For absolute URLs, require whitelist protocol.
  try {
    const url = new URL(href, "https://placeholder.invalid");
    return SAFE_LINK_PROTOCOLS.has(url.protocol);
  } catch {
    // Not a valid absolute URL — allow only if it is a pure anchor / relative
    // path with no scheme. Reject anything that looks like `scheme:...`.
    if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
      return false;
    }
    return href.startsWith("#") || href.startsWith("/");
  }
}

function asNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

function asBoolean(value: unknown): boolean | null {
  if (typeof value !== "boolean") {
    return null;
  }
  return value;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asLinks(value: unknown): ReaderStructuredSourceLink[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const links: ReaderStructuredSourceLink[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const text = asString(record.text);
    const href = asString(record.href);
    if (text !== null && isSafeHref(href)) {
      links.push({ text, href });
    }
  }
  return links;
}

function asStrippedLinks(value: unknown): ReaderStructuredSourceStrippedLink[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const stripped: ReaderStructuredSourceStrippedLink[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const text = asString(record.text);
    const href = asString(record.href);
    const reason = asString(record.reason) ?? "unsafe_protocol";
    if (text !== null && href !== null) {
      stripped.push({ text, href, reason });
    }
  }
  return stripped;
}

interface BlockTreeNode {
  block: ReaderStructuredSourceBlock;
  children: BlockTreeNode[];
}

function buildBlockTree(blocks: ReaderStructuredSourceBlock[]): BlockTreeNode[] {
  const byId = new Map<string, BlockTreeNode>();
  for (const block of blocks) {
    byId.set(block.block_id, { block, children: [] });
  }

  const roots: BlockTreeNode[] = [];
  for (const block of blocks) {
    const node = byId.get(block.block_id);
    if (!node) continue;
    const parentId = block.parent_block_id;
    if (parentId === null || parentId === undefined) {
      roots.push(node);
    } else {
      const parent = byId.get(parentId);
      if (parent) {
        parent.children.push(node);
      } else {
        // Orphan block — surface as a root so it is still visible.
        roots.push(node);
      }
    }
  }

  const sortRecursive = (nodes: BlockTreeNode[]) => {
    nodes.sort((a, b) => a.block.order_index - b.block.order_index);
    for (const node of nodes) {
      sortRecursive(node.children);
    }
  };
  sortRecursive(roots);

  return roots;
}

function renderInlineMarks(
  textContent: string,
  marks: ReaderStructuredSourceInlineMark[] | undefined,
): ReactNode {
  if (!marks || marks.length === 0) {
    return textContent;
  }

  // Render marks as inline spans in declaration order.
  //
  // IMPORTANT: When the backend parser is upgraded to emit `inline_marks`,
  // the marks array MUST cover the full text span of the block (i.e.,
  // concatenating all mark.text must equal textContent). Partial-coverage
  // marks would cause text loss. The G0 frozen contract currently does NOT
  // emit inline_marks — backend flattens marks into text_content (see
  // CONTRACT.md Clause 3, "Inline flattening"). This path is reserved for
  // a future parser-version bump.
  //
  // Bug fix: previously `nodes` was initialized to
  // `[textContent]` and then each mark was pushed, causing the full text
  // to render twice (once as raw textContent + once as concatenated mark
  // texts). The bug was latent because the backend never emits marks.
  const nodes: ReactNode[] = [];
  for (let i = 0; i < marks.length; i++) {
    const mark = marks[i];
    if (!mark) continue;
    const key = `mark-${i}`;
    switch (mark.kind) {
      case "emphasis":
        nodes.push(<em key={key}>{mark.text}</em>);
        break;
      case "strong":
        nodes.push(<strong key={key}>{mark.text}</strong>);
        break;
      case "strikethrough":
        nodes.push(<s key={key}>{mark.text}</s>);
        break;
      case "inline_code":
        nodes.push(<code key={key}>{mark.text}</code>);
        break;
      case "link": {
        if (isSafeHref(mark.href)) {
          nodes.push(
            <a
              key={key}
              href={mark.href}
              target="_blank"
              rel="noopener noreferrer"
            >
              {mark.text}
            </a>,
          );
        } else {
          nodes.push(<span key={key}>{mark.text}</span>);
        }
        break;
      }
    }
  }
  return <>{nodes}</>;
}

function renderParagraphLinks(payload: Record<string, unknown>): ReactNode[] {
  const links = asLinks(payload.links);
  if (links.length === 0) {
    return [];
  }
  return links.map((link, index) => (
    <a
      key={`paragraph-link-${index}`}
      href={link.href}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="structured-source-safe-link"
    >
      {link.text}
    </a>
  ));
}

function renderStrippedLinksNotice(
  payload: Record<string, unknown>,
): ReactNode | null {
  const stripped = asStrippedLinks(payload.stripped_links);
  if (stripped.length === 0) {
    return null;
  }
  return (
    <span
      data-testid="structured-source-stripped-links"
      aria-label={`${stripped.length} 个不安全协议链接已移除`}
      className="ml-1 inline-flex items-center rounded-[4px] border border-amber-400/50 bg-amber-50 px-1.5 py-0.5 text-[0.7rem] font-medium text-amber-800"
      title={`已移除 ${stripped.length} 个不安全协议链接（javascript/data/vbscript）`}
    >
      {stripped.length} 个不安全链接已移除
    </span>
  );
}

function renderHeading(node: BlockTreeNode): ReactNode {
  const level = asNumber(node.block.payload_json.level) ?? 2;
  const Tag = (`h${Math.min(Math.max(level, 1), 6)}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6");
  return (
    <Tag
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="heading"
      data-heading-level={Tag}
    >
      {renderInlineMarks(node.block.text_content ?? "", node.block.inline_marks)}
    </Tag>
  );
}

function renderParagraph(node: BlockTreeNode): ReactNode {
  const payload = node.block.payload_json;
  const extractedFrom = asString(payload.extracted_from);
  const strippedNotice = renderStrippedLinksNotice(payload);
  return (
    <p
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="paragraph"
      data-extracted-from={extractedFrom ?? undefined}
    >
      {renderInlineMarks(node.block.text_content ?? "", node.block.inline_marks)}
      {renderParagraphLinks(payload).map((link) => link)}
      {strippedNotice}
    </p>
  );
}

function renderBlockquote(node: BlockTreeNode, renderNode: (node: BlockTreeNode) => ReactNode): ReactNode {
  return (
    <blockquote
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="blockquote"
    >
      {node.block.text_content ?? null}
      {node.children.map((child) => renderNode(child))}
    </blockquote>
  );
}

function renderThematicBreak(node: BlockTreeNode): ReactNode {
  return (
    <hr
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="thematic_break"
    />
  );
}

function renderList(node: BlockTreeNode, renderNode: (node: BlockTreeNode) => ReactNode): ReactNode {
  const ordered = asBoolean(node.block.payload_json.ordered) ?? false;
  const depth = asNumber(node.block.payload_json.depth) ?? 0;
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="list"
      data-list-ordered={ordered ? "true" : "false"}
      data-list-depth={depth}
    >
      {node.children.map((child) => renderNode(child))}
    </Tag>
  );
}

function renderListItem(node: BlockTreeNode, renderNode: (node: BlockTreeNode) => ReactNode): ReactNode {
  // list_item may contain nested `list` children (recursive nesting per G0 §3.3).
  const nestedListChildren = node.children.filter(
    (child) => child.block.block_type === "list",
  );
  const otherChildren = node.children.filter(
    (child) => child.block.block_type !== "list",
  );
  return (
    <li
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="list_item"
    >
      {renderInlineMarks(node.block.text_content ?? "", node.block.inline_marks)}
      {otherChildren.map((child) => renderNode(child))}
      {nestedListChildren.map((child) => renderNode(child))}
    </li>
  );
}

function renderCodeBlock(node: BlockTreeNode): ReactNode {
  const language = asString(node.block.payload_json.language);
  const fenced = asBoolean(node.block.payload_json.fenced);
  const closed = asBoolean(node.block.payload_json.closed);
  const isMermaid = language === "mermaid";
  // Phase 3 / P2: visible language badge for non-mermaid code blocks.
  // Mermaid blocks already carry data-mermaid and are handled by a separate
  // static-render path; showing a "MERMAID" badge would be noise.
  const hasLanguageBadge = Boolean(language) && !isMermaid;
  return (
    <pre
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="code_block"
      data-language={language ?? undefined}
      data-fenced={fenced === null ? undefined : fenced ? "true" : "false"}
      data-closed={closed === null ? undefined : closed ? "true" : "false"}
      className="relative"
    >
      {hasLanguageBadge ? (
        <span
          data-testid="code-language-badge"
          className="absolute right-3 top-2 font-sans text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground/70"
        >
          {language}
        </span>
      ) : null}
      <code
        data-language={language ?? undefined}
        data-mermaid={isMermaid ? "true" : undefined}
      >
        {node.block.text_content ?? ""}
      </code>
    </pre>
  );
}

function renderTable(node: BlockTreeNode, renderNode: (node: BlockTreeNode) => ReactNode): ReactNode {
  const rows = node.children.filter((child) => child.block.block_type === "table_row");
  const headerRows = rows.filter(
    (child) => asBoolean(child.block.payload_json.is_header) === true,
  );
  const bodyRows = rows.filter(
    (child) => asBoolean(child.block.payload_json.is_header) !== true,
  );

  return (
    <table
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="table"
    >
      {headerRows.length > 0 ? (
        <thead data-testid="structured-source-table-head">
          {headerRows.map((row) => renderNode(row))}
        </thead>
      ) : null}
      {bodyRows.length > 0 ? (
        <tbody data-testid="structured-source-table-body">
          {bodyRows.map((row) => renderNode(row))}
        </tbody>
      ) : null}
    </table>
  );
}

function renderTableRow(node: BlockTreeNode, renderNode: (node: BlockTreeNode) => ReactNode): ReactNode {
  const isHeader = asBoolean(node.block.payload_json.is_header) === true;
  const Tag = isHeader ? "tr" : "tr";
  return (
    <Tag
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="table_row"
      data-row-is-header={isHeader ? "true" : "false"}
    >
      {node.children.map((child) => renderNode(child))}
    </Tag>
  );
}

function renderTableCell(node: BlockTreeNode): ReactNode {
  const isHeader = asBoolean(node.block.payload_json.is_header) === true;
  const alignment = asString(node.block.payload_json.alignment) ?? "default";
  const Tag = isHeader ? "th" : "td";
  return (
    <Tag
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="table_cell"
      data-cell-alignment={alignment}
      data-cell-is-header={isHeader ? "true" : "false"}
      style={
        alignment === "left" || alignment === "right" || alignment === "center"
          ? { textAlign: alignment }
          : undefined
      }
    >
      {renderInlineMarks(node.block.text_content ?? "", node.block.inline_marks)}
    </Tag>
  );
}

function renderFootnote(node: BlockTreeNode): ReactNode {
  const footnoteId = asString(node.block.payload_json.footnote_id);
  return (
    <aside
      key={node.block.block_id}
      data-block-id={node.block.block_id}
      data-block-type="footnote"
      data-footnote-id={footnoteId ?? undefined}
      role="note"
    >
      {node.block.text_content ?? ""}
    </aside>
  );
}

function renderBlockNode(node: BlockTreeNode): ReactNode {
  switch (node.block.block_type) {
    case "heading":
      return renderHeading(node);
    case "paragraph":
      return renderParagraph(node);
    case "blockquote":
      return renderBlockquote(node, renderBlockNode);
    case "thematic_break":
      return renderThematicBreak(node);
    case "list":
      return renderList(node, renderBlockNode);
    case "list_item":
      return renderListItem(node, renderBlockNode);
    case "code_block":
      return renderCodeBlock(node);
    case "table":
      return renderTable(node, renderBlockNode);
    case "table_row":
      return renderTableRow(node, renderBlockNode);
    case "table_cell":
      return renderTableCell(node);
    case "footnote":
      return renderFootnote(node);
    default:
      // Unknown block type — render text_content as a defensive fallback
      // rather than crashing. G0 contract emits only the closed set above.
      return (
        <p
          key={node.block.block_id}
          data-block-id={node.block.block_id}
          data-block-type={node.block.block_type}
          data-unknown-block="true"
        >
          {node.block.text_content ?? ""}
        </p>
      );
  }
}

const WARNING_LABEL: Record<ReaderStructuredSourceWarning["code"], string> = {
  raw_html_block: "原始 HTML 块",
  inline_html: "内联 HTML",
  has_unclosed_fence: "代码块未闭合",
  unsafe_link_protocol: "不安全协议链接",
  footnote_reference: "脚注引用",
  strikethrough_extension: "GFM 删除线扩展",
  mermaid_static_only: "Mermaid 仅静态渲染",
  code_dominant: "代码主导内容",
  missing_source_range: "缺少源码区间",
};

function renderWarnings(warnings: ReaderStructuredSourceWarning[]): ReactNode {
  if (warnings.length === 0) {
    return null;
  }
  return (
    <ul
      data-testid="structured-source-warnings"
      className="list-disc space-y-0.5 pl-4 text-[0.76rem] text-amber-800"
      aria-label="结构化源码警告"
    >
      {warnings.map((warning, index) => (
        <li
          key={`warning-${warning.code}-${index}`}
          data-warning-code={warning.code}
          data-testid={`structured-source-warning-${warning.code}`}
        >
          <span className="font-medium">{WARNING_LABEL[warning.code] ?? warning.code}</span>
          <span className="text-amber-900/70"> · {warning.message}</span>
        </li>
      ))}
    </ul>
  );
}

function renderUnsupported(
  unsupported: { code: string; message: string }[],
): ReactNode {
  if (unsupported.length === 0) {
    return null;
  }
  return (
    <ul
      data-testid="structured-source-unsupported"
      className="list-disc space-y-0.5 pl-4 text-[0.76rem] text-muted-foreground"
      aria-label="不支持的特性"
    >
      {unsupported.map((entry, index) => (
        <li
          key={`unsupported-${entry.code}-${index}`}
          data-unsupported-code={entry.code}
        >
          <span className="font-medium">{entry.code}</span>
          <span className="text-muted-foreground"> · {entry.message}</span>
        </li>
      ))}
    </ul>
  );
}

const OUTCOME_LABEL: Record<string, string> = {
  stable_document_ready: "结构化文档就绪",
  candidate_document_required: "需要候选文档确认",
  input_rejected_or_action_required: "输入被拒绝，需要处理",
};

function renderOutcome(outcome: string): ReactNode {
  const label = OUTCOME_LABEL[outcome] ?? outcome;
  const tone =
    outcome === "stable_document_ready"
      ? "text-emerald-700"
      : outcome === "candidate_document_required"
        ? "text-amber-700"
        : "text-red-700";
  return (
    <p
      data-testid="structured-source-outcome"
      data-outcome={outcome}
      className={`text-[0.78rem] font-semibold ${tone}`}
    >
      {label}
    </p>
  );
}

export interface StructuredSourceRendererProps {
  blocks: ReaderStructuredSourceBlock[];
  diagnostic?: ReaderStructuredSourceDiagnostic;
  className?: string;
}

export function StructuredSourceRenderer({
  blocks,
  diagnostic,
  className,
}: StructuredSourceRendererProps) {
  const tree = buildBlockTree(blocks);
  return (
    <div
      data-testid="structured-source-renderer"
      className={className}
    >
      <div data-testid="structured-source-blocks" className="space-y-3">
        {tree.map((node) => (
          <Fragment key={node.block.block_id}>{renderBlockNode(node)}</Fragment>
        ))}
      </div>
      {diagnostic ? (
        <div
          data-testid="structured-source-diagnostics"
          className="mt-4 space-y-2 border-t border-hairline/60 pt-3"
        >
          {renderOutcome(diagnostic.outcome)}
          {renderWarnings(diagnostic.warnings)}
          {renderUnsupported(diagnostic.unsupported)}
        </div>
      ) : null}
    </div>
  );
}
