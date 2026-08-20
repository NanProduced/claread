/**
 * Static contract for the Daily Reader surface token and font
 * infrastructure (C-1). Locks the --dr-* token group, its surface scope
 * on the two public Daily pages, the Newsreader / Noto Serif SC subset /
 * IBM Plex Mono wiring, and the Noto subset's swap + unicode-range
 * loading contract. Source-text assertions only.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = path.resolve(__dirname, "../..");
const GLOBALS_CSS = fs.readFileSync(path.join(WEB_ROOT, "src/app/globals.css"), "utf8");
const FONTS_TS = fs.readFileSync(path.join(WEB_ROOT, "src/app/claread-fonts.ts"), "utf8");
const DAILY_PAGES = [
  path.join(WEB_ROOT, "src/app/(public)/daily/page.tsx"),
  path.join(WEB_ROOT, "src/app/(public)/daily/[articleId]/page.tsx"),
];
const NOTO_DIR = path.join(WEB_ROOT, "public/fonts/noto-serif-sc");
const SELF_PATH = __filename;

function extractBlock(source: string, selector: string): string {
  const start = source.indexOf(selector);
  expect(start, `selector "${selector}" must exist`).toBeGreaterThanOrEqual(0);
  const open = source.indexOf("{", start);
  let depth = 1;
  let i = open + 1;
  while (depth > 0 && i < source.length) {
    const ch = source[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") depth -= 1;
    i += 1;
  }
  return source.slice(start, i);
}

function extractNotoFontFace(source: string): string {
  const idx = source.indexOf('font-family: "Claread Noto Serif SC";');
  expect(idx, "Noto Serif SC subset @font-face must exist").toBeGreaterThanOrEqual(0);
  const start = source.lastIndexOf("@font-face", idx);
  return extractBlock(source.slice(start), "@font-face");
}

function listSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...listSourceFiles(full));
    } else if (/\.(tsx?|css)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("daily reader surface tokens and fonts (C-1)", () => {
  it("defines the core --dr-* token group on .daily-reader-surface", () => {
    const block = extractBlock(GLOBALS_CSS, ".daily-reader-surface {");
    expect(block).toContain("--dr-paper: #F7F5F2;");
    expect(block).toContain("--dr-paper-raised: #FFFFFF;");
    expect(block).toContain("--dr-ink: #1A1A1A;");
    expect(block).toContain("--dr-ink-zh: #33302C;");
    expect(block).toContain("--dr-meta: #6B6B6B;");
    expect(block).toContain("--dr-rule: #D9D4CD;");
    expect(block).toContain("--dr-ratio-hero: 21 / 9;");
    expect(block).toContain("--dr-ratio-inline: 3 / 2;");
    expect(block).toContain("--dr-ratio-square: 1 / 1;");
    // Accent is an independent Daily token: literal brand blue, not an
    // alias of the workspace action tokens.
    expect(block).toContain("--dr-accent: #1F5EFF;");
    expect(block).not.toMatch(/--dr-accent:\s*var\(/);
  });

  it("declares the semantic Daily type ramp with clamp reserved for hero and headline", () => {
    const block = extractBlock(GLOBALS_CSS, ".daily-reader-surface {");
    expect(block).toContain("--dr-type-hero-size: clamp(2rem, 1.2rem + 4vw, 3.5rem);");
    expect(block).toContain("--dr-type-hero-lh: 1.08;");
    expect(block).toContain("--dr-type-headline-size: clamp(1.625rem, 1.3rem + 1vw, 2rem);");
    expect(block).toContain("--dr-type-headline-lh: 1.12;");
    expect(block).toContain("--dr-type-deck-size: 1.25rem;");
    expect(block).toContain("--dr-type-deck-lh: 1.45;");
    expect(block).toContain("--dr-type-body-size: 1.125rem;");
    expect(block).toContain("--dr-type-body-lh: 1.65;");
    expect(block).toContain("--dr-type-zh-size: 0.9375rem;");
    expect(block).toContain("--dr-type-zh-lh: 1.8;");
    expect(block).toContain("--dr-type-caption-size: 0.8125rem;");
    expect(block).toContain("--dr-type-caption-lh: 1.35;");
    expect(block).toContain("--dr-type-mono-size: 0.75rem;");
    expect(block).toContain("--dr-type-mono-lh: 1.4;");
    for (const step of ["deck", "body", "zh", "caption", "mono"]) {
      expect(block).not.toMatch(new RegExp("--dr-type-" + step + "-size:[^;]*clamp\\("));
    }
  });

  it("declares Daily font stacks for English serif, Chinese serif, UI sans, and mono", () => {
    const block = extractBlock(GLOBALS_CSS, ".daily-reader-surface {");
    expect(block).toContain("--dr-font-en: var(--font-reading-en), Georgia, serif;");
    expect(block).toContain(
      '--dr-font-zh: "Claread Noto Serif SC", var(--font-reading-en), "Source Han Serif SC", "Songti SC", "STSong", "Noto Serif SC", serif;',
    );
    expect(block).toContain("--dr-font-ui: var(--font-ui-en), var(--font-ui-zh), sans-serif;");
    expect(block).toContain(
      "--dr-font-mono: var(--font-mono-en), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;",
    );
  });

  it("wires daily-reader-surface to the outermost main of both Daily pages only", () => {
    for (const page of DAILY_PAGES) {
      const source = fs.readFileSync(page, "utf8");
      expect(source).toMatch(/<main className="daily-reader-surface /);
    }
    const referencing = listSourceFiles(path.join(WEB_ROOT, "src"))
      .filter((file) => file !== SELF_PATH)
      .filter((file) => !file.endsWith(path.join("src", "app", "globals.css")))
      .filter((file) => fs.readFileSync(file, "utf8").includes("daily-reader-surface"))
      .map((file) => path.relative(WEB_ROOT, file).replace(/\\/g, "/"))
      .sort();
    expect(referencing).toEqual([
      "src/app/(public)/daily/[articleId]/page.tsx",
      "src/app/(public)/daily/page.tsx",
    ]);
  });

  it("self-hosts the Noto Serif SC subset with swap and a CJK-only unicode-range", () => {
    const face = extractNotoFontFace(GLOBALS_CSS);
    expect(face).toContain(
      'src: url("/fonts/noto-serif-sc/noto-serif-sc-regular-subset.woff2") format("woff2");',
    );
    expect(face).toContain("font-display: swap;");
    expect(face).toContain("font-weight: 400;");
    expect(face).toMatch(/unicode-range:[^;]*U\+4E00-9FFF/);
    // Latin stays in Newsreader/Inter: the subset must not claim the
    // basic Latin range.
    expect(face).not.toMatch(/U\+0000-007F/);
    expect(face).not.toMatch(/U\+0020-007E/);
  });

  it("ships only the subsetted WOFF2 and its OFL license", () => {
    const woff2 = path.join(NOTO_DIR, "noto-serif-sc-regular-subset.woff2");
    const stats = fs.statSync(woff2);
    // ~7.5k-glyph subset; a full Noto Serif SC release is far larger.
    expect(stats.size).toBeGreaterThan(100_000);
    expect(stats.size).toBeLessThan(4_000_000);
    const license = fs.readFileSync(path.join(NOTO_DIR, "OFL.txt"), "utf8");
    expect(license).toMatch(/SIL OPEN FONT LICENSE/i);
    expect(fs.readdirSync(NOTO_DIR).sort()).toEqual([
      "OFL.txt",
      "noto-serif-sc-regular-subset.woff2",
    ]);
  });

  it("loads Newsreader for English reading and IBM Plex Mono via next/font", () => {
    expect(FONTS_TS).toContain(
      'import { IBM_Plex_Mono, Inter, Newsreader } from "next/font/google";',
    );
    expect(FONTS_TS).not.toContain("Source_Serif_4");
    expect(FONTS_TS).toContain("const clareadReadingSerif = Newsreader({");
    expect(FONTS_TS).toContain('variable: "--font-reading-en",');
    expect(FONTS_TS).toContain("const clareadMono = IBM_Plex_Mono({");
    expect(FONTS_TS).toContain('weight: ["400", "500"],');
    expect(FONTS_TS).toContain('variable: "--font-mono-en",');
    expect(FONTS_TS).toContain("clareadMono.variable,");
    expect(FONTS_TS).toContain("const clareadUiSans = Inter({");
    expect(FONTS_TS).toContain('variable: "--font-ui-en",');
  });
});
