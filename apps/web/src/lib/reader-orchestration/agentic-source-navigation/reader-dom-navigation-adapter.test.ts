/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createReaderDomNavigationAdapter } from "./reader-dom-navigation-adapter";

function mountPlateBody(): HTMLDivElement {
  const body = document.createElement("div");
  body.className = "reader-record-plate-document";
  document.body.appendChild(body);
  return body;
}

function addParagraph(
  parent: HTMLElement,
  unitId: string,
  opts?: { unitStart?: boolean; text?: string },
): HTMLParagraphElement {
  const p = document.createElement("p");
  p.setAttribute("data-reader-record-node", "paragraph");
  p.setAttribute("data-unit-id", unitId);
  if (opts?.unitStart) {
    p.setAttribute("data-reader-record-unit-start", "true");
  }
  p.textContent = opts?.text ?? `unit ${unitId}`;
  p.scrollIntoView = vi.fn();
  parent.appendChild(p);
  return p;
}

function addAnchor(parent: HTMLElement, segmentId: string): HTMLElement {
  const span = document.createElement("span");
  span.setAttribute("data-anchor-segment-id", segmentId);
  span.textContent = `seg ${segmentId}`;
  span.scrollIntoView = vi.fn();
  parent.appendChild(span);
  return span;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("reader-dom-navigation-adapter (public resolveAndScroll only)", () => {
  it("22. only searches inside .reader-record-plate-document", () => {
    const chrome = document.createElement("div");
    chrome.className = "chrome";
    const outside = document.createElement("span");
    outside.setAttribute("data-anchor-segment-id", "s-outside");
    outside.scrollIntoView = vi.fn();
    chrome.appendChild(outside);
    document.body.appendChild(chrome);

    const plate = mountPlateBody();
    const inside = addAnchor(plate, "s-inside");

    const adapter = createReaderDomNavigationAdapter(document);
    expect(
      adapter.resolveAndScroll([
        { mode: "anchor_segment", targetId: "s-outside" },
      ]),
    ).toBeNull();
    expect(outside.scrollIntoView).not.toHaveBeenCalled();

    expect(
      adapter.resolveAndScroll([
        { mode: "anchor_segment", targetId: "s-inside" },
      ]),
    ).toEqual({ mode: "anchor_segment", targetId: "s-inside" });
    expect(inside.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("23. ignores chrome/rail elements with same data-unit-id", () => {
    const rail = document.createElement("div");
    rail.className = "reader-record-mini-rail";
    const railPara = document.createElement("button");
    railPara.setAttribute("data-unit-id", "u1");
    railPara.setAttribute("data-reader-record-node", "paragraph");
    railPara.scrollIntoView = vi.fn();
    rail.appendChild(railPara);
    document.body.appendChild(rail);

    const plate = mountPlateBody();
    const bodyPara = addParagraph(plate, "u1", { unitStart: true });

    const adapter = createReaderDomNavigationAdapter(document);
    const hit = adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]);
    expect(hit).toEqual({ mode: "unit", targetId: "u1" });
    expect(bodyPara.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(railPara.scrollIntoView).not.toHaveBeenCalled();
  });

  it("24. prefers unit-start paragraph", () => {
    const plate = mountPlateBody();
    const first = addParagraph(plate, "u1", { unitStart: false });
    const start = addParagraph(plate, "u1", { unitStart: true });

    const adapter = createReaderDomNavigationAdapter(document);
    const hit = adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]);
    expect(hit).toEqual({ mode: "unit", targetId: "u1" });
    expect(start.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(first.scrollIntoView).not.toHaveBeenCalled();
  });

  it("25. without unit-start uses first matching paragraph", () => {
    const plate = mountPlateBody();
    const first = addParagraph(plate, "u1", { unitStart: false });
    const second = addParagraph(plate, "u1", { unitStart: false });

    const adapter = createReaderDomNavigationAdapter(document);
    const hit = adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]);
    expect(hit).toEqual({ mode: "unit", targetId: "u1" });
    expect(first.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(second.scrollIntoView).not.toHaveBeenCalled();
  });

  it("26. special-character ids match by attribute (no selector injection)", () => {
    const plate = mountPlateBody();
    const evilId = 's1"] body { display:none } [data-x="';
    const el = addAnchor(plate, evilId);

    const adapter = createReaderDomNavigationAdapter(document);
    expect(() =>
      adapter.resolveAndScroll([
        { mode: "anchor_segment", targetId: evilId },
      ]),
    ).not.toThrow();
    expect(el.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("27. scrolls only once on first hit", () => {
    const plate = mountPlateBody();
    const s1 = addAnchor(plate, "s1");
    const s2 = addAnchor(plate, "s2");
    const u1 = addParagraph(plate, "u1", { unitStart: true });

    const adapter = createReaderDomNavigationAdapter(document);
    adapter.resolveAndScroll([
      { mode: "anchor_segment", targetId: "s1" },
      { mode: "anchor_segment", targetId: "s2" },
      { mode: "unit", targetId: "u1" },
    ]);

    expect(s1.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(s2.scrollIntoView).not.toHaveBeenCalled();
    expect(u1.scrollIntoView).not.toHaveBeenCalled();
  });

  it("28. no scroll when nothing matches", () => {
    const plate = mountPlateBody();
    const s1 = addAnchor(plate, "s1");
    const adapter = createReaderDomNavigationAdapter(document);
    const hit = adapter.resolveAndScroll([
      { mode: "anchor_segment", targetId: "missing" },
      { mode: "unit", targetId: "missing-unit" },
    ]);
    expect(hit).toBeNull();
    expect(s1.scrollIntoView).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });

  it("unit mode moves real focus to the navigable block", () => {
    const plate = mountPlateBody();
    const start = addParagraph(plate, "u1", { unitStart: true });

    const adapter = createReaderDomNavigationAdapter(document);
    const hit = adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]);

    expect(hit).toEqual({ mode: "unit", targetId: "u1" });
    expect(start.scrollIntoView).toHaveBeenCalledTimes(1);
    // jsdom only moves document.activeElement for elements that can take
    // focus — a plain paragraph needs the adapter's tabindex="-1" stamping.
    expect(document.activeElement).toBe(start);
    expect(start.getAttribute("tabindex")).toBe("-1");
  });

  it("anchor mode scrolls the precise segment but focuses its navigable block", () => {
    const plate = mountPlateBody();
    const para = addParagraph(plate, "u1", { unitStart: true, text: "" });
    const anchor = addAnchor(para, "s-in-para");

    const adapter = createReaderDomNavigationAdapter(document);
    const hit = adapter.resolveAndScroll([
      { mode: "anchor_segment", targetId: "s-in-para" },
    ]);

    expect(hit).toEqual({ mode: "anchor_segment", targetId: "s-in-para" });
    expect(anchor.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(para);
    expect(anchor.getAttribute("tabindex")).toBeNull();
  });

  it("focuses the hit element itself when no navigable block wraps it", () => {
    const plate = mountPlateBody();
    const orphan = addAnchor(plate, "s-orphan");

    const adapter = createReaderDomNavigationAdapter(document);
    adapter.resolveAndScroll([{ mode: "anchor_segment", targetId: "s-orphan" }]);

    expect(document.activeElement).toBe(orphan);
    expect(orphan.getAttribute("tabindex")).toBe("-1");
  });

  it("does not stamp tabindex on natively focusable focus owners", () => {
    const plate = mountPlateBody();
    const button = document.createElement("button");
    button.setAttribute("data-reader-record-node", "paragraph");
    button.setAttribute("data-unit-id", "u1");
    button.setAttribute("data-reader-record-unit-start", "true");
    button.scrollIntoView = vi.fn();
    plate.appendChild(button);

    const adapter = createReaderDomNavigationAdapter(document);
    adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]);

    expect(document.activeElement).toBe(button);
    expect(button.hasAttribute("tabindex")).toBe(false);
  });

  it("re-navigation after the first focus still moves focus to the new target", () => {
    const plate = mountPlateBody();
    const first = addParagraph(plate, "u1", { unitStart: true });
    const second = addParagraph(plate, "u2", { unitStart: true });

    const adapter = createReaderDomNavigationAdapter(document);
    adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]);
    expect(document.activeElement).toBe(first);

    adapter.resolveAndScroll([{ mode: "unit", targetId: "u2" }]);
    expect(document.activeElement).toBe(second);
  });

  it("SSR/Node: adapter with no Document fail-closes without throw", () => {
    // Explicit null forces no document access even under jsdom.
    const adapter = createReaderDomNavigationAdapter(null);
    expect(() =>
      adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]),
    ).not.toThrow();
    expect(
      adapter.resolveAndScroll([{ mode: "unit", targetId: "u1" }]),
    ).toBeNull();
  });
});
