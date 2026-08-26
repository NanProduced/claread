/**
 * Reader image chrome noise reduction + display math flat wrapper.
 *
 * F5: 复制链接/修改链接 reveal on hover / keyboard focus (group + focus-within,
 * same pattern as sidebar-rail); caption shows alt text only when non-empty and
 * the image loaded. F4/F7: display math wrapper is flat centered (my-3, no card).
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
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";

function imgValue(altText: string): Descendant[] {
  return [
    {
      type: READER_IMAGE_BLOCK_TYPE,
      id: "block:img_el_1",
      children: [
        {
          type: READER_IMAGE_TYPE,
          id: "img_el_1",
          children: [{ text: "" }],
          data: {
            sourceUrl: "https://example.com/a.png",
            effectiveUrl: "https://example.com/a.png",
            altText,
            title: null,
            positionKind: "standalone",
            stableBlockId: "img1",
            parentStableBlockId: null,
          },
        },
      ] as Descendant[],
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

async function renderLoadedImage(altText: string) {
  const { container } = render(<Harness value={imgValue(altText)} />);
  const img = container.querySelector('[data-reader-image="true"] img');
  expect(img).not.toBeNull();
  await act(async () => {
    fireEvent(img as Element, new Event("load"));
  });
  return container;
}

afterEach(cleanup);

describe("Reader image chrome hover-reveal (F5)", () => {
  it("复制链接 reveals on group-hover / focus-within / focus-visible", async () => {
    await renderLoadedImage("chart alt");
    const btn = screen.getByRole("button", { name: "复制链接" });
    expect(btn.className).toContain("opacity-0");
    expect(btn.className).toContain("group-hover:opacity-100");
    expect(btn.className).toContain("group-focus-within:opacity-100");
    expect(btn.className).toContain("focus-visible:opacity-100");
  });

  it("修改链接 reveals on hover / focus with keyboard reachable", async () => {
    await renderLoadedImage("");
    const btn = screen.getByRole("button", { name: "修改链接" });
    expect(btn.className).toContain("opacity-0");
    expect(btn.className).toContain("group-hover:opacity-100");
    expect(btn.className).toContain("group-focus-within:opacity-100");
    expect(btn.className).toContain("focus-visible:opacity-100");
  });
});

describe("Reader image caption (F5)", () => {
  it("shows alt caption under loaded image", async () => {
    const container = await renderLoadedImage("A chart of results");
    const caption = container.querySelector('[data-reader-image-caption="true"]');
    expect(caption?.textContent).toBe("A chart of results");
    // left-aligned small soft ink style
    expect(caption?.className).toContain("text-xs");
    expect(caption?.className).toContain("text-ink-soft");
  });

  it("no caption while loading or when alt is empty", async () => {
    const { container } = render(<Harness value={imgValue("")} />);
    expect(container.querySelector('[data-reader-image-caption="true"]')).toBeNull();
    const img = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      fireEvent(img as Element, new Event("load"));
    });
    expect(container.querySelector('[data-reader-image-caption="true"]')).toBeNull();
  });

  it("no caption on load failure", async () => {
    const { container } = render(<Harness value={imgValue("broken alt")} />);
    const img = container.querySelector('[data-reader-image="true"] img');
    await act(async () => {
      fireEvent(img as Element, new Event("error"));
    });
    expect(container.querySelector('[data-reader-image-caption="true"]')).toBeNull();
  });
});

describe("Reader display math flat centered wrapper (F4/F7)", () => {
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
