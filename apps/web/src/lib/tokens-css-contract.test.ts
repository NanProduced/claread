/**
 * Plain-text CSS contract for the token layer.
 * Resolved at test time against the tokens.css source so the suite
 * catches any future drift back to a Paper-warm :root or a missing
 * .light mirror.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const TOKENS_PATH = path.resolve(
  __dirname,
  "../../../../packages/design-tokens/src/web/tokens.css",
);

function readTokens(): string {
  return fs.readFileSync(TOKENS_PATH, "utf8");
}

function tokensAfterRoot(): string {
  const source = readTokens();
  const idx = source.indexOf(":root,");
  expect(idx).toBeGreaterThanOrEqual(0);
  return source.slice(idx);
}

function extractRootBlock(source: string): string {
  // The :root, .light block runs until the first .dark { selector.
  const endIdx = source.indexOf("\n.dark {", source.indexOf(":root,"));
  return source.slice(0, endIdx === -1 ? source.length : endIdx);
}

function extractRootSubBlock(source: string): string {
  // The first nested :root, .light { ... } group.
  const start = source.indexOf(":root,");
  const open = source.indexOf("{", start);
  let depth = 1;
  let i = open + 1;
  while (depth > 0 && i < source.length) {
    const ch = source[i];
    if (ch === "{") depth++;
    else if (ch === "}") depth--;
    i++;
  }
  return source.slice(start, i);
}

describe("web design tokens contract", () => {
  it("declares :root and .light as a single token block (no second drift set)", () => {
    const source = tokensAfterRoot();
    expect(source).toMatch(/:root,\s*\.light\s*\{/);
  });

  it("does not declare a .paper selector in :root tokens", () => {
    const source = readTokens();
    expect(source).not.toMatch(/^\.paper[,{]/m);
    expect(source).not.toMatch(/\.theme-preview-surface--paper/);
  });

  it("declares a .dark override block", () => {
    const source = readTokens();
    expect(source).toMatch(/^\.dark\s*\{/m);
  });

  it("keeps the canonical Light neutrals on :root (canvas, surface, ink)", () => {
    const rootBlock = extractRootBlock(readTokens());
    expect(rootBlock).toMatch(/--cl-color-app-canvas:\s*#f7f5f0;/);
    expect(rootBlock).toMatch(/--cl-color-app-surface:\s*#ffffff;/);
    expect(rootBlock).toMatch(/--cl-color-app-ink:\s*#151515;/);
  });

  it("strips Paper-warm rgba from the Light canvas/panel gradients", () => {
    const source = readTokens();
    const rootSubBlock = extractRootSubBlock(source);
    expect(rootSubBlock).not.toContain("rgba(254, 251, 245");
    expect(rootSubBlock).not.toContain("rgba(255, 250, 242");
    expect(rootSubBlock).not.toContain("rgba(249, 244, 234");
  });
});
