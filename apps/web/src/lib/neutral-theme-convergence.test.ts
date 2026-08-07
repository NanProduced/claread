/**
 * Static contract tests for the non-Reader / non-Ask neutral theme
 * convergence (Task 3A). Asserts that functional pages (Daily, Settings,
 * Library, Vocabulary) no longer reference paper semantics, warm gradients,
 * or raw HEX/RGBA, and that the Theme Preferences preview surface uses
 * only named design tokens with a distinct Light/Dark contract.
 *
 * These are source-text assertions — they read files from disk and pattern
 * match, so they catch regressions without rendering anything.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const ROOT = resolve(__dirname, "../..");

function readAppFile(rel: string): string {
  return readFileSync(resolve(ROOT, rel), "utf8");
}

const NEUTRALIZED_FILES: ReadonlyArray<{ rel: string; label: string }> = [
  { rel: "src/app/(public)/daily/page.tsx", label: "Daily index" },
  { rel: "src/app/(public)/daily/[articleId]/page.tsx", label: "Daily article" },
  { rel: "src/app/(private)/app/settings/FeedbackForm.tsx", label: "Settings FeedbackForm" },
  { rel: "src/app/(private)/app/settings/MyFeedbackList.tsx", label: "Settings MyFeedbackList" },
  { rel: "src/app/(private)/app/settings/NicknameEditor.tsx", label: "Settings NicknameEditor" },
  { rel: "src/app/(private)/app/settings/page.tsx", label: "Settings page" },
  { rel: "src/app/(private)/app/settings/feedback/page.tsx", label: "Settings feedback page" },
  { rel: "src/app/(private)/app/settings/ledger/page.tsx", label: "Settings ledger page" },
  { rel: "src/app/(private)/app/library/page.tsx", label: "Library page" },
  { rel: "src/app/(private)/app/vocabulary/page.tsx", label: "Vocabulary page" },
  { rel: "src/app/(private)/app/vocabulary/VocabularyClient.tsx", label: "Vocabulary client" },
  { rel: "src/app/(private)/app/settings/ThemePreferencesSection.tsx", label: "ThemePreferencesSection" },
];

// Patterns that must NOT appear in the neutralized TSX files.
const FORBIDDEN_PATTERNS: ReadonlyArray<{ re: RegExp; label: string }> = [
  { re: /bg-reader-paper/, label: "paper canvas class bg-reader-paper" },
  { re: /text-reader-paper/, label: "paper text class text-reader-paper" },
  { re: /bg-surface-warm/, label: "warm surface class bg-surface-warm" },
  { re: /bg-\[linear-gradient/, label: "inline linear-gradient background" },
  { re: /bg-\[radial-gradient/, label: "inline radial-gradient background" },
  { re: /bg-\[repeating-gradient/, label: "inline repeating-gradient background" },
  { re: /bg-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX background" },
  { re: /text-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX text color" },
  { re: /border-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX border color" },
  { re: /from-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX gradient from-" },
  { re: /via-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX gradient via-" },
  { re: /to-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX gradient to-" },
  { re: /bg-\[rgba\(/, label: "raw RGBA background" },
  { re: /var\(--reader-paper\)/, label: "raw reader-paper CSS var reference" },
  { re: /var\(--surface-warm\)/, label: "raw surface-warm CSS var reference" },
];

describe("neutral theme convergence — functional pages", () => {
  it.each(NEUTRALIZED_FILES)(
    "$label does not reference paper semantics, warm gradients, or raw HEX/RGBA",
    ({ rel }) => {
      const source = readAppFile(rel);
      for (const { re, label } of FORBIDDEN_PATTERNS) {
        expect(
          re.test(source),
          `${rel}: forbidden pattern "${label}" still present`,
        ).toBe(false);
      }
    },
  );
});

// ---------------------------------------------------------------------------
// Theme Preferences preview contract
// ---------------------------------------------------------------------------

const GLOBALS_CSS = readAppFile("src/app/globals.css");
const TOKENS_CSS = readFileSync(
  resolve(__dirname, "../../../../packages/design-tokens/src/web/tokens.css"),
  "utf8",
);

function extractBlock(source: string, selectorRe: RegExp): string {
  const start = source.search(selectorRe);
  if (start === -1) {
    throw new Error(`Selector not found: ${selectorRe}`);
  }
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

function extractThemePreviewSection(source: string): string {
  // Grab from the first .theme-preview-surface rule to the last known
  // theme-preview child rule (__chip). This isolates the preview CSS
  // from Reader / Ask / other sections.
  const start = source.indexOf(".theme-preview-surface {");
  expect(start, "theme-preview-surface section must exist in globals.css").toBeGreaterThanOrEqual(0);
  const endMarker = source.indexOf(".reader-settings-theme-swatch", start);
  const end = endMarker === -1 ? source.length : endMarker;
  return source.slice(start, end);
}

describe("theme preferences preview — semantic token contract", () => {
  const previewSection = extractThemePreviewSection(GLOBALS_CSS);

  it("does not hardcode raw HEX or RGBA in the preview section", () => {
    expect(previewSection).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    expect(previewSection).not.toMatch(/rgba?\(\s*\d/);
  });

  it("does not use gradients in the preview section", () => {
    expect(previewSection).not.toMatch(/linear-gradient/);
    expect(previewSection).not.toMatch(/radial-gradient/);
    expect(previewSection).not.toMatch(/repeating-gradient/);
  });

  it("declares a Light preview modifier referencing light tokens", () => {
    const lightBlock = extractBlock(GLOBALS_CSS, /\.theme-preview-surface--light\s*\{/);
    expect(lightBlock).toMatch(/--theme-preview-surface:\s*var\(--theme-preview-light-surface\)/);
    expect(lightBlock).toMatch(/--theme-preview-line:\s*var\(--theme-preview-light-line\)/);
    expect(lightBlock).toMatch(/--theme-preview-chip-surface:\s*var\(--theme-preview-light-chip-surface\)/);
  });

  it("declares a Dark preview modifier referencing dark tokens", () => {
    const darkBlock = extractBlock(GLOBALS_CSS, /\.theme-preview-surface--dark\s*\{/);
    expect(darkBlock).toMatch(/--theme-preview-surface:\s*var\(--theme-preview-dark-surface\)/);
    expect(darkBlock).toMatch(/--theme-preview-line:\s*var\(--theme-preview-dark-line\)/);
    expect(darkBlock).toMatch(/--theme-preview-chip-surface:\s*var\(--theme-preview-dark-chip-surface\)/);
  });

  it("defines named Light and Dark preview tokens in tokens.css", () => {
    expect(TOKENS_CSS).toMatch(/--theme-preview-light-surface:\s*#[0-9a-fA-F]{3,8};/);
    expect(TOKENS_CSS).toMatch(/--theme-preview-dark-surface:\s*#[0-9a-fA-F]{3,8};/);
  });

  it("keeps Light and Dark preview surfaces visually distinct", () => {
    const rootBlock = extractBlock(TOKENS_CSS, /:root,\s*\.light\s*\{/);
    const lightMatch = rootBlock.match(/--theme-preview-light-surface:\s*(#[0-9a-fA-F]{3,8});/);
    const darkMatch = rootBlock.match(/--theme-preview-dark-surface:\s*(#[0-9a-fA-F]{3,8});/);
    expect(lightMatch, "Light preview surface token must be defined").toBeTruthy();
    expect(darkMatch, "Dark preview surface token must be defined").toBeTruthy();
    expect(lightMatch![1]).not.toBe(darkMatch![1]);
  });
});
