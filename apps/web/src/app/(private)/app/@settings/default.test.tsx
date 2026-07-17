/** @vitest-environment node */

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import SettingsSlotDefault from "./default";

describe("@settings/default", () => {
  it("renders null — no modal overlay when settings route is not intercepted", () => {
    const html = renderToString(<SettingsSlotDefault />);
    // React renderToString of a component returning null produces ""
    expect(html).toBe("");
  });

  it("produces no visible DOM nodes", () => {
    const html = renderToString(<SettingsSlotDefault />);
    expect(html).not.toContain("div");
    expect(html).not.toContain("span");
    expect(html).not.toContain("dialog");
  });
});
