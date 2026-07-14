import { describe, expect, it } from "vitest";
import { primitiveFocusRing, primitiveSurface } from "./shared";

/**
 * Locks the primitive focus-ring class to the semantic `focus-ring` token
 * so future edits to the focus recipe cannot accidentally regress to a raw
 * color literal.
 */
describe("primitives/shared", () => {
  it("routes the canonical focus ring through the focus-ring semantic token", () => {
    expect(primitiveFocusRing).toContain("ring-focus-ring/");
    expect(primitiveFocusRing).toContain("ring-offset-surface-canvas");
  });

  it("keeps the surface primitive routed through border-subtle + ink", () => {
    // `primitiveSurface` still composes from the existing foundation aliases
    // today; this test pins the boundary so migrating it later to
    // `border-subtle` / `text-primary` surfaces the diff intentionally.
    expect(primitiveSurface).toContain("border-hairline");
    expect(primitiveSurface).toContain("text-ink");
  });
});
