/**
 * Syntax-aware task-history governance for tracked TypeScript sources.
 *
 * The full-repository assertion intentionally stays RED during rolling cleanup.
 * Self-tests, parse checks, comment cross-audit, and changed-scope checks must
 * be GREEN. There is no baseline residual ceiling.
 */

import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const GUARD_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = execFileSync(
  "git",
  ["-C", GUARD_DIRECTORY, "rev-parse", "--show-toplevel"],
  { encoding: "utf8" },
).trim();
const TYPESCRIPT_RUNTIME =
  process.env.CLAREAD_TYPESCRIPT_PATH ??
  resolve(REPO_ROOT, "apps/web/node_modules/typescript/lib/typescript.js");
const ts = createRequire(import.meta.url)(TYPESCRIPT_RUNTIME);
const EXPECTED_ROOTS = [
  "services/api",
  "evals",
  "apps/web",
  "apps/miniprogram",
  "apps/directus",
  "packages",
  "infra",
] as const;
const GUARD_SELF =
  "apps/web/src/lib/reader-orchestration/task-number-naming-guard.test.ts";

const KEEP_WIRE_TOKENS = [
  "ask_retry_contract_r5",
  "CLAREAD_R4_A3_BBC_RECORD_ID",
  "CLAREAD_R4_A3_DATASET_DIR",
  "CLAREAD_R4_A3_MAX_REQUESTS",
  "CLAREAD_R4_A3_MAX_TOKENS",
  "CLAREAD_R4_A3_PRIOR_RUN_ID",
  "CLAREAD_R4_A3_PRO_PROFILE",
  "CLAREAD_R4_A3_RUN",
  "CLAREAD_R4_A3_RUN_ID",
  "CLAREAD_R4_A3_RUNS_DIR",
  "CLAREAD_R4_A3_THINKING_VIA_PROFILE",
  "d4-p1-translation-worker",
  "d4-p2-translation-parsed",
  "d5-v3-vocabulary-worker",
  "d5-v6-grammar-worker",
  "d6_i3b_structured_source_v1",
  "full_snapshot_until_pux_r4",
  "r4-a3-dataset-v1",
  "r4-a4-2r2",
  "r4-a4-2r3",
  "reader_d5_attribution_schema_drift",
  "reader_d6_anchor_migration_missing",
  "reader-record-ask-r4-a3",
  "reading_base_builder_d3_p2_v1",
  "t1-1-translation-batch-worker",
  "t1-1-vocabulary-batch-worker",
  "zplus_grammar_bundle_v1",
] as const;
const KEEP_FIXTURE_TOKENS = [
  "d6_i3b_plain_text_markdown_v1",
  "r14_complex",
] as const;

const LABEL_RE =
  /(?<![A-Za-z0-9])(?:Task|Phase|Wave)\s+\d+(?:\.\d+)?[A-Za-z]?|(?<![A-Za-z0-9])(?:R\d+(?:[._-][A-Za-z0-9]+)*|D\d+(?:[._-][A-Za-z0-9]+)*|P\d+[A-Z]?(?:[._-][A-Za-z0-9]+)*|T\d+(?:\.\d+)?[a-z]?(?:[._-][A-Za-z0-9]+)*|A\d+(?:[._-][A-Za-z0-9]+)*|B\d+(?:[._-][A-Za-z0-9]+)*|C[123]|S\d+(?:\.\d+)?|U\d+)(?![A-Za-z0-9])/g;
const MACHINE_RE =
  /(?<![A-Za-z0-9_])(?:d\d+[-_]i\d+[a-z]?|d\d+[-_][pv]\d+|r\d+[-_][ab]\d+|t\d+[-_]\d+)(?:[-_][a-z0-9]+)+(?![A-Za-z0-9_])/g;
const SPECIAL_IDENTIFIER_RE =
  /(?:ReaderRecordAskR4A3|load_r4_a3_dataset|allow_r4_a4|allow_r4_b1|task_label|_P1(?:D|F|G)(?:_R1)?_)/g;
const HEX_COLOR_RE = /#[0-9A-Fa-f]{6}\b/g;
const ISSUE_RE = /#\d+\b/g;
const CEFR_RE = /(?<![A-Za-z0-9])(?:A1|A2|B1|B2|C1|C2)(?![A-Za-z0-9])/g;
const ARTICLE_RAG_RE = /(?<![A-Za-z0-9])B[123](?![A-Za-z0-9])/g;
const SEGMENT_TOKEN_RE =
  /(?<=[_-])(?:A|B|C|D|P|R|S|T|U)\d{1,3}(?:\.\d+)?[A-Za-z]?/g;
const D3_READING_BASE_RE = /(?<![a-z0-9])d3-p[14](?![a-z0-9])/gi;
const TRACKED_FILENAME_RE = /(?:^|_)d\d+(?:_|\.|$)/i;

// Accepted semantic KEEP ratchet. This is not a residual baseline ceiling.
const ACCEPTED_SEMANTIC_KEEP_COUNTS = new Map<string, number>([
  ["apps/directus/scripts/sync-llm-config-metadata.mjs\u001ftypescript_string_literal\u001fD97706", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_identifier\u001fA2", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_identifier\u001fB1", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_identifier\u001fB2", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_identifier\u001fC1", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_string_literal\u001fA2", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_string_literal\u001fB1", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_string_literal\u001fB2", 1],
  ["apps/miniprogram/src/components/DailyReaderHeader/index.tsx\u001ftypescript_string_literal\u001fC1", 1],
  ["apps/miniprogram/src/components/LucideIcon/index.tsx\u001ftypescript_string_literal\u001fB45309", 1],
  ["apps/miniprogram/src/packageA/credit-detail/index.tsx\u001ftypescript_string_literal\u001fD97706", 2],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_identifier\u001fA2", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_identifier\u001fB1", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_identifier\u001fB2", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_identifier\u001fC1", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_string_literal\u001fA2", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_string_literal\u001fB1", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_string_literal\u001fB2", 1],
  ["apps/miniprogram/src/packageB/daily-reader-archive/index.tsx\u001ftypescript_string_literal\u001fC1", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_identifier\u001fA2", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_identifier\u001fB1", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_identifier\u001fB2", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_identifier\u001fC1", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_string_literal\u001fA2", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_string_literal\u001fB1", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_string_literal\u001fB2", 1],
  ["apps/miniprogram/src/pages/home/index.tsx\u001ftypescript_string_literal\u001fC1", 1],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fA1", 3],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fA2", 1],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fA3", 1],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fP1", 7],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fP2", 7],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fP3", 6],
  ["apps/web/src/app/(private)/app/library/reading-record-status.test.tsx\u001ftypescript_string_literal\u001fP4", 6],
  ["apps/web/src/app/(private)/app/read/AnalyzeSubmitForm.editor-integration.test.tsx\u001ftypescript_string_literal\u001fR1", 1],
  ["apps/web/src/app/(private)/app/read/page.test.tsx\u001ftypescript_string_literal\u001fB1", 1],
  ["apps/web/src/app/(private)/app/read/page.test.tsx\u001ftypescript_string_literal\u001fB2", 1],
  ["apps/web/src/components/ai-elements/streamdown.test.ts\u001ftypescript_string_literal\u001fC1", 2],
  ["apps/web/src/components/ai-elements/streamdown.test.ts\u001ftypescript_string_literal\u001fC2", 2],
  ["apps/web/src/components/product-page/hero/HeroCopy.tsx\u001ftypescript_string_literal\u001fS57", 1],
  ["apps/web/src/components/product-page/hero/HeroCopy.tsx\u001ftypescript_string_literal\u001fS90", 1],
  ["apps/web/src/components/product-page/ProductStickerWall.tsx\u001ftypescript_string_literal\u001fB45309", 1],
  ["apps/web/src/components/reader/plate/ReaderRecordNavigationRail.test.tsx\u001ftypescript_string_literal\u001fU1", 1],
  ["apps/web/src/components/reader/plate/ReaderRecordNavigationRail.test.tsx\u001ftypescript_string_literal\u001fU2", 1],
  ["apps/web/src/components/reader/plate/ReaderRecordNavigationRail.test.tsx\u001ftypescript_string_literal\u001fU3", 1],
  ["apps/web/src/lib/reader-plate/markdown/__tests__/fixtures.ts\u001ftypescript_string_literal\u001fr14_complex", 1],
  ["apps/web/src/lib/reader-plate/projection/__tests__/structured-source-renderer.test.tsx\u001ftypescript_string_literal\u001fr14_complex", 2],
  ["apps/web/src/lib/reader-plate/projection/reader-record-anchor-draft.test.ts\u001ftypescript_identifier\u001fS1", 27],
  ["apps/web/src/lib/reader-plate/projection/reader-record-anchor-draft.test.ts\u001ftypescript_identifier\u001fS2", 11],
  ["apps/web/src/lib/reader-plate/projection/reader-record-anchor-draft.test.ts\u001ftypescript_identifier\u001fU1", 42],
  ["apps/web/src/lib/reader-plate/projection/reader-record-anchor-draft.test.ts\u001ftypescript_identifier\u001fU2", 20],
  ["apps/web/src/lib/reader-plate/projection/reader-record-plate-document.test.ts\u001ftypescript_string_literal\u001fR1", 1],
  ["apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts\u001ftypescript_comment\u001fB3", 1],
  ["apps/web/src/lib/source-callout/source-callout-roundtrip.test.ts\u001ftypescript_string_literal\u001fA1", 1],
  ["apps/web/src/lib/source-callout/source-callout-roundtrip.test.ts\u001ftypescript_string_literal\u001fA2", 1],
]);

interface SyntaxItem {
  path: string;
  kind:
    | "typescript_comment"
    | "typescript_string_literal"
    | "typescript_identifier"
    | "tracked_filename";
  line: number;
  text: string;
  purpose: string;
}

interface GuardHit extends SyntaxItem {
  token: string;
}

interface ParseResult {
  path: string;
  items: SyntaxItem[];
  diagnostics: string[];
  scannerCommentRanges: Set<string>;
  auditedCommentRanges: Set<string>;
}

interface CompilerSource {
  parseDiagnostics: Array<{
    messageText: string | { messageText: string };
  }>;
  getLineAndCharacterOfPosition(position: number): { line: number };
}

interface CompilerNode {
  parent: CompilerNode;
  kind: number;
  pos: number;
  end: number;
  name: CompilerNode;
  expression: CompilerNode;
  text: string;
  getStart(source: CompilerSource): number;
  getText(source: CompilerSource): string;
  getSourceFile(): CompilerSource;
}

function trackedPaths(): string[] {
  return execFileSync(
    "git",
    ["-C", REPO_ROOT, "ls-files", "-z", "--", ...EXPECTED_ROOTS],
    { encoding: "utf8" },
  )
    .split("\0")
    .filter(Boolean);
}

function stripExact(text: string, tokens: readonly string[]): string {
  let remainder = text;
  for (const token of tokens) {
    remainder = remainder.split(token).join("");
  }
  return remainder;
}

function stripContextualKeeps(text: string, purpose: string): string {
  let remainder = text
    .replace(HEX_COLOR_RE, "")
    .replace(ISSUE_RE, "");
  remainder = stripExact(remainder, KEEP_WIRE_TOKENS);
  remainder = stripExact(remainder, KEEP_FIXTURE_TOKENS);

  const context = text + " " + purpose;
  if (/\b(?:CEFR|difficulty|reading level)\b/i.test(context)) {
    remainder = remainder.replace(CEFR_RE, "");
  }
  if (/\bArticle RAG\b/i.test(context)) {
    remainder = remainder.replace(ARTICLE_RAG_RE, "");
  }
  if (
    /(?:SEGMENT_ID|segment_id|unit_id|sentence_id|summary_id|chunk_id|block_id|fixture_identity)/i.test(
      context,
    )
  ) {
    remainder = remainder.replace(SEGMENT_TOKEN_RE, "");
  }
  if (context.includes("reading_bases")) {
    remainder = remainder.replace(D3_READING_BASE_RE, "");
  }
  return remainder;
}

function taskTokens(
  text: string,
  purpose = "",
  kind: SyntaxItem["kind"] = "typescript_string_literal",
): string[] {
  if (kind === "tracked_filename" && TRACKED_FILENAME_RE.test(text)) {
    return [text];
  }

  const remainder = stripContextualKeeps(text, purpose);
  const matches: Array<{ index: number; token: string }> = [];
  for (const pattern of [
    LABEL_RE,
    MACHINE_RE,
    SPECIAL_IDENTIFIER_RE,
  ]) {
    pattern.lastIndex = 0;
    for (const match of remainder.matchAll(pattern)) {
      matches.push({ index: match.index, token: match[0] });
    }
  }
  const seen = new Set<string>();
  return matches
    .sort((left, right) => left.index - right.index)
    .map(({ token }) => token)
    .filter((token) => {
      if (seen.has(token)) return false;
      seen.add(token);
      return true;
    });
}

function scriptKind(path: string): number {
  if (path.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (path.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (path.endsWith(".js") || path.endsWith(".mjs")) return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function lineOf(source: CompilerSource, position: number): number {
  return source.getLineAndCharacterOfPosition(position).line + 1;
}

function nodePurpose(node: CompilerNode): string {
  const parent = node.parent;
  if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
    return "variable:" + parent.name.text;
  }
  if (ts.isPropertyAssignment(parent)) {
    return "property:" + parent.name.getText(parent.getSourceFile());
  }
  if (ts.isJsxAttribute(parent)) {
    return "jsx-attribute:" + parent.name.getText(parent.getSourceFile());
  }
  if (ts.isCallExpression(parent)) {
    return "call:" + parent.expression.getText(parent.getSourceFile());
  }
  return ts.SyntaxKind[parent.kind] ?? "unknown";
}

function isStringNode(node: CompilerNode): boolean {
  return (
    ts.isStringLiteral(node) ||
    ts.isNoSubstitutionTemplateLiteral(node) ||
    node.kind === ts.SyntaxKind.TemplateHead ||
    node.kind === ts.SyntaxKind.TemplateMiddle ||
    node.kind === ts.SyntaxKind.TemplateTail ||
    node.kind === ts.SyntaxKind.JsxText
  );
}

function stringNodeText(node: CompilerNode, source: CompilerSource): string {
  if (
    ts.isStringLiteral(node) ||
    ts.isNoSubstitutionTemplateLiteral(node) ||
    node.kind === ts.SyntaxKind.TemplateHead ||
    node.kind === ts.SyntaxKind.TemplateMiddle ||
    node.kind === ts.SyntaxKind.TemplateTail
  ) {
    return node.text;
  }
  return node.getText(source);
}

function scanTypeScript(relativePath: string): ParseResult {
  const text = readFileSync(resolve(REPO_ROOT, relativePath), "utf8");
  const source = ts.createSourceFile(
    relativePath,
    text,
    ts.ScriptTarget.Latest,
    true,
    scriptKind(relativePath),
  );
  const diagnostics = source.parseDiagnostics.map(
    (diagnostic: { messageText: string | { messageText: string } }) =>
      typeof diagnostic.messageText === "string"
        ? diagnostic.messageText
        : diagnostic.messageText.messageText,
  );
  const items: SyntaxItem[] = [];
  const scannerCommentRanges = new Set<string>();
  const auditedCommentRanges = new Set<string>();

  const visit = (node: CompilerNode): void => {
    for (const range of ts.getLeadingCommentRanges(text, node.pos) ?? []) {
      const key = range.pos + ":" + range.end;
      auditedCommentRanges.add(key);
    }
    for (const range of ts.getTrailingCommentRanges(text, node.end) ?? []) {
      const key = range.pos + ":" + range.end;
      auditedCommentRanges.add(key);
    }

    if (ts.isIdentifier(node)) {
      items.push({
        path: relativePath,
        kind: "typescript_identifier",
        line: lineOf(source, node.getStart(source)),
        text: node.text,
        purpose: nodePurpose(node),
      });
    } else if (isStringNode(node)) {
      items.push({
        path: relativePath,
        kind: "typescript_string_literal",
        line: lineOf(source, node.getStart(source)),
        text: stringNodeText(node, source),
        purpose: nodePurpose(node),
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(source);

  const scanner = ts.createScanner(
    ts.ScriptTarget.Latest,
    false,
    relativePath.endsWith(".tsx") || relativePath.endsWith(".jsx")
      ? ts.LanguageVariant.JSX
      : ts.LanguageVariant.Standard,
    text,
  );
  for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    if (
      token !== ts.SyntaxKind.SingleLineCommentTrivia &&
      token !== ts.SyntaxKind.MultiLineCommentTrivia
    ) {
      continue;
    }
    const position = scanner.getTokenPos();
    const end = scanner.getTextPos();
    scannerCommentRanges.add(position + ":" + end);
    const firstLine = lineOf(source, position);
    for (const [offset, line] of text.slice(position, end).split(/\r?\n/).entries()) {
      if (!line.trim()) continue;
      items.push({
        path: relativePath,
        kind: "typescript_comment",
        line: firstLine + offset,
        text: line,
        purpose: "comment",
      });
    }
  }

  return {
    path: relativePath,
    items,
    diagnostics,
    scannerCommentRanges,
    auditedCommentRanges,
  };
}

function damageHits(items: readonly SyntaxItem[]): string[] {
  const damage: string[] = [];
  for (const item of items) {
    if (/\b(?:pre-|LP-)\s*(?:$|[,.;:])/.test(item.text)) {
      damage.push(item.path + ":" + item.line + ":dangling-prefix");
    }
    if (item.text.includes(String.fromCharCode(96).repeat(4))) {
      damage.push(item.path + ":" + item.line + ":empty-inline-code");
    }
    if (/:[A-Za-z][\w.-]*:\s+\x60/.test(item.text)) {
      damage.push(item.path + ":" + item.line + ":broken-sphinx-role");
    }
    if (/\x60[A-Za-z]/.test(item.text)) {
      damage.push(item.path + ":" + item.line + ":role-adhesion");
    }
  }
  return damage;
}

const TRACKED_PATHS = trackedPaths();
const TYPESCRIPT_PATHS = TRACKED_PATHS.filter(
  (path) =>
    [".ts", ".tsx", ".js", ".jsx", ".mjs"].includes(
      extname(path).toLowerCase(),
    ) &&
    // Working-tree deletions (pending, unstaged removals) still appear
    // in git ls-files output; only files that exist on disk are
    // scannable.
    existsSync(resolve(REPO_ROOT, path)),
);
const PARSE_RESULTS = TYPESCRIPT_PATHS.filter((path) => path !== GUARD_SELF).map(
  scanTypeScript,
);
const ALL_ITEMS = PARSE_RESULTS.flatMap((result) => result.items);
for (const relativePath of TYPESCRIPT_PATHS) {
  const name = relativePath.split("/").at(-1) ?? "";
  if (taskTokens(name, "", "tracked_filename").length > 0) {
    ALL_ITEMS.push({
      path: relativePath,
      kind: "tracked_filename",
      line: 0,
      text: name,
      purpose: "filename",
    });
  }
}

function residualHits(items: readonly SyntaxItem[]): GuardHit[] {
  const acceptedSeen = new Map<string, number>();
  const seenOccurrences = new Set<string>();
  const hits: GuardHit[] = [];
  for (const item of items) {
    for (const token of taskTokens(item.text, item.purpose, item.kind)) {
      const occurrence = [item.path, item.kind, item.line, token].join("\u001f");
      if (seenOccurrences.has(occurrence)) continue;
      seenOccurrences.add(occurrence);
      const key = [item.path, item.kind, token].join("\u001f");
      const seen = acceptedSeen.get(key) ?? 0;
      if (seen < (ACCEPTED_SEMANTIC_KEEP_COUNTS.get(key) ?? 0)) {
        acceptedSeen.set(key, seen + 1);
        continue;
      }
      hits.push({ ...item, token });
    }
  }
  return hits;
}

describe("Syntax-aware task-history governance", () => {
  it("scans every expected tracked root and a non-empty TypeScript bucket", () => {
    for (const root of EXPECTED_ROOTS) {
      expect(
        TRACKED_PATHS.some(
          (path) => path === root || path.startsWith(root + "/"),
        ),
        root,
      ).toBe(true);
    }
    expect(TYPESCRIPT_PATHS.length).toBeGreaterThan(0);
  });

  it("parses every tracked TypeScript source with TypeScript 5.9.3", () => {
    expect(ts.version).toBe("5.9.3");
    // Ratchet count: tracked TS/TSX/JS/JSX/MJS sources under the expected
    // roots that exist on disk, measured at runtime via git ls-files.
    // Bump this number whenever tracked TypeScript files are added or
    // removed (re-measure with the guard run, never hand-count).
    expect(TYPESCRIPT_PATHS).toHaveLength(758);
    expect(PARSE_RESULTS.length).toBeGreaterThan(0);
    expect(
      PARSE_RESULTS.flatMap((result) =>
        result.diagnostics.map(
          (diagnostic) => result.path + ": " + diagnostic,
        ),
      ),
    ).toEqual([]);
  });

  it("cross-audits compiler comment ranges against scanner trivia", () => {
    const counts = PARSE_RESULTS.reduce(
      (total, result) => {
        for (const range of result.scannerCommentRanges) {
          total[
            result.auditedCommentRanges.has(range) ? "shared" : "scannerOnly"
          ] += 1;
        }
        for (const range of result.auditedCommentRanges) {
          if (!result.scannerCommentRanges.has(range)) total.auditOnly += 1;
        }
        return total;
      },
      { shared: 0, scannerOnly: 0, auditOnly: 0 },
    );
    expect(counts.shared).toBeGreaterThan(0);
    expect(counts.scannerOnly).toBeGreaterThan(0);
    expect(counts.auditOnly).toBeGreaterThan(0);
  });

  it("enforces mandatory ordinary-string boundary samples", () => {
    const mustFail = [
      "Phase " + "2 in process prose",
      "Task " + "5 task-label output",
      "R" + "4-A3 cleanup",
      "D" + "6-I3Q",
      "ReaderRecordAsk" + "R4A3",
      "load_r4_a3_dataset and " + "task_label",
    ];
    const tick = String.fromCharCode(96);
    const mustPass = [
      "CEFR A1/A2/B1/B2/C1/C2 business fields",
      "SEGMENT_ID_U1_S1 and related fixture identities",
      "Article RAG B1/B2/B3 chunk identities",
      "d3-p1/d3-p4 reading_bases versions",
      "d6_i3b_plain_text_markdown_v1",
      "r14_complex",
      "#A66445 and #B45309 colors",
      ":class:" + tick + "ReaderRecord" + tick,
      "issue #1234 and issue #5678",
    ];
    for (const sample of mustFail) {
      expect(taskTokens(sample), sample).not.toEqual([]);
    }
    for (const sample of mustPass) {
      expect(taskTokens(sample), sample).toEqual([]);
    }
  });

  it("rescans the same syntax item after stripping an exact wire token", () => {
    const wire = KEEP_WIRE_TOKENS[0];
    expect(taskTokens(wire)).toEqual([]);
    expect(taskTokens(wire + "; " + "R" + "7 cleanup")).toEqual(["R7"]);
  });

  it("detects mechanical damage in changed syntax items", () => {
    const clean: SyntaxItem = {
      path: "sample.ts",
      kind: "typescript_comment",
      line: 1,
      text: "Business behavior.",
      purpose: "comment",
    };
    expect(damageHits([clean])).toEqual([]);
    expect(
      damageHits([{ ...clean, text: "dangling " + "pre" + "-;" }]),
    ).not.toEqual([]);
  });

  it("keeps the changed Web guard free of mechanical damage", () => {
    expect(damageHits(scanTypeScript(GUARD_SELF).items)).toEqual([]);
  });

  it("has no task-history residuals after the accepted rolling cleanup", () => {
    const hits = residualHits(ALL_ITEMS);
    expect(
      hits
        .slice(0, 80)
        .map(
          (hit) =>
            hit.path +
            ":" +
            hit.line +
            ":" +
            hit.kind +
            ":" +
            hit.token +
            ":" +
            hit.purpose,
        ),
      "expected RED until accepted residual inventory reaches zero; " +
        "total=" +
        hits.length,
    ).toEqual([]);
  });
});
