// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Button, buttonVariants } from ".";
import type { VariantProps } from "class-variance-authority";
import { primitiveFocusRing } from "../shared";

type ButtonVariants = NonNullable<VariantProps<typeof buttonVariants>>;

afterEach(cleanup);

/**
 * Round 2 regression: every Button variant must route its static colors
 * through the semantic token layer (text-primary / text-secondary /
 * border-subtle / border-strong / feedback-error) instead of the older
 * foundation aliases (text-ink / text-ink-soft / text-muted / text-error-red /
 * border-hairline).
 *
 * Gradient recipes (primary, secondary, danger) and the primary-ink solid
 * recipe are kept as background-image / bg-ink because action-primary and
 * feedback-error alone cannot express the original visual identity.
 */

function classOf(variant: ButtonVariants["variant"]) {
  return buttonVariants({ variant, size: "md", density: "default" });
}

describe("Button semantic token consumption", () => {
  it.each([
    "primary",
    "secondary",
    "outline",
    "subtle",
    "quiet",
    "danger",
    "ghost",
    "primary-ink",
  ] as const)("renders %s without raw foundation color classes", (variant) => {
    const cls = classOf(variant);
    expect(cls, `${variant} must not use text-ink (use text-text-primary)`).not
      .toMatch(/\btext-ink\b/);
    expect(
      cls,
      `${variant} must not use text-ink-soft (use text-text-primary or text-text-secondary)`,
    ).not.toMatch(/\btext-ink-soft\b/);
    expect(
      cls,
      `${variant} must not use text-muted (use text-text-secondary)`,
    ).not.toMatch(/\btext-muted\b/);
    expect(
      cls,
      `${variant} must not use text-error-red (use text-feedback-error)`,
    ).not.toMatch(/\btext-error-red\b/);
    expect(
      cls,
      `${variant} must not use border-hairline (use border-border-subtle)`,
    ).not.toMatch(/\bborder-hairline\b/);
  });

  it("primary CTA routes text to action-primary-foreground and keeps gradient", () => {
    const cls = classOf("primary");
    expect(cls).toContain("text-action-primary-foreground");
    // Gradient recipe is preserved intentionally.
    expect(cls).toContain("[background-image:var(--app-primary-gradient)]");
    expect(cls).not.toContain("[background-image:var(--app-primary-gradient)] text-white");
  });

  it("secondary CTA routes text to action-secondary-foreground and keeps dark gradient", () => {
    const cls = classOf("secondary");
    expect(cls).toContain("text-action-secondary-foreground");
    expect(cls).toContain("[background-image:var(--app-secondary-gradient)]");
    // Legacy raw `text-white` literal must not be present anymore.
    expect(cls).not.toContain("text-white ");
    expect(cls).not.toContain(" text-white");
  });

  it("danger variant routes text to feedback-error and keeps gradient", () => {
    const cls = classOf("danger");
    expect(cls).toContain("text-feedback-error");
    expect(cls).toContain("[background-image:var(--app-danger-gradient)]");
  });

  it("outline variant uses text-primary + border-subtle and hovers to text-secondary + border-strong", () => {
    const cls = classOf("outline");
    expect(cls).toContain("text-text-primary");
    expect(cls).toContain("border-border-subtle");
    expect(cls).toContain("hover:border-border-strong");
    expect(cls).toContain("hover:text-text-secondary");
  });

  it("quiet variant uses text-secondary by default and hovers to text-primary", () => {
    const cls = classOf("quiet");
    expect(cls).toContain("text-text-secondary");
    expect(cls).toContain("hover:text-text-primary");
  });

  it("ghost variant uses text-secondary by default and hovers to text-primary", () => {
    const cls = classOf("ghost");
    expect(cls).toContain("text-text-secondary");
    expect(cls).toContain("hover:text-text-primary");
  });

  it("does not reintroduce raw focus-ring literals", () => {
    const cls = classOf("outline");
    // The Button itself does not own the focus ring; primitiveFocusRing does.
    // We assert the literal string is absent in the variant output and that
    // primitiveFocusRing still uses the semantic ring tokens.
    expect(cls).not.toContain("ring-lens-blue");
    expect(primitiveFocusRing).toContain("ring-focus-ring/");
    expect(primitiveFocusRing).toContain("ring-offset-surface-canvas");
  });

  it("renders a default Button with disabled state still using opacity-50", () => {
    render(<Button disabled>disabled button</Button>);
    const btn = screen.getByRole("button", { name: "disabled button" });
    expect(btn.className).toContain("disabled:opacity-50");
    expect(btn.className).toContain("disabled:pointer-events-none");
  });
});