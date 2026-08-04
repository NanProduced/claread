/**
 * Static contract tests for Task 3B/3C: Reader / Ask neutral theme convergence.
 * Asserts that Reader and Ask components no longer reference paper semantics,
 * warm gradients, raw HEX/RGBA, or dark: color patches.
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

// Files cleaned in Task 3B (non-conflict) and Task 3C (formerly conflict).
const CONVERGED_FILES: ReadonlyArray<{ rel: string; label: string }> = [
  // Reader settings
  { rel: "src/components/reader/settings/ReaderSettingsPanel.tsx", label: "ReaderSettingsPanel" },
  // Reader component layer
  { rel: "src/components/reader/FavoriteButton.tsx", label: "FavoriteButton (component)" },
  { rel: "src/components/reader/ReaderGlobalFeedbackPrompt.tsx", label: "ReaderGlobalFeedbackPrompt" },
  { rel: "src/components/reader/ReaderContextPanel.tsx", label: "ReaderContextPanel" },
  { rel: "src/components/reader/AnnotationGutter.tsx", label: "AnnotationGutter" },
  { rel: "src/components/reader/interaction.ts", label: "interaction" },
  // Reader plate layer
  { rel: "src/components/reader/plate/InlineCommentPanel.tsx", label: "InlineCommentPanel" },
  { rel: "src/components/reader/plate/ReaderRecordNavigationRail.tsx", label: "ReaderRecordNavigationRail" },
  // Reader plate-ui-adapter / dictionary
  { rel: "src/components/reader/plate-ui-adapter/toolbar.tsx", label: "plate-ui-adapter toolbar" },
  { rel: "src/components/reader/dictionary/ReaderDictionaryDetailPanel.tsx", label: "ReaderDictionaryDetailPanel" },
  { rel: "src/components/reader/dictionary/ReaderStructuredInspectCard.tsx", label: "ReaderStructuredInspectCard" },
  { rel: "src/components/reader/dictionary/ReaderDictionaryRecentStrip.tsx", label: "ReaderDictionaryRecentStrip" },
  // Reader ask-chat
  { rel: "src/components/reader/ask-chat/ConversationShell.tsx", label: "ConversationShell" },
  { rel: "src/components/reader/ask-chat/ArticleRagCitationList.tsx", label: "ArticleRagCitationList" },
  // Reader / Ask page layer
  { rel: "src/app/(private)/app/reader/[recordId]/FavoriteButton.tsx", label: "FavoriteButton (page)" },
  { rel: "src/app/(private)/app/read/page.tsx", label: "read page" },
  { rel: "src/app/(private)/app/read/CandidateConfirmDialog.tsx", label: "CandidateConfirmDialog" },
  // Task 3C — formerly conflict files, now converged
  { rel: "src/app/(private)/app/read/AnalyzeSubmitForm.tsx", label: "AnalyzeSubmitForm" },
  { rel: "src/components/reader/AiWorkspacePanel.tsx", label: "AiWorkspacePanel" },
  { rel: "src/components/reader/plate/ReaderRecordPlateSurface.tsx", label: "ReaderRecordPlateSurface" },
];

// Patterns that must NOT appear in the converged TSX files.
const FORBIDDEN_PATTERNS: ReadonlyArray<{ re: RegExp; label: string }> = [
  { re: /bg-reader-paper/, label: "paper canvas class bg-reader-paper" },
  { re: /bg-surface-warm/, label: "warm surface class bg-surface-warm" },
  { re: /bg-paper-warm/, label: "non-standard bg-paper-warm class" },
  { re: /bg-\[linear-gradient/, label: "inline linear-gradient background" },
  { re: /bg-\[radial-gradient/, label: "inline radial-gradient background" },
  { re: /bg-\[repeating-gradient/, label: "inline repeating-gradient background" },
  { re: /bg-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX background" },
  { re: /text-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX text color" },
  { re: /border-\[#[0-9a-fA-F]{3,8}\]/, label: "raw HEX border color" },
  { re: /bg-\[rgba\(/, label: "raw RGBA background" },
  { re: /var\(--reading-paper-surface\)/, label: "legacy reading-paper-surface CSS var" },
  { re: /var\(--surface-warm\)/, label: "raw surface-warm CSS var reference" },
  { re: /shadow-\[0_[0-9]+px_[0-9]+px_rgba\(/, label: "raw RGBA shadow" },
  { re: /shadow-\[inset_0_[0-9]+px_[0-9]+px_rgba\(/, label: "raw RGBA inset shadow" },
  { re: /drop-shadow-\[0_[0-9]+px_[0-9]+px_rgba\(/, label: "raw RGBA drop-shadow" },
  // dark: color patches that bypass the semantic token system
  { re: /dark:bg-\[#/, label: "dark: raw HEX background patch" },
  { re: /dark:text-\[#/, label: "dark: raw HEX text patch" },
  { re: /dark:bg-zinc/, label: "dark: zinc background patch" },
  { re: /dark:bg-muted/, label: "dark: muted background patch" },
  { re: /dark:hover:bg-zinc/, label: "dark: hover zinc patch" },
  { re: /dark:hover:bg-muted/, label: "dark: hover muted patch" },
  // Task 3C — broader patterns for formerly conflict files
  { re: /linear-gradient\(/, label: "linear-gradient() in any context" },
  { re: /radial-gradient\(/, label: "radial-gradient() in any context" },
  { re: /rgba\(/, label: "raw rgba() color value" },
  { re: /var\(--reader-paper\)/, label: "legacy reader-paper CSS var reference" },
  { re: /border-surface-warm/, label: "warm surface border class" },
  { re: /dark:bg-\[linear-gradient/, label: "dark: linear-gradient background patch" },
  { re: /dark:active:bg-\[linear-gradient/, label: "dark: active linear-gradient patch" },
  { re: /dark:shadow-\[/, label: "dark: raw shadow patch" },
  { re: /dark:active:shadow-\[/, label: "dark: active raw shadow patch" },
  { re: /dark:hover:border-muted/, label: "dark: hover border muted patch" },
];

describe("reader / ask theme convergence — cleaned files", () => {
  it.each(CONVERGED_FILES)(
    "$label does not reference paper semantics, warm gradients, raw HEX/RGBA, or dark: patches",
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
// globals.css Reader/Ask section contract
// ---------------------------------------------------------------------------

const GLOBALS_CSS = readAppFile("src/app/globals.css");

describe("globals.css — Ask scrollbar uses semantic tokens", () => {
  it("ask-conversation-scroll does not use raw rgba for scrollbar-color", () => {
    const scrollStart = GLOBALS_CSS.indexOf(".ask-conversation-scroll {");
    expect(scrollStart).toBeGreaterThanOrEqual(0);
    const scrollEnd = GLOBALS_CSS.indexOf(".ask-message-response", scrollStart);
    const scrollSection = GLOBALS_CSS.slice(scrollStart, scrollEnd);
    expect(scrollSection).not.toMatch(/rgba\(\s*24,\s*24,\s*20/);
    expect(scrollSection).toMatch(/color-mix\(in srgb, var\(--ink\)/);
  });
});

describe("globals.css — Reader selectors do not use raw rgba for marks", () => {
  it(".reader-mark does not use raw rgba for fill/line/edge", () => {
    const markStart = GLOBALS_CSS.indexOf(".reader-mark {");
    expect(markStart).toBeGreaterThanOrEqual(0);
    const markEnd = GLOBALS_CSS.indexOf("}", GLOBALS_CSS.indexOf("}", markStart) + 1);
    const markSection = GLOBALS_CSS.slice(markStart, markEnd);
    expect(markSection).not.toMatch(/rgba\(\s*228,\s*176,\s*0/);
    expect(markSection).not.toMatch(/rgba\(\s*166,\s*121,\s*0/);
    expect(markSection).toMatch(/var\(--vocab-amber\)/);
  });

  it(".reader-immersive-paragraph-cue--note uses vocab-amber token", () => {
    const cueStart = GLOBALS_CSS.indexOf(".reader-immersive-paragraph-cue--note {");
    expect(cueStart).toBeGreaterThanOrEqual(0);
    const cueEnd = GLOBALS_CSS.indexOf("}", cueStart);
    const cueSection = GLOBALS_CSS.slice(cueStart, cueEnd);
    expect(cueSection).not.toMatch(/rgba\(\s*167,\s*119,\s*0/);
    expect(cueSection).toMatch(/var\(--vocab-amber\)/);
  });

  it(".reader-immersive-paragraph-cue--highlight uses structure-green token", () => {
    const cueStart = GLOBALS_CSS.indexOf(".reader-immersive-paragraph-cue--highlight {");
    expect(cueStart).toBeGreaterThanOrEqual(0);
    const cueEnd = GLOBALS_CSS.indexOf("}", cueStart);
    const cueSection = GLOBALS_CSS.slice(cueStart, cueEnd);
    expect(cueSection).not.toMatch(/rgba\(\s*53,\s*126,\s*95/);
    expect(cueSection).toMatch(/var\(--structure-green\)/);
  });
});

describe("globals.css — reader-theme-preview classes reference preview tokens", () => {
  it("declares .reader-theme-preview--light referencing light preview token", () => {
    expect(GLOBALS_CSS).toMatch(
      /\.reader-theme-preview--light\s*\{[^}]*background:\s*var\(--theme-preview-light-surface\)/,
    );
  });

  it("declares .reader-theme-preview--dark referencing dark preview token", () => {
    expect(GLOBALS_CSS).toMatch(
      /\.reader-theme-preview--dark\s*\{[^}]*background:\s*var\(--theme-preview-dark-surface\)/,
    );
  });

  it("reader-settings-theme-swatch does not use raw rgba or gradients", () => {
    const swatchStart = GLOBALS_CSS.indexOf(".reader-settings-theme-swatch {");
    expect(swatchStart).toBeGreaterThanOrEqual(0);
    const swatchEnd = GLOBALS_CSS.indexOf(".reader-shell", swatchStart);
    const swatchSection = GLOBALS_CSS.slice(swatchStart, swatchEnd);
    expect(swatchSection).not.toMatch(/rgba\(/);
    expect(swatchSection).not.toMatch(/linear-gradient/);
  });
});

// ---------------------------------------------------------------------------
// Dark Reader color hierarchy contract
// ---------------------------------------------------------------------------

function darkThemeBlock(source: string): string {
  const start = source.indexOf(".dark {\n  --reader-floating-surface:");
  expect(start).toBeGreaterThanOrEqual(0);
  const open = source.indexOf("{", start);
  let depth = 1;
  let index = open + 1;
  while (depth > 0 && index < source.length) {
    if (source[index] === "{") depth++;
    else if (source[index] === "}") depth--;
    index++;
  }
  return source.slice(start, index);
}

describe("Dark Reader color hierarchy", () => {
  const darkSection = darkThemeBlock(GLOBALS_CSS);

  it("uses neutral floating and entry surfaces rather than blue-gray recipes", () => {
    expect(darkSection).toMatch(/--reader-floating-surface:\s*rgba\(37, 37, 37,/);
    expect(darkSection).toMatch(/--reader-entry-surface:\s*rgba\(35, 35, 35,/);
    expect(darkSection).not.toMatch(/rgba\(42, 47, 53/);
    expect(darkSection).not.toMatch(/rgba\(39, 44, 50/);
  });

  it("defines Dark-specific subdued fills for every Reading Record user highlight", () => {
    expect(darkSection).toMatch(/--reader-record-user-yellow-fill:\s*rgba\(111, 88, 30,/);
    expect(darkSection).toMatch(/--reader-record-user-mint-fill:\s*rgba\(44, 82, 58,/);
    expect(darkSection).toMatch(/--reader-record-user-rose-fill:\s*rgba\(76, 61, 91,/);
  });

  it("uses direct selection paint rather than a multiply blend overlay", () => {
    const selectionStart = GLOBALS_CSS.indexOf(".reader-record-plate-document .slate-selection-area");
    const selectionEnd = GLOBALS_CSS.indexOf("}", selectionStart);
    const selectionSection = GLOBALS_CSS.slice(selectionStart, selectionEnd);
    expect(selectionSection).not.toMatch(/mix-blend-mode/);
    expect(selectionSection).toMatch(/var\(--reader-record-selection-fill-strong\)/);
  });
});
// ---------------------------------------------------------------------------
// Reader theme recipe convergence
// ---------------------------------------------------------------------------

describe("Reader theme recipe convergence", () => {
  const darkSection = darkThemeBlock(GLOBALS_CSS);
  it("uses the reading-muted token for context-muted marks and analysis atoms", () => {
    const selectors = [
      ".reader-mark--context-muted {",
      ".reader-analysis-atom--context-muted {",
      ".reader-user-range--context-muted {",
    ];

    for (const selector of selectors) {
      const start = GLOBALS_CSS.indexOf(selector);
      expect(start, `${selector} should exist`).toBeGreaterThanOrEqual(0);
      const end = GLOBALS_CSS.indexOf("}", start);
      const section = GLOBALS_CSS.slice(start, end);
      expect(section).toMatch(/color:\s*var\(--reader-reading-muted\)/);
      expect(section).not.toMatch(/rgba\(17,\s*17,\s*17,\s*0\.76\)/);
    }
  });

  it("keeps Dark Reader support surfaces neutral rather than blue-gray", () => {
    expect(darkSection).not.toMatch(/rgba\(83,\s*89,\s*99/);
    expect(darkSection).not.toMatch(/rgba\(67,\s*74,\s*84/);
    expect(darkSection).not.toMatch(/rgba\(59,\s*65,\s*73/);
    expect(darkSection).not.toMatch(/rgba\(34,\s*38,\s*43/);
    expect(darkSection).toMatch(/--reader-entry-chip-surface:\s*color-mix\(in srgb, var\(--surface-raised\)/);
    expect(darkSection).toMatch(/--reader-gutter-strip-surface:\s*color-mix\(in srgb, var\(--surface-raised\)/);
  });

  it("removes Light-only cream recipes from dictionary, annotation slips, and Daily hero", () => {
    const dictionaryStart = GLOBALS_CSS.indexOf(".reader-dictionary-panel {");
    const dictionaryEnd = GLOBALS_CSS.indexOf(".reader-dictionary-tertiary-button {", dictionaryStart);
    const dictionarySection = GLOBALS_CSS.slice(dictionaryStart, dictionaryEnd);
    expect(dictionarySection).not.toMatch(/linear-gradient|rgba\(255,|rgba\(250,|rgba\(251,/);
    expect(dictionarySection).toMatch(/background:\s*var\(--surface-raised\)/);
    expect(dictionarySection).toMatch(/border-color:\s*var\(--hairline\)/);

    const slipStart = GLOBALS_CSS.indexOf(".reader-annotation-slip {");
    const slipEnd = GLOBALS_CSS.indexOf(".reader-annotation-slip-icon {", slipStart);
    const slipSection = GLOBALS_CSS.slice(slipStart, slipEnd);
    expect(slipSection).not.toMatch(/linear-gradient|var\(--surface-warm\)|rgba\(/);
    expect(slipSection).toMatch(/background:\s*var\(--surface-raised\)/);

    const heroStart = GLOBALS_CSS.indexOf(".daily-hero {");
    const heroEnd = GLOBALS_CSS.indexOf("@media (min-width: 640px)", heroStart);
    const heroSection = GLOBALS_CSS.slice(heroStart, heroEnd);
    expect(heroSection).not.toMatch(/#D4D0C8|#FAF9F6|rgba\(250,\s*249,\s*246/);
    expect(heroSection).toMatch(/background:\s*var\(--surface-raised\)/);
    expect(heroSection).toMatch(/var\(--surface-canvas\)/);
  });

  it("maps analysis tones back to the Reader semantic palette", () => {
    for (const semanticToken of [
      "structure-green",
      "vocab-amber",
      "phrase-lavender",
      "context-blue",
      "grammar-violet",
      "reader-reading-muted",
    ]) {
      expect(GLOBALS_CSS).toContain(`var(--${semanticToken})`);
    }
    expect(GLOBALS_CSS).not.toMatch(/--reader-analysis-tone-[1-6]:\s*(?:rgb|rgba)\(/);
  });
});
// ---------------------------------------------------------------------------
// Theme localStorage contract — Reader must not read/write theme keys
// ---------------------------------------------------------------------------

describe("reader theme localStorage contract", () => {
  const READER_DIRS = [
    "src/components/reader",
    "src/app/(private)/app/reader",
    "src/app/(private)/app/read",
  ];

  it.each(READER_DIRS)("no file in %s references legacy reader theme localStorage", (dir) => {
    // We cannot easily glob in this test, so we check the specific
    // files we know about. This is a smoke check, not exhaustive.
    const filesToCheck = [
      "src/components/reader/settings/ReaderSettingsPanel.tsx",
      "src/components/reader/settings/shared.ts",
      "src/components/reader/ReaderContextPanel.tsx",
      "src/app/(private)/app/read/page.tsx",
    ];
    for (const file of filesToCheck) {
      if (!file.startsWith(dir)) continue;
      const source = readAppFile(file);
      expect(
        /claread\.reader\.themeName/.test(source),
        `${file}: legacy claread.reader.themeName reference found`,
      ).toBe(false);
      expect(
        /LEGACY_READER_THEME_STORAGE_KEY/.test(source),
        `${file}: LEGACY_READER_THEME_STORAGE_KEY reference found`,
      ).toBe(false);
    }
  });
});

// ---------------------------------------------------------------------------
// Task 3C review fix — ReaderRecordPlateSurface top bar / More menu
// semantic surface tier contract.
// ---------------------------------------------------------------------------

const PLATE_SURFACE_SOURCE = readAppFile(
  "src/components/reader/plate/ReaderRecordPlateSurface.tsx",
);

describe("ReaderRecordPlateSurface — top bar and More menu use semantic surface tiers", () => {
  it("ReaderRecordTopBar uses bg-surface (not bg-surface-canvas)", () => {
    // The top bar element carries testid `reader-record-top-bar`. Its
    // className must include `bg-surface` as a complete class (not as a
    // prefix of `bg-surface-canvas` or `bg-surface-raised`).
    const topBarStart = PLATE_SURFACE_SOURCE.indexOf(
      'data-testid="reader-record-top-bar"',
    );
    expect(topBarStart).toBeGreaterThanOrEqual(0);
    const topBarEnd = PLATE_SURFACE_SOURCE.indexOf(">", topBarStart);
    const topBarSection = PLATE_SURFACE_SOURCE.slice(topBarStart, topBarEnd);
    expect(topBarSection).toMatch(/bg-surface(?![-\w])/);
    expect(topBarSection).not.toMatch(/bg-surface-canvas/);
  });

  it("ReaderRecordMoreMenu DropdownMenuContent uses bg-surface-raised (not bg-surface-canvas)", () => {
    // The More menu panel carries testid `reader-record-more-menu-content`.
    // Its className must include `bg-surface-raised` as a complete class
    // and must not include the canvas-tier `bg-surface-canvas` class.
    const moreMenuStart = PLATE_SURFACE_SOURCE.indexOf(
      'data-testid="reader-record-more-menu-content"',
    );
    expect(moreMenuStart).toBeGreaterThanOrEqual(0);
    // The className appears before the testid in this component; scan a
    // generous window around the testid to capture the full opening tag.
    const moreMenuSection = PLATE_SURFACE_SOURCE.slice(
      moreMenuStart - 600,
      moreMenuStart + 200,
    );
    expect(moreMenuSection).toMatch(/bg-surface-raised(?![-\w])/);
    expect(moreMenuSection).not.toMatch(/bg-surface-canvas/);
  });

  it("ReaderRecordPlateSurface no longer uses bg-surface-canvas anywhere", () => {
    expect(PLATE_SURFACE_SOURCE).not.toMatch(/bg-surface-canvas/);
  });
});
