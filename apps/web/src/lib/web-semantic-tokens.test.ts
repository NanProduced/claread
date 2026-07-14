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
});