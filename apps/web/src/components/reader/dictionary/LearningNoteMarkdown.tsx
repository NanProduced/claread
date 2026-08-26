"use client";

/**
 * Safe Markdown renderer for phrase_gloss `learning_note`.
 *
 * Contract subset only (math 不在合同子集内):
 * - plain paragraphs
 * - bold emphasis
 * - inline code
 * - short unordered lists
 * - necessary line breaks
 *
 * Headings, links, images, tables, blockquotes, ordered lists, fenced code
 * blocks, raw HTML, and math (equation / inline_equation, $..$ / $$..$$)
 * must not produce matching DOM. Math intentionally excluded from this
 * {cjk} contract-subset; input preview (MarkdownTextInput + Content Check)
 * owns KaTeX rendering, Reader owns its own math rendering. Uses Streamdown with
 * no dangerouslySetInnerHTML.
 */
import { cjk } from "@streamdown/cjk";
import { Streamdown } from "streamdown";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

const plugins = { cjk };

/** Tags permitted in the learning_note Markdown contract. */
export const LEARNING_NOTE_ALLOWED_ELEMENTS = [
  "p",
  "strong",
  "b",
  "code",
  "ul",
  "li",
  "br",
] as const;

/**
 * Drop disallowed structure while keeping plain text children when Streamdown
 * still emits a node (belt-and-suspenders with allowedElements).
 */
function TextOnly({ children }: { children?: ReactNode }) {
  return <>{children}</>;
}

function isFencedOrBlockCode(
  className: string | undefined,
  children: ReactNode,
): boolean {
  if (className && /language-/.test(className)) {
    return true;
  }
  const text = Array.isArray(children)
    ? children.map(String).join("")
    : String(children ?? "");
  return text.includes("\n");
}

// Streamdown's Components index signature is loose; keep this map untyped at
// the edges so TextOnly / code overrides stay simple and typecheck cleanly.
const learningNoteComponents = {
  h1: TextOnly,
  h2: TextOnly,
  h3: TextOnly,
  h4: TextOnly,
  h5: TextOnly,
  h6: TextOnly,
  a: TextOnly,
  img: TextOnly,
  table: TextOnly,
  thead: TextOnly,
  tbody: TextOnly,
  tr: TextOnly,
  th: TextOnly,
  td: TextOnly,
  blockquote: TextOnly,
  ol: TextOnly,
  hr: TextOnly,
  pre: TextOnly,
  code: ({
    className,
    children,
  }: {
    className?: string;
    children?: ReactNode;
  }) => {
    if (isFencedOrBlockCode(className, children)) {
      return <>{children}</>;
    }
    return <code className={className}>{children}</code>;
  },
};

export function LearningNoteMarkdown({
  markdown,
  className,
}: {
  markdown: string;
  className?: string;
}) {
  const text = markdown.trim();
  if (!text) {
    return null;
  }

  return (
    <div data-testid="learning-note-markdown" className={className}>
      <Streamdown
        className={cn(
          "text-xs leading-5 text-ink-soft",
          "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
          "[&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-4",
          "[&_p]:my-1",
          "[&_code]:rounded [&_code]:bg-ink/[0.06] [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em]",
          "[&_strong]:font-semibold [&_strong]:text-ink",
        )}
        plugins={plugins}
        allowedElements={[...LEARNING_NOTE_ALLOWED_ELEMENTS]}
        unwrapDisallowed
        skipHtml
        components={learningNoteComponents as never}
      >
        {text}
      </Streamdown>
    </div>
  );
}
