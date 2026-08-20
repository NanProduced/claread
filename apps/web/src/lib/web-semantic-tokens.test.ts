import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  clareadTailwindPreset,
} from "@claread/design-tokens/web/tailwind-preset";

const tokensCssPath = resolve(
  __dirname,
  "../../../../packages/design-tokens/src/web/tokens.css",
);
const tokensCss = readFileSync(tokensCssPath, "utf8");
const globalsCssPath = resolve(__dirname, "../app/globals.css");
const globalsCss = readFileSync(globalsCssPath, "utf8");

/**
 * Canonical web semantic tokens defined in DESIGN.md §"主题与色彩角色".
 * Adding a new semantic token MUST extend `REQUIRED_SEMANTIC_TOKENS`
 * (defined in `:root, .light`) and, if it varies by theme, also
 * `THEME_OVERRIDABLE_TOKENS` so `.dark` overrides it. The Paper
 * theme is permanently offline — Light is the canonical neutral
 * declaration, and `.dark` provides the only contrast layer.
 */
const REQUIRED_SEMANTIC_TOKENS = [
  "surface-canvas",
  "surface-stage",
  "surface-raised",
  "surface-overlay",
  "text-primary",
  "text-secondary",
  "action-primary",
  "action-primary-foreground",
  "action-secondary",
  "action-secondary-foreground",
  "border-subtle",
  "border-strong",
  "focus-ring",
  "feedback-success",
  "feedback-warning",
  "feedback-error",
  "feedback-error-foreground",
] as const;

/** Tokens that must be redefined in the .dark block (Light + Dark only)
 * because each theme provides a different interactive / soft-tint recipe. */
const THEME_OVERRIDABLE_TOKENS = [
  "interactive-hover",
  "interactive-active",
  "interactive-quiet-hover",
  "feedback-success-soft",
  "feedback-warning-soft",
] as const;

/** Interactive tokens resolve to gradient / surface recipes and are NOT
 * safe as Tailwind `--color-*` aliases until a backgroundImage utility
 * is shipped alongside them. The preset and globals.css must therefore
 * omit them. If a future round introduces such a utility, this list
 * becomes a `THEME_OVERRIDABLE_TOKENS_BACKGROUNDIMAGE` and the test
 * switches to asserting the utility exists. */
const GRADIENT_INTERACTIVE_TOKENS = [
  "interactive-hover",
  "interactive-active",
  "interactive-quiet-hover",
] as const;

/**
 * shadcn/Tailwind semantic tokens that must exist as paired CSS variables
 * in `:root, .light` and be exposed through `@theme` as Tailwind utilities.
 * The sidebar tokens complete the shadcn Sidebar component contract.
 */
const SHADCN_PAIRED_TOKENS = [
  "background",
  "foreground",
  "card",
  "card-foreground",
  "popover",
  "popover-foreground",
  "primary",
  "primary-foreground",
  "secondary",
  "secondary-foreground",
  "muted",
  "muted-foreground",
  "accent",
  "accent-foreground",
  "destructive",
  "destructive-foreground",
  "border",
  "input",
  "ring",
  "sidebar",
  "sidebar-foreground",
  "sidebar-primary",
  "sidebar-primary-foreground",
  "sidebar-accent",
  "sidebar-accent-foreground",
  "sidebar-border",
  "sidebar-ring",
] as const;

function blockRange(openSelector: RegExp) {
  const start = tokensCss.search(openSelector);
  if (start === -1) {
    throw new Error(`Theme block not found: ${openSelector}`);
  }
  const rest = tokensCss.indexOf("\n", start);
  const closeIdx = tokensCss.indexOf("\n}", rest);
  if (closeIdx === -1) {
    throw new Error(`Theme block closing brace not found: ${openSelector}`);
  }
  return tokensCss.slice(start, closeIdx + 1);
}

function assertTokenInBlock(
  block: string,
  token: string,
  label: string,
) {
  const pattern = new RegExp(`--${token}\\s*:`);
  expect(
    pattern.test(block),
    `Expected --${token} in ${label} theme block`,
  ).toBe(true);
}

function declarationValue(block: string, token: string) {
  // Match `--<token>:` at an identifier boundary: the preceding char (if
  // any) must not be part of a longer identifier. Using `(?<![a-zA-Z0-9-])`
  // ensures `--app-surface-raised` does NOT match `--surface-raised`.
  const re = new RegExp(`(?<![a-zA-Z0-9-])--${token}\\s*:\\s*([^;]+);`);
  const match = re.exec(block);
  return match ? match[1].trim() : null;
}

describe("Claread Web semantic tokens", () => {
  it("declares every required semantic token in the Light (root) block", () => {
    const block = blockRange(/:root,\s*\.light\s*\{/);
    for (const token of REQUIRED_SEMANTIC_TOKENS) {
      assertTokenInBlock(block, token, "Light :root");
    }
  });

  it("exposes every required semantic token through the Tailwind preset", () => {
    const presetColors = clareadTailwindPreset.colors as Record<string, string>;

    for (const token of REQUIRED_SEMANTIC_TOKENS) {
      expect(
        presetColors[token],
        `Tailwind preset is missing semantic token: ${token}`,
      ).toContain(`var(--${token})`);
    }

    // Soft feedback variants are surface recipes, but they ARE valid color
    // values (color-mix returns a hex), so they go through `--color-*` like
    // any other semantic feedback token.
    expect(presetColors["feedback-success-soft"]).toContain(
      "var(--feedback-success-soft)",
    );
    expect(presetColors["feedback-warning-soft"]).toContain(
      "var(--feedback-warning-soft)",
    );
  });

  it.each([
    ["Light", /^\.light\s*\{/m],
    ["Dark", /^\.dark\s*\{/m],
  ] as const)(
    "overrides every overridable semantic token in the %s theme",
    (label, selector) => {
      const block = blockRange(selector);
      for (const token of THEME_OVERRIDABLE_TOKENS) {
        assertTokenInBlock(block, token, label);
      }
    },
  );

  it("keeps light and dark blocks resolvable (no accidental `unset`)", () => {
    for (const [label, selector] of [
      ["Light", /^\.light\s*\{/m],
      ["Dark", /^\.dark\s*\{/m],
    ] as const) {
      const block = blockRange(selector);
      expect(
        /unset|initial/.test(block),
        `${label} block should not reset semantic tokens to unset/initial`,
      ).toBe(false);
    }
  });

  it("does not let any required semantic token reference itself", () => {
    const block = blockRange(/:root,\s*\.light\s*\{/);
    for (const token of REQUIRED_SEMANTIC_TOKENS) {
      const value = declarationValue(block, token);
      expect(
        value,
        `--${token} is missing from :root — did the foundation map regress?`,
      ).not.toBeNull();
      expect(
        value,
        `--${token} must not reference itself (got: ${value})`,
      ).not.toBe(`var(--${token})`);
    }
  });

  it("resolves surface-raised to a real foundation token in Light", () => {
    const block = blockRange(/:root,\s*\.light\s*\{/);
    const value = declarationValue(block, "surface-raised");
    expect(value, "--surface-raised must be defined in :root").not.toBeNull();
    // Must reference a foundation alias or another declared variable;
    // a self-reference is a regression.
    expect(
      value!.startsWith("var(--"),
      `--surface-raised should resolve through a CSS variable (got: ${value})`,
    ).toBe(true);
    expect(value).not.toBe("var(--surface-raised)");
    // Foundation → semantic mapping kept intact in `:root, .light`:
    // --surface-raised → --app-surface-raised → --cl-color-app-surface-raised.
    expect(value).toBe("var(--app-surface-raised)");

    // And the chain terminates at a literal foundation color (not another
    // variable hop), proving no circular reference was introduced.
    const appRaised = declarationValue(block, "app-surface-raised");
    expect(appRaised).toBe("var(--cl-color-app-surface-raised)");
    const clRaised = declarationValue(block, "cl-color-app-surface-raised");
    expect(clRaised).toMatch(/^#[0-9a-fA-F]{3,8}$/);
  });

  it("does NOT expose gradient interactive tokens as Tailwind colors", () => {
    const presetColors = clareadTailwindPreset.colors as Record<
      string,
      string | undefined
    >;
    for (const token of GRADIENT_INTERACTIVE_TOKENS) {
      expect(
        presetColors[token],
        `Tailwind preset must not alias gradient token --${token} as a color ` +
          `(its value is a linear-gradient / surface recipe and is unsafe ` +
          `for bg-* utilities).`,
      ).toBeUndefined();
    }

    // globals.css must also skip the alias layer for the same reason.
    for (const token of GRADIENT_INTERACTIVE_TOKENS) {
      expect(
        new RegExp(`--color-${token}\\s*:`).test(globalsCss),
        `globals.css must not alias --${token} under --color-* until a ` +
          `backgroundImage utility is shipped.`,
      ).toBe(false);
    }

    // Sanity: tokens.css STILL defines them so explicit
    // `background-image: var(--interactive-hover)` keeps working.
    const root = blockRange(/:root,\s*\.light\s*\{/);
    for (const token of GRADIENT_INTERACTIVE_TOKENS) {
      assertTokenInBlock(root, token, "Light :root");
    }
  });

  it("declares every shadcn paired token in :root, .light", () => {
    const block = blockRange(/:root,\s*\.light\s*\{/);
    for (const token of SHADCN_PAIRED_TOKENS) {
      assertTokenInBlock(block, token, "Light :root (shadcn)");
    }
  });

  it("exposes every shadcn paired token through @theme in globals.css", () => {
    for (const token of SHADCN_PAIRED_TOKENS) {
      expect(
        new RegExp(`--color-${token}\\s*:`).test(globalsCss),
        `globals.css @theme must expose --color-${token} as a Tailwind utility`,
      ).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Daily Reader surface (public /daily pages) token and font contract.
// The Daily magazine surface owns a scoped --dr-* token group; the Daily
// reading serif is a dedicated next/font variable so the global reading
// font stays on Source Serif 4 for every non-Daily page.
// ---------------------------------------------------------------------------

const clareadFontsTs = readFileSync(
  resolve(__dirname, "../app/claread-fonts.ts"),
  "utf8",
);
const dailyPages = [
  resolve(__dirname, "../app/(public)/daily/page.tsx"),
  resolve(__dirname, "../app/(public)/daily/[articleId]/page.tsx"),
] as const;
const notoSerifDir = resolve(__dirname, "../../public/fonts/noto-serif-sc");

function cssBlock(source: string, selector: string): string {
  const start = source.indexOf(selector);
  if (start === -1) {
    throw new Error(`Selector not found in CSS source: ${selector}`);
  }
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

function notoFontFaceBlock(): string {
  const idx = globalsCss.indexOf('font-family: "Claread Noto Serif SC";');
  expect(idx, "Claread Noto Serif SC @font-face must exist").toBeGreaterThanOrEqual(0);
  const start = globalsCss.lastIndexOf("@font-face", idx);
  return cssBlock(globalsCss.slice(start), "@font-face");
}

describe("Daily Reader surface tokens and fonts", () => {
  it("defines the scoped --dr-* token group on .daily-reader-surface", () => {
    const block = cssBlock(globalsCss, ".daily-reader-surface {");
    expect(block).toContain("--dr-paper: #F7F5F2;");
    expect(block).toContain("--dr-paper-raised: #FFFFFF;");
    expect(block).toContain("--dr-ink: #1A1A1A;");
    expect(block).toContain("--dr-ink-zh: #33302C;");
    expect(block).toContain("--dr-meta: #6B6B6B;");
    expect(block).toContain("--dr-rule: #D9D4CD;");
    // Brand blue as an independent literal Daily accent, never an alias
    // of the workspace action tokens.
    expect(block).toContain("--dr-accent: #1F5EFF;");
    expect(block).not.toMatch(/--dr-accent:\s*var\(/);
    expect(block).toContain("--dr-ratio-hero: 21 / 9;");
    expect(block).toContain("--dr-ratio-inline: 3 / 2;");
    expect(block).toContain("--dr-ratio-square: 1 / 1;");
  });

  it("scopes the Daily reading serif, font stacks, and type ramp to the surface", () => {
    const block = cssBlock(globalsCss, ".daily-reader-surface {");
    expect(block).toContain(
      "--dr-font-en: var(--font-daily-reading-en), Georgia, serif;",
    );
    expect(block).toContain(
      '--dr-font-zh: "Claread Noto Serif SC", var(--font-reading-en), "Source Han Serif SC", "Songti SC", "STSong", "Noto Serif SC", serif;',
    );
    expect(block).toContain(
      "--dr-font-mono: var(--font-mono-en), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;",
    );
    // Fluid clamp is reserved for the hero and article headline steps.
    expect(block).toContain(
      "--dr-type-hero-size: clamp(2rem, 1.2rem + 4vw, 3.5rem);",
    );
    expect(block).toContain(
      "--dr-type-headline-size: clamp(1.625rem, 1.3rem + 1vw, 2rem);",
    );
    expect(block).toContain("--dr-type-zh-size: 0.9375rem;");
    expect(block).toContain("--dr-type-zh-lh: 1.8;");
    for (const step of ["deck", "body", "zh", "caption", "mono"]) {
      expect(block).not.toMatch(
        new RegExp(`--dr-type-${step}-size:[^;]*clamp\\(`),
      );
    }
    // The Daily serif variable is referenced exactly once in globals.css
    // (by the surface stack) and the surface class lives only on the two
    // public Daily pages.
    expect(globalsCss.match(/var\(--font-daily-reading-en\)/g)).toHaveLength(1);
    for (const page of dailyPages) {
      expect(readFileSync(page, "utf8")).toMatch(
        /<main className="daily-reader-surface /,
      );
    }
  });

  it("keeps the global reading font on Source Serif 4 and wires the Daily fonts via next/font", () => {
    expect(clareadFontsTs).toContain(
      'import { IBM_Plex_Mono, Inter, Newsreader, Source_Serif_4 } from "next/font/google";',
    );
    expect(clareadFontsTs).toContain("const clareadUiSans = Inter({");
    expect(clareadFontsTs).toContain('variable: "--font-ui-en",');
    expect(clareadFontsTs).toContain("const clareadReadingSerif = Source_Serif_4({");
    expect(clareadFontsTs).toContain('variable: "--font-reading-en",');
    expect(clareadFontsTs).toContain("const clareadDailyReadingSerif = Newsreader({");
    expect(clareadFontsTs).toContain('variable: "--font-daily-reading-en",');
    expect(clareadFontsTs).toContain("const clareadMono = IBM_Plex_Mono({");
    expect(clareadFontsTs).toContain('weight: ["400", "500"],');
    expect(clareadFontsTs).toContain('variable: "--font-mono-en",');
    expect(clareadFontsTs).toContain("clareadReadingSerif.variable,");
    expect(clareadFontsTs).toContain("clareadDailyReadingSerif.variable,");
    expect(clareadFontsTs).toContain("clareadMono.variable,");

    // The Tailwind reading utilities resolve at :root, where tokens.css
    // declares --font-reading-en: Newsreader; a var() reference would
    // flatten to that phantom name and now match the global Newsreader
    // face. They must spell out the global serif instead so non-Daily
    // pages render Source Serif 4.
    const themeBlock = cssBlock(globalsCss, "@theme {");
    for (const stack of ["--font-display:", "--font-headline:", "--font-reading:"]) {
      const decl = themeBlock
        .split("\n")
        .find((line) => line.trim().startsWith(stack));
      expect(decl, `${stack} declaration must exist in @theme`).toBeTruthy();
      expect(decl).toContain(
        '"Source Serif 4", "Source Serif 4 Fallback", var(--font-reading-zh), Georgia, "Times New Roman", serif;',
      );
      expect(decl).not.toContain("var(--font-reading-en)");
      expect(decl).not.toContain("Newsreader");
    }
  });

  it("self-hosts the Noto Serif SC subset with swap and a CJK-only unicode-range", () => {
    const face = notoFontFaceBlock();
    expect(face).toContain(
      'src: url("/fonts/noto-serif-sc/noto-serif-sc-regular-subset.woff2") format("woff2");',
    );
    expect(face).toContain("font-display: swap;");
    expect(face).toContain("font-weight: 400;");
    expect(face).toMatch(/unicode-range:[^;]*U\+00D7/);
    expect(face).toMatch(/unicode-range:[^;]*U\+2103/);
    expect(face).toMatch(/unicode-range:[^;]*U\+4E00-9FFF/);
    // Basic Latin stays with the Latin webfonts (Newsreader / Inter).
    expect(face).not.toMatch(/U\+0000-007F/);
    expect(face).not.toMatch(/U\+0020-007E/);
    const woff2 = readFileSync(
      resolve(notoSerifDir, "noto-serif-sc-regular-subset.woff2"),
    );
    expect(woff2.byteLength).toBeGreaterThan(100_000);
    expect(woff2.byteLength).toBeLessThan(4_000_000);
    expect(readFileSync(resolve(notoSerifDir, "OFL.txt"), "utf8")).toMatch(
      /SIL OPEN FONT LICENSE/i,
    );
  });
});