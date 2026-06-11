"use client";

const MERMAID_FENCE_REGEX = /```mermaid([^\n]*)\n([\s\S]*?)```/gi;

function normalizeLabelQuotes(content: string) {
  const leadingWhitespace = content.match(/^\s*/)?.[0] ?? "";
  const trailingWhitespace = content.match(/\s*$/)?.[0] ?? "";
  let core = content.slice(
    leadingWhitespace.length,
    content.length - trailingWhitespace.length,
  );

  // Mermaid often breaks when LLMs emit wrapper quotes plus nested quotes
  // inside node labels. Keep the text, but remove parser-hostile straight quotes.
  if (core.startsWith("\"") && core.endsWith("\"") && core.length >= 2) {
    core = core.slice(1, -1);
  }

  if (!core.includes("\"")) {
    return `${leadingWhitespace}${core}${trailingWhitespace}`;
  }

  let shouldUseOpenQuote = true;
  const normalized = core.replace(/"/g, () => {
    const quote = shouldUseOpenQuote ? "“" : "”";
    shouldUseOpenQuote = !shouldUseOpenQuote;
    return quote;
  });

  return `${leadingWhitespace}${normalized}${trailingWhitespace}`;
}

function sanitizeMermaidLine(line: string) {
  if (!line.includes("\"") || /^\s*%%\{/.test(line)) {
    return line;
  }

  let output = line;
  const patterns = [/\[[^[\]]*]/g, /\([^()]*\)/g, /\{[^{}]*}/g];

  for (const pattern of patterns) {
    output = output.replace(pattern, (segment) => {
      const open = segment[0];
      const close = segment.at(-1) ?? "";
      const content = segment.slice(1, -1);
      return `${open}${normalizeLabelQuotes(content)}${close}`;
    });
  }

  return output;
}

export function sanitizeMermaidSource(source: string) {
  return source
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => sanitizeMermaidLine(line))
    .join("\n");
}

export function sanitizeMarkdownForStreamdown(markdown: string) {
  if (!markdown.includes("```mermaid")) {
    return markdown;
  }

  return markdown.replace(
    MERMAID_FENCE_REGEX,
    (_match, fenceMeta: string, mermaidSource: string) =>
      `\`\`\`mermaid${fenceMeta}\n${sanitizeMermaidSource(mermaidSource)}\`\`\``,
  );
}
