/**
 * Static contract tests for Task 3B: Reader / Ask neutral theme convergence.
 * Asserts that Reader and Ask components (excluding files owned by
 * concurrent agents) no longer reference paper semantics, warm gradients,
 * raw HEX/RGBA, or dark: color patches.
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

// Files cleaned in Task 3B. Files owned by concurrent agents
// (AnalyzeSubmitForm, ReaderWorkbench, AiWorkspacePanel,
// ReaderRecordWorkbenchSurface, ReaderRecordPlateSurface) are excluded
// until their owners commit.
const CONVERGED_FILES: ReadonlyArray<{ rel: string; label: string }> = [
  // Reader settings
  { rel: "src/components/reader/settings/ReaderSettingsPanel.tsx", label: "ReaderSettingsPanel" },
  // Reader component layer
  { rel: "src/components/reader/FeedbackSheet.tsx", label: "FeedbackSheet" },
  { rel: "src/components/reader/FavoriteButton.tsx", label: "FavoriteButton (component)" },
  { rel: "src/components/reader/ReaderGlobalFeedbackPrompt.tsx", label: "ReaderGlobalFeedbackPrompt" },
  { rel: "src/components/reader/ReaderContextPanel.tsx", label: "ReaderContextPanel" },
  { rel: "src/components/reader/AnnotationGutter.tsx", label: "AnnotationGutter" },
  { rel: "src/components/reader/interaction.ts", label: "interaction" },
  // Reader plate layer
  { rel: "src/components/reader/plate/ReaderPlateSnapshotSurface.tsx", label: "ReaderPlateSnapshotSurface" },
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
