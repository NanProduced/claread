/**
 * Reader image state surface — compact chrome, stable geometry, captions.
 *
 * - Loaded standalone image: absolute top-right compact neutral toolbar
 *   (icon-only + Tooltip primitive), revealed on hover / focus-within only,
 *   never taking body height; caption comes from explicit Markdown title.
 * - Loading reserves a stable full-width block slot (standalone) while inline
 *   stays compact — standalone full-width styles never leak into text flow.
 * - Failed: 「图片无法加载」 primary + alt secondary + 重新加载 / 复制链接 /
 *   修改链接；retry remounts the same safe URL verbatim.
 * - Unsafe: fail-closed friendly copy without raw source/effective URL.
 * - Display math wrapper is flat centered (my-3, no card).
 */
/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Descendant } from "platejs";
import { Editor, EditorContainer } from "@/components/ui/editor";
import { Plate, usePlateEditor } from "platejs/react";
import { ReaderRecordPlateKit } from "@/components/editor/plugins/reader-plate-kit";
import {
  ReaderFrozenImageOverrideContext,
} from "@/components/editor/plugins/reader-blocks-kit";
import {
  READER_IMAGE_BLOCK_TYPE,
  READER_IMAGE_TYPE,
  READER_MATH_BLOCK_TYPE,
  READER_PARAGRAPH_TYPE,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";

interface ImgSpec {
  altText?: string;
  title?: string | null;
  positionKind?: "standalone" | "inline";
  sourceUrl?: string;
  effectiveUrl?: string | null;
}

function imgValue({
  altText = "",
  title = null,
  positionKind = "standalone",
  sourceUrl = "https://example.com/a.png",
  effectiveUrl = "https://example.com/a.png",
}: ImgSpec = {}): Descendant[] {
  const imageNode = {
    type: READER_IMAGE_TYPE,
    id: positionKind === "inline" ? "img_inline_1" : "img_el_1",
    children: [{ text: "" }],
    data: {
      sourceUrl,
      effectiveUrl,
      altText,
      title,
      positionKind,
      stableBlockId: positionKind === "inline" ? "p1" : "img1",
      parentStableBlockId: positionKind === "inline" ? "img1" : null,
      ...(positionKind === "inline" ? { inlineOrdinal: 0 } : {}),
    },
  };
  if (positionKind === "inline") {
    return [
      {
        type: READER_PARAGRAPH_TYPE,
        id: "p1",
        children: [{ text: "before " }, imageNode, { text: " after" }],
      } as unknown as Descendant,
    ];
  }
  return [
    {
      type: READER_IMAGE_BLOCK_TYPE,
      id: "block:img_el_1",
      children: [imageNode] as Descendant[],
      data: { stableBlockId: "img1", parentStableBlockId: null },
    } as unknown as Descendant,
  ];
}

function mathDisplayValue(): Descendant[] {
  return [
    {
      type: READER_MATH_BLOCK_TYPE,
      id: "block:math1",
      children: [
        {
          type: "reader_math_inline",
          id: "math1",
          children: [{ text: "" }],
          data: { latex: "E = mc^2", display: true },
        },
      ] as Descendant[],
      data: { stableBlockId: "math1", parentStableBlockId: null },
    } as unknown as Descendant,
  ];
}

function Harness({ value }: { value: Descendant[] }) {
  const editor = usePlateEditor(
    { plugins: [...ReaderRecordPlateKit], value: value as never[] },
    [],
  );
  return (
    <ReaderFrozenImageOverrideContext.Provider
      value={{
        canEdit: true,
        stableDocumentId: "stable_doc_1",
        upsert: async () => ({ ok: true }),
        remove: async () => ({ ok: true }),
      }}
    >
      <Plate editor={editor} readOnly>
        <EditorContainer className="h-auto overflow-visible bg-transparent px-0 py-0">
          <Editor readOnly disableDefaultStyles className="space-y-2 px-0 py-0 outline-none" />
        </EditorContainer>
      </Plate>
    </ReaderFrozenImageOverrideContext.Provider>
  );
}

async function renderLoadedImage(spec: ImgSpec = {}) {
  const { container } = render(<Harness value={imgValue(spec)} />);
  const img = container.querySelector('[data-reader-image="true"] img');
  expect(img).not.toBeNull();
  await act(async () => {
    fireEvent(img as Element, new Event("load"));
  });
  return container;
}

afterEach(cleanup);

describe("Reader image compact toolbar (loaded)", () => {
  it("toolbar is absolutely positioned top-right, out of flow, revealed on hover / focus-within", async () => {
    const container = await renderLoadedImage({ altText: "chart alt" });
    const toolbar = container.querySelector('[data-reader-image-toolbar="true"]');
    expect(toolbar).not.toBeNull();
    // absolute positioning: chrome never takes body height
    expect(toolbar?.className).toContain("absolute");
    expect(toolbar?.className).toContain("top-");
    expect(toolbar?.className).toContain("right-");
    // reveal on hover / keyboard focus only
    expect(toolbar?.className).toContain("opacity-0");
    expect(toolbar?.className).toContain("group-hover:opacity-100");
    expect(toolbar?.className).toContain("group-focus-within:opacity-100");
  });

  it("复制链接 is icon-only Tooltip control with aria-label / pointer / hover / focus-visible", async () => {
    await renderLoadedImage({ altText: "chart alt" });
    const btn = screen.getByRole("button", { name: "复制链接" });
    expect(btn.querySelector("svg")).not.toBeNull();
    expect(btn.textContent ?? "").not.toContain("复制链接");
    expect(btn.getAttribute("aria-label")).toBe("复制链接");
    // reuses the Tooltip primitive (Radix trigger carries data-state)
    expect(btn.getAttribute("data-state")).toBe("closed");
    expect(btn.className).toContain("cursor-pointer");
    expect(btn.className).toContain("hover:");
    expect(btn.className).toContain("focus-visible:");
  });

  it("修改链接 is icon-only Tooltip control with aria-label / pointer / hover / focus-visible", async () => {
    await renderLoadedImage({ altText: "" });
    const btn = screen.getByRole("button", { name: "修改链接" });
    expect(btn.querySelector("svg")).not.toBeNull();
    expect(btn.getAttribute("aria-label")).toBe("修改链接");
    expect(btn.getAttribute("data-state")).toBe("closed");
    expect(btn.className).toContain("cursor-pointer");
    expect(btn.className).toContain("hover:");
    expect(btn.className).toContain("focus-visible:");
  });
});

describe("Reader image caption (explicit title, never alt)", () => {
  it("explicit Markdown title renders as visible caption; img keeps no native title tooltip", async () => {
    const container = await renderLoadedImage({
      altText: "the alt",
      title: "The Title",
    });
    const caption = container.querySelector('[data-reader-image-caption="true"]');
    expect(caption?.textContent).toBe("The Title");
    expect(caption?.className).toContain("text-xs");
    expect(caption?.className).toContain("text-ink-soft");
    const img = container.querySelector('[data-reader-image="true"] img');
    expect(img?.getAttribute("title")).toBeNull();
  });

  it("alt-only image renders no caption (alt lives on img alt only)", async () => {
    const container = await renderLoadedImage({ altText: "only alt" });
    expect(
      container.querySelector('[data-reader-image-caption="true"]'),
    ).toBeNull();
    const img = container.querySelector('[data-reader-image="true"] img');
    expect(img?.getAttribute("alt")).toBe("only alt");
  });

  it("no caption while loading", () => {
    const { container } = render(
      <Harness value={imgValue({ altText: "loading alt", title: "T" })} />,
    );
    expect(
      container.querySelector('[data-reader-image-caption="true"]'),
    ).toBeNull();
  });

  it("no caption on load failure", async () => {
    const { container } = render(
      <Harness value={imgValue({ altText: "broken alt", title: "T" })} />,
    );
    const img = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    expect(
      container.querySelector('[data-reader-image-caption="true"]'),
    ).toBeNull();
  });
});

describe("Reader standalone vs inline geometry", () => {
  it("standalone loading reserves a stable full-width block slot", () => {
    const { container } = render(<Harness value={imgValue({ altText: "a" })} />);
    // outer span 也带 data-image-state，定位到真正的占位盒
    const placeholder = container.querySelector(
      '[data-reader-image="true"] > span > [data-image-state="loading"]',
    );
    expect(placeholder).not.toBeNull();
    expect(placeholder?.className).toContain("w-full");
    expect(placeholder?.className).toContain("min-h-[4.5rem]");
  });

  it("inline image stays compact: no standalone full-width styles in text flow", () => {
    const { container } = render(
      <Harness value={imgValue({ altText: "a", positionKind: "inline" })} />,
    );
    const outer = container.querySelector('[data-reader-image="true"]');
    expect(outer?.className).toContain("inline-block");
    expect((outer?.className ?? "").split(/\s+/)).not.toContain("w-full");
    const placeholder = container.querySelector(
      '[data-reader-image="true"] > span > [data-image-state="loading"]',
    );
    expect(placeholder).not.toBeNull();
    expect((placeholder?.className ?? "").split(/\s+/)).not.toContain("w-full");
    const img = container.querySelector('[data-reader-image="true"] img');
    expect((img?.className ?? "").split(/\s+/)).not.toContain("w-full");
  });
});

describe("Reader image failed state", () => {
  it("shows 图片无法加载 primary with alt secondary and visible recovery actions", async () => {
    const { container } = render(
      <Harness value={imgValue({ altText: "broken alt" })} />,
    );
    const img = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    const failed = container.querySelector('[data-image-state="load_failed"]');
    expect(failed?.textContent).toContain("图片无法加载");
    expect(failed?.textContent).toContain("broken alt");
    const primaryAt = failed?.textContent?.indexOf("图片无法加载") ?? -1;
    const altAt = failed?.textContent?.indexOf("broken alt") ?? -1;
    expect(altAt).toBeGreaterThan(primaryAt);
    expect(
      screen.getByRole("button", { name: "重新加载" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "复制链接" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "修改链接" }),
    ).toBeTruthy();
    // recovery actions are not hover-hidden
    const retry = screen.getByRole("button", { name: "重新加载" });
    expect(retry.className).not.toContain("opacity-0");
    // failed state keeps no live img[src]
    expect(container.querySelector('[data-reader-image="true"] img[src]')).toBeNull();
  });

  it("empty alt keeps 图片加载失败 guidance copy", async () => {
    const { container } = render(<Harness value={imgValue({ altText: "" })} />);
    const img = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    expect(container.textContent).toContain("图片无法加载");
    expect(container.textContent).toContain("图片加载失败");
  });

  it("重新加载 remounts the same safe URL verbatim (no URL rewrite)", async () => {
    const { container } = render(
      <Harness value={imgValue({ altText: "retry alt" })} />,
    );
    const firstImg = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      fireEvent(firstImg as Element, new Event("error"));
    });
    expect(
      container.querySelector('[data-image-state="load_failed"]'),
    ).not.toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    });
    const retryImg = container.querySelector('[data-reader-image="true"] img');
    expect(retryImg).not.toBeNull();
    expect(retryImg).not.toBe(firstImg);
    expect(retryImg?.getAttribute("src")).toBe("https://example.com/a.png");
    expect(
      container.querySelector('[data-image-state="loading"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-image-state="load_failed"]'),
    ).toBeNull();
  });
});

describe("Reader image unsafe state", () => {
  it("fail-closed: friendly copy only, no raw source/effective URL, 修改链接 entry", () => {
    const { container } = render(
      <Harness
        value={imgValue({
          altText: "alt",
          sourceUrl: "https://example.com/source.png",
          effectiveUrl: "javascript:alert(1)",
        })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("链接不安全");
    // normal surface never shows raw source/effective URLs (edit panel only)
    expect(container.textContent).not.toContain("javascript:alert(1)");
    expect(container.textContent).not.toContain("https://example.com/source.png");
    expect(
      screen.getByRole("button", { name: "修改链接" }),
    ).toBeTruthy();
  });
});

describe("Reader image native load (hidden+lazy deadlock repair)", () => {
  it("unloaded image may stay hidden but must not use loading=lazy", () => {
    // The img is display:none until onLoad fires; Chromium never fetches a
    // lazy image with no layout box, so hidden+lazy deadlocks at 图片加载中….
    // Hiding before load is allowed; lazy loading is not.
    const { container } = render(
      <Harness value={imgValue({ altText: "native load alt" })} />,
    );
    const img = container.querySelector('[data-reader-image="true"] img');
    expect(img).not.toBeNull();
    expect((img as HTMLImageElement).getAttribute("loading")).not.toBe("lazy");
  });
});

describe("Reader display math flat centered wrapper", () => {
  it("wrapper keeps my-3 centering and overflow, drops card chrome", () => {
    const { container } = render(<Harness value={mathDisplayValue()} />);
    const wrapper = container.querySelector('[data-reader-math-content="true"]');
    expect(wrapper).not.toBeNull();
    expect(wrapper?.className).toContain("my-3");
    expect(wrapper?.className).toContain("overflow-x-auto");
    expect(wrapper?.className).not.toContain("rounded-lg");
    expect(wrapper?.className).not.toContain("bg-surface-raised/30");
    expect(wrapper?.className).not.toContain("px-3");
    expect(wrapper?.className).not.toContain("py-2");
  });
});
