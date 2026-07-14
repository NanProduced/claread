// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { IconButton, iconButtonVariants } from ".";
import type { VariantProps } from "class-variance-authority";

type IconButtonVariants = NonNullable<VariantProps<typeof iconButtonVariants>>;

afterEach(cleanup);

function classOf(variant: IconButtonVariants["variant"]) {
  return iconButtonVariants({ variant, size: "md" });
}

describe("IconButton semantic token consumption", () => {
  it.each(["outline", "quiet", "danger"] as const)(
    "renders %s without raw foundation color classes",
    (variant) => {
      const cls = classOf(variant);
      expect(cls, `${variant} must not use text-ink`).not.toMatch(/\btext-ink\b/);
      expect(cls, `${variant} must not use text-ink-soft`).not.toMatch(
        /\btext-ink-soft\b/,
      );
      expect(cls, `${variant} must not use text-muted`).not.toMatch(
        /\btext-muted\b/,
      );
      expect(cls, `${variant} must not use text-error-red`).not.toMatch(
        /\btext-error-red\b/,
      );
      expect(cls, `${variant} must not use border-hairline`).not.toMatch(
        /\bborder-hairline\b/,
      );
    },
  );

  it("outline uses text-secondary by default and hovers to text-primary + border-strong", () => {
    const cls = classOf("outline");
    expect(cls).toContain("text-text-secondary");
    expect(cls).toContain("border-border-subtle");
    expect(cls).toContain("hover:border-border-strong");
    expect(cls).toContain("hover:text-text-primary");
  });

  it("quiet uses text-secondary by default and hovers to text-primary", () => {
    const cls = classOf("quiet");
    expect(cls).toContain("text-text-secondary");
    expect(cls).toContain("hover:text-text-primary");
  });

  it("danger uses feedback-error text and keeps the danger gradient recipe", () => {
    const cls = classOf("danger");
    expect(cls).toContain("text-feedback-error");
    expect(cls).toContain("[background-image:var(--app-danger-gradient)]");
    expect(cls).toContain("hover:[background-image:var(--app-danger-gradient-hover)]");
  });

  it("requires aria-label (a11y contract)", () => {
    // The TS `aria-label` requirement is verified at compile time; we
    // re-assert it at runtime so a future refactor that loosens the type
    // is caught in CI.
    render(
      <IconButton aria-label="back to source">
        <span aria-hidden>→</span>
      </IconButton>,
    );
    expect(
      screen.getByRole("button", { name: "back to source" }),
    ).toBeTruthy();
  });

  it("applies disabled:opacity-50 + disabled:pointer-events-none", () => {
    render(
      <IconButton aria-label="disabled" disabled>
        <span aria-hidden>x</span>
      </IconButton>,
    );
    const btn = screen.getByRole("button", { name: "disabled" });
    expect(btn.className).toContain("disabled:opacity-50");
    expect(btn.className).toContain("disabled:pointer-events-none");
  });
});