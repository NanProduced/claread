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
function extractDarkBlock(source: string): string {
  const start = source.indexOf(".dark {");
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

function tokenHex(block: string, token: string): string {
  const match = block.match(new RegExp(`--${token}:\\s*(#[0-9a-fA-F]{6});`));
  expect(match, `Expected --${token} to be a six-digit hex color`).toBeTruthy();
  return match?.[1] ?? "#000000";
}

function relativeLuminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground: string, background: string): number {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)].sort(
    (a, b) => b - a,
  );
  return (lighter + 0.05) / (darker + 0.05);
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
    expect(rootBlock).toMatch(/--cl-color-app-canvas:\s*#f8f8f8;/);
    expect(rootBlock).toMatch(/--cl-color-app-surface:\s*#ffffff;/);
    expect(rootBlock).toMatch(/--cl-color-app-ink:\s*#151515;/);
  });

  it("keeps shadcn muted text and input boundaries contrast-safe in both themes", () => {
    const source = readTokens();
    const themes = [
      { name: "Light", block: extractRootSubBlock(source) },
      { name: "Dark", block: extractDarkBlock(source) },
    ];

    for (const theme of themes) {
      const canvas = tokenHex(theme.block, "cl-color-app-canvas");
      const surface = tokenHex(theme.block, "cl-color-app-surface");
      const mutedForeground = tokenHex(theme.block, "cl-color-app-muted");
      const input = tokenHex(theme.block, "cl-color-app-input");

      expect(
        contrastRatio(mutedForeground, canvas),
        `${theme.name} muted foreground must meet WCAG text contrast on canvas`,
      ).toBeGreaterThanOrEqual(4.5);
      expect(
        contrastRatio(input, surface),
        `${theme.name} input boundary must meet WCAG non-text contrast on surface`,
      ).toBeGreaterThanOrEqual(3);
    }

    expect(source).toMatch(/--cl-color-input:\s*var\(--cl-color-app-input\);/);
    expect(source).toMatch(/--text-secondary:\s*var\(--muted-foreground\);/);
  });
  it("keeps quiet hover and persistent current navigation surfaces distinct in both themes", () => {
    const source = readTokens();
    const rootBlock = extractRootSubBlock(source);
    const darkBlock = extractDarkBlock(source);

    expect(rootBlock).toMatch(/--app-control-quiet:\s*#e8e8e8;/);
    expect(rootBlock).toMatch(/--app-control-current:\s*#dedede;/);
    expect(darkBlock).toMatch(/--app-control-quiet:\s*#242424;/);
    expect(darkBlock).toMatch(/--app-control-current:\s*#303030;/);
  });

  it("strips Paper-warm rgba from the Light canvas/panel gradients", () => {
    const source = readTokens();
    const rootSubBlock = extractRootSubBlock(source);
    expect(rootSubBlock).not.toContain("rgba(254, 251, 245");
    expect(rootSubBlock).not.toContain("rgba(255, 250, 242");
    expect(rootSubBlock).not.toContain("rgba(249, 244, 234");
  });
  it("keeps Reader prose and analysis-label contrast safe in both themes", () => {
    const source = readTokens();
    const themes = [
      { name: "Light", block: extractRootSubBlock(source) },
      { name: "Dark", block: extractDarkBlock(source) },
    ];
    const analysisLabels = [
      "cl-color-vocab-amber",
      "cl-color-phrase-lavender",
      "cl-color-context-blue",
      "cl-color-grammar-violet",
      "cl-color-structure-green",
    ];

    for (const theme of themes) {
      const stage = tokenHex(theme.block, "cl-color-reader-stage");
      const readingInk = tokenHex(theme.block, "cl-color-reader-reading-ink");
      const readingInkStrong = tokenHex(theme.block, "cl-color-reader-reading-ink-strong");
      const readingMuted = tokenHex(theme.block, "cl-color-reader-reading-muted");

      expect(
        contrastRatio(readingInk, stage),
        `${theme.name} Reader prose must meet enhanced contrast on its stage`,
      ).toBeGreaterThanOrEqual(7);
      expect(
        contrastRatio(readingInkStrong, stage),
        `${theme.name} Reader prose emphasis must meet enhanced contrast on its stage`,
      ).toBeGreaterThanOrEqual(7);
      expect(
        contrastRatio(readingMuted, stage),
        `${theme.name} Reader translation and auxiliary copy must meet WCAG text contrast`,
      ).toBeGreaterThanOrEqual(4.5);

      for (const label of analysisLabels) {
        expect(
          contrastRatio(tokenHex(theme.block, label), stage),
          `${theme.name} ${label} must be safe when used as a small analysis label`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
