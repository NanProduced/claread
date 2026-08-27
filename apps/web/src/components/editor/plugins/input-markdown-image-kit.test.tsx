/** @vitest-environment jsdom */

/**
 * Input Markdown Image Kit 合同测试（G1′-A）。
 *
 * 覆盖：
 * - §10.2 允许/拒绝参数矩阵逐行断言（validator + 组件渲染 img.src 两个层面：
 *   allow 行 img[src] === 原始 URL；reject 行永不渲染 img[src]，零网络请求）。
 * - 组件四态：loading / loaded / load_failed / unsafe（§11.1）。
 * - native img 属性完整（lazy / async / no-referrer / alt / title）。
 * - URL 编辑：保存只更新 URL（alt/title 保留，节点顺序不动）；取消零变化。
 * - 输入端 options 级 round-trip 矩阵（standalone / inline / consecutive /
 *   empty alt / title / strong·em·delete wrapped）。
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createPlateEditor, Plate, PlateContent } from "platejs/react";
import { MarkdownPlugin } from "@platejs/markdown";
import type { Descendant } from "platejs";

import { prepareClipboardHtml } from "@/lib/clipboard/prepare-clipboard-html";

import {
  INPUT_MARKDOWN_PLUGIN_OPTIONS,
  InputMarkdownImagePlugin,
  isLoadableImageUrl,
  type InputImageNode,
} from "./input-markdown-image-kit";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// §10.2 允许 / 拒绝参数矩阵（与后端共享的测试真值表，双端逐行一致）
// ---------------------------------------------------------------------------

const ALLOW_URLS = [
  "https://example.com/a.png",
  "http://example.com/a.png",
  "HTTP://Example.COM/a.png",
  "http://example.com:65535/a.png",
  "http://example.com:8080/a.png?q=1#f",
  "http://127.0.0.1/a.png",
  "http://[::1]:8080/a.png",
  "https://xn--r8jz45g.jp/a.png",
  "http://example.com",
  // %20 是编码空格，不是裸空格（rule 2 不命中）
  "https://example.com/a%20b.png",
  // path 中 %5C 是编码反斜杠，不是原始反斜杠（rule 3 不命中）
  "http://example.com/%5C@evil.com/a.png",
];

const REJECT_URLS: Array<{ url: string; rule: string }> = [
  // 规则 1（非 string）
  { url: "", rule: "空串（无 http(s) 前缀）" },
  // 规则 1（首尾空白；双端 parser 均会剥除，trim 检查承重）
  { url: "  https://example.com/a.png  ", rule: "trim 不等" },
  // 规则 4（相对路径 / protocol-relative）
  { url: "/a.png", rule: "相对路径" },
  { url: "a.png", rule: "相对路径" },
  { url: "//example.com/a.png", rule: "protocol-relative" },
  // 规则 4（词法前缀必须先于 parser：Node 会把 http:foo 解析为 host）
  { url: "http:foo", rule: "http:foo" },
  { url: "https:foo", rule: "https:foo" },
  // 规则 7（缺 hostname）
  { url: "http://", rule: "缺 hostname" },
  { url: "https:///", rule: "缺 hostname" },
  // 规则 8（credentials）
  { url: "http://user:pass@example.com/a.png", rule: "credentials" },
  { url: "http://user@example.com/a.png", rule: "credentials" },
  // 规则 4（非 http(s) scheme）
  { url: "javascript:alert(1)", rule: "javascript:" },
  { url: "data:image/png;base64,AAAA", rule: "data:" },
  { url: "file:///etc/passwd", rule: "file:" },
  { url: "blob:https://x/y", rule: "blob:" },
  { url: "mailto:a@b.com", rule: "mailto:" },
  // 规则 2（控制字符 / 裸空格：词法扫描，不依赖 parser）
  { url: "http://exa\0mple.com/a.png", rule: "NUL 控制字符" },
  { url: "http://example.com/a\x01.png", rule: "path 控制字符" },
  { url: "http://exa mple.com/a.png", rule: "hostname 裸空格" },
  { url: "http://example.com/a b.png", rule: "path 裸空格" },
  // 规则 3（原始反斜杠：authority/path 解释分叉）
  { url: "http://example.com\\@evil.com/a.png", rule: "原始反斜杠 @" },
  { url: "http://example.com\\evil/a.png", rule: "原始反斜杠" },
  // 规则 5/6（端口：非数字 / 越界 / 负端口）
  { url: "http://example.com:bad/a.png", rule: "非数字端口" },
  { url: "http://example.com:65536/a.png", rule: "越界端口 65536" },
  { url: "http://example.com:99999/a.png", rule: "越界端口 99999" },
  { url: "http://example.com:-1/a.png", rule: "负端口" },
  // 规则 5（new URL 抛错：畸形 IPv6，未闭合括号）
  { url: "http://[::1", rule: "畸形 IPv6（new URL throw）" },
];

describe("isLoadableImageUrl（§10.1 八规则 fail-closed 判定）", () => {
  it.each(ALLOW_URLS)("允许：%s", (url) => {
    expect(isLoadableImageUrl(url)).toBe(true);
  });

  it.each(REJECT_URLS)("拒绝（%s）：%j", ({ url }) => {
    expect(isLoadableImageUrl(url)).toBe(false);
  });

  it("规则 1：非 string 输入拒绝", () => {
    expect(isLoadableImageUrl(null)).toBe(false);
    expect(isLoadableImageUrl(undefined)).toBe(false);
    expect(isLoadableImageUrl(123)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 组件渲染层：img.src 赋值前 fail-closed（reject 项零 img[src]）
// ---------------------------------------------------------------------------

function renderInputImage(url: string, alt = "a", title?: string) {
  const imageNode: InputImageNode = {
    type: "img",
    url,
    ...(title ? { title } : {}),
    caption: [{ text: alt }],
    children: [{ text: "" }],
  };
  const editor = createPlateEditor({
    plugins: [
      MarkdownPlugin.configure({ options: INPUT_MARKDOWN_PLUGIN_OPTIONS }),
      InputMarkdownImagePlugin,
    ],
    value: [{ type: "p", children: [imageNode] }] as never[],
  });
  const utils = render(
    <Plate editor={editor}>
      <PlateContent />
    </Plate>,
  );
  return { editor, ...utils };
}

describe("InputImageElement trust boundary（组件渲染层）", () => {
  it.each(ALLOW_URLS)(
    "允许行渲染真实 img 且 src 为原始 URL（不用 parser 规范化串）：%s",
    (url) => {
      const { container } = renderInputImage(url);
      const img = container.querySelector("img");
      expect(img).not.toBeNull();
      expect(img?.getAttribute("src")).toBe(url);
    },
  );

  it.each(REJECT_URLS)(
    "拒绝行永不渲染 img[src]（%s）：%j",
    ({ url }) => {
      const { container } = renderInputImage(url);
      expect(container.querySelector("img[src]")).toBeNull();
    },
  );

  it("native img 属性完整：async / no-referrer / alt，无 lazy；无原生 title tooltip", () => {
    const { container } = renderInputImage(
      "https://example.com/a.png",
      "alt text",
      "The Title",
    );
    const img = container.querySelector("img");
    // NARROW-REPAIR 契约：移除 loading=lazy（与 Reader 一致），保留状态机
    expect(img?.getAttribute("loading")).toBeNull();
    expect(img?.getAttribute("decoding")).toBe("async");
    expect(img?.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(img?.getAttribute("alt")).toBe("alt text");
    // R2 契约：移除原生 title tooltip，显式 title 只作为可见 caption
    expect(img?.getAttribute("title")).toBeNull();
  });

  it("防死锁回归：加载前隐藏的 img 不得携带 loading=lazy（真实浏览器需能发起原生请求）", () => {
    // img 在 onLoad 前是 display:none；Chromium 不会请求没有布局盒的 lazy
    // 图片，hidden+lazy 会死锁在「图片加载中…」。允许加载前隐藏，禁止 lazy。
    const { container } = renderInputImage("https://example.com/a.png", "alt");
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.className).toContain("hidden");
    expect(img?.getAttribute("loading")).not.toBe("lazy");
  });
});

describe("InputImageElement 四态（§11.1）", () => {
  it("loading → loaded：初始 loading 占位，onLoad 后显示图片", () => {
    const { container } = renderInputImage("https://example.com/a.png");
    expect(
      container.querySelector("[data-image-state='loading']"),
    ).not.toBeNull();
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    act(() => {
      fireEvent(img as Element, new Event("load"));
    });
    expect(
      container.querySelector("[data-image-state='loaded']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-image-state='loading']"),
    ).toBeNull();
  });

  it("load_failed：图片无法加载为主、alt 为次级，提供重新加载/复制链接/修改链接", () => {
    const { container } = renderInputImage(
      "https://example.com/a.png",
      "alt text",
    );
    const img = container.querySelector("img");
    act(() => {
      fireEvent(img as Element, new Event("error"));
    });
    const failed = container.querySelector("[data-image-state='load_failed']");
    expect(failed).not.toBeNull();
    expect(failed?.textContent).toContain("图片无法加载");
    expect(failed?.textContent).toContain("alt text");
    expect(
      (failed?.textContent?.indexOf("图片无法加载") ?? -1),
    ).toBeLessThan(failed?.textContent?.indexOf("alt text") ?? -1);
    expect(screen.getByRole("button", { name: "重新加载" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制链接" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "修改链接" })).toBeTruthy();
    // 失败后不再挂载带 src 的 img
    expect(container.querySelector("img[src]")).toBeNull();
  });

  it("load_failed 空 alt：主文案 + 图片加载失败引导", () => {
    const { container } = renderInputImage("https://example.com/a.png", "");
    const img = container.querySelector("img");
    act(() => {
      fireEvent(img as Element, new Event("error"));
    });
    expect(container.textContent).toContain("图片无法加载");
    expect(container.textContent).toContain("图片加载失败");
  });

  it("load_failed 复制链接：写入 effective URL（原样字符串）", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    renderInputImage("https://example.com/a.png", "alt");
    const img = screen.getByRole("img", { hidden: true });
    act(() => {
      fireEvent(img, new Event("error"));
    });
    fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
    expect(writeText).toHaveBeenCalledWith("https://example.com/a.png");
  });

  it("unsafe：不渲染 img，普通状态不显示 raw URL；进入编辑面板后才显示并可编辑", () => {
    const { container } = renderInputImage("javascript:alert(1)");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("链接不安全");
    // NARROW-REPAIR 契约：普通表面不显示 raw URL（与 Reader 一致）
    expect(container.textContent).not.toContain("javascript:alert(1)");
    expect(screen.getByRole("button", { name: "修改链接" })).toBeTruthy();
    // 仅点击「修改链接」进入显式编辑面板后允许显示和编辑 URL
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const input = screen.getByLabelText("图片链接") as HTMLInputElement;
    expect(input.value).toBe("javascript:alert(1)");
  });
});

// ---------------------------------------------------------------------------
// R2 · 紧凑 chrome 与 caption（与 Reader 图片状态语言一致）
// ---------------------------------------------------------------------------

describe("InputImageElement 紧凑 chrome 与 caption（R2）", () => {
  it("loaded：右上角绝对定位紧凑 toolbar，hover/focus-within 才显示，不占正文高度", () => {
    const { container } = renderInputImage("https://example.com/a.png", "alt");
    const img = container.querySelector("img");
    act(() => {
      fireEvent(img as Element, new Event("load"));
    });
    const toolbar = container.querySelector("[data-image-toolbar='true']");
    expect(toolbar).not.toBeNull();
    expect(toolbar?.className).toContain("absolute");
    expect(toolbar?.className).toContain("opacity-0");
    expect(toolbar?.className).toContain("group-hover:opacity-100");
    expect(toolbar?.className).toContain("group-focus-within:opacity-100");
    // 修改链接只在 hover toolbar 内，不再长期显示独立按钮
    const editBtn = screen.getByRole("button", { name: "修改链接" });
    expect(editBtn.closest("[data-image-toolbar='true']")).not.toBeNull();
    expect(editBtn.querySelector("svg")).not.toBeNull();
    expect(editBtn.getAttribute("aria-label")).toBe("修改链接");
    // 复用现有 Tooltip primitive（Radix trigger 携带 data-state）
    expect(editBtn.getAttribute("data-state")).toBe("closed");
    expect(editBtn.className).toContain("cursor-pointer");
    expect(editBtn.className).toContain("hover:");
    expect(editBtn.className).toContain("focus-visible:");
    const copyBtn = screen.getByRole("button", { name: "复制链接" });
    expect(copyBtn.closest("[data-image-toolbar='true']")).not.toBeNull();
  });

  it("loading：toolbar 已存在（hover/键盘可达），修改链接不占正文位置", () => {
    const { container } = renderInputImage("https://example.com/a.png", "alt");
    const toolbar = container.querySelector("[data-image-toolbar='true']");
    expect(toolbar).not.toBeNull();
    expect(toolbar?.className).toContain("opacity-0");
    const editBtn = screen.getByRole("button", { name: "修改链接" });
    expect(editBtn.closest("[data-image-toolbar='true']")).not.toBeNull();
  });

  it("caption 来自显式 Markdown title；alt 不自动成为 caption", () => {
    const { container } = renderInputImage(
      "https://example.com/a.png",
      "the alt",
      "The Title",
    );
    const img = container.querySelector("img");
    act(() => {
      fireEvent(img as Element, new Event("load"));
    });
    const caption = container.querySelector("[data-image-caption='true']");
    expect(caption?.textContent).toBe("The Title");
    expect(img?.getAttribute("title")).toBeNull();
    expect(img?.getAttribute("alt")).toBe("the alt");

    const { container: c2 } = renderInputImage(
      "https://example.com/b.png",
      "only alt",
    );
    const img2 = c2.querySelector("img");
    act(() => {
      fireEvent(img2 as Element, new Event("load"));
    });
    expect(c2.querySelector("[data-image-caption='true']")).toBeNull();
  });

  it("重新加载：重新挂载同一安全 URL，不改写；重挂的 img 可重新触发原生加载", () => {
    const { container } = renderInputImage("https://example.com/a.png", "alt");
    const firstImg = container.querySelector("img");
    act(() => {
      fireEvent(firstImg as Element, new Event("error"));
    });
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    });
    const retryImg = container.querySelector("img");
    expect(retryImg).not.toBeNull();
    expect(retryImg).not.toBe(firstImg);
    expect(retryImg?.getAttribute("src")).toBe("https://example.com/a.png");
    // 重挂的 img 不得携带 lazy（真实浏览器中隐藏的 lazy 图片不会发起请求）
    expect(retryImg?.getAttribute("loading")).not.toBe("lazy");
    expect(
      container.querySelector("[data-image-state='loading']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-image-state='load_failed']"),
    ).toBeNull();
    // 重挂的 img 是活的：其 load 事件能把组件推进 loaded（重试可恢复）
    act(() => {
      fireEvent(retryImg as Element, new Event("load"));
    });
    expect(
      container.querySelector("[data-image-state='loaded']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-image-state='loading']"),
    ).toBeNull();
  });
});

/** 从编辑器树中找到第一个 img 节点（Slate normalize 会在 inline void 周围插入空 text）。 */
function findImgNode(editor: {
  children: Array<{ children?: Array<Record<string, unknown>> }>;
}): InputImageNode {
  for (const block of editor.children) {
    for (const child of block.children ?? []) {
      if (child.type === "img") return child as unknown as InputImageNode;
    }
  }
  throw new Error("img node not found in editor value");
}

describe("InputImageElement URL 编辑（冻结前本地编辑态）", () => {
  it("保存：只更新当前 img 节点 URL，alt/title 保留，serialize 输出新 URL", () => {
    const { editor, container } = renderInputImage(
      "https://example.com/a.png",
      "alt text",
      "The Title",
    );
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const input = screen.getByLabelText("图片链接") as HTMLInputElement;
    expect(input.value).toBe("https://example.com/a.png");
    fireEvent.change(input, {
      target: { value: "https://example.com/b.png" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    // 节点级断言：URL 更新，alt（caption）/title 保留
    const updated = findImgNode(editor);
    expect(updated.url).toBe("https://example.com/b.png");
    expect(updated.caption).toEqual([{ text: "alt text" }]);
    expect(updated.title).toBe("The Title");
    // serialize 输出新 URL 的 image syntax
    const md = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(md).toContain('![alt text](https://example.com/b.png "The Title")');
    // 保存后回到非编辑态（URL 变化重置 loading）
    expect(
      container.querySelector("[data-image-state='loading']"),
    ).not.toBeNull();
  });

  it("取消：节点零变化", () => {
    const { editor } = renderInputImage(
      "https://example.com/a.png",
      "alt text",
      "The Title",
    );
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const input = screen.getByLabelText("图片链接") as HTMLInputElement;
    fireEvent.change(input, {
      target: { value: "https://example.com/b.png" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    const unchanged = findImgNode(editor);
    expect(unchanged.url).toBe("https://example.com/a.png");
    const md = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(md).toContain('![alt text](https://example.com/a.png "The Title")');
    expect(md).not.toContain("b.png");
  });

  it("编辑控件不进入 serialize 输出", () => {
    const { editor } = renderInputImage("https://example.com/a.png", "alt");
    fireEvent.click(screen.getByRole("button", { name: "修改链接" }));
    const md = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(md).toContain("![alt](https://example.com/a.png)");
    expect(md).not.toContain("图片链接");
    expect(md).not.toContain("保存");
    expect(md).not.toContain("取消");
  });
});

// ---------------------------------------------------------------------------
// 输入端 options 级 round-trip（kit rules 直接合同）
// ---------------------------------------------------------------------------

describe("INPUT_MARKDOWN_PLUGIN_OPTIONS round-trip 矩阵", () => {
  const editor = createPlateEditor({
    plugins: [
      MarkdownPlugin.configure({ options: INPUT_MARKDOWN_PLUGIN_OPTIONS }),
      InputMarkdownImagePlugin,
    ],
  });

  const CASES: Array<{ name: string; md: string; expected: string }> = [
    {
      name: "standalone",
      md: '![a](https://example.com/a.png "T")',
      expected: '![a](https://example.com/a.png "T")',
    },
    {
      name: "mixed inline",
      md: 'before ![a](https://example.com/a.png "T") after',
      expected: 'before ![a](https://example.com/a.png "T") after',
    },
    {
      name: "consecutive",
      md: "left ![a](u1)![b](u2) right",
      expected: "left ![a](u1)![b](u2) right",
    },
    {
      name: "empty alt",
      md: "![](https://example.com/a.png)",
      expected: "![](https://example.com/a.png)",
    },
    {
      name: "strong wrapped",
      md: '**![a](https://example.com/u.png "T")**',
      expected: '**![a](https://example.com/u.png "T")**',
    },
    { name: "em wrapped", md: "*![a](u)*", expected: "*![a](u)*" },
    { name: "delete wrapped", md: "~~![a](u)~~", expected: "~~![a](u)~~" },
    // 补强：semantic destination 边界（语法层归一，语义层保真）
    {
      name: "angle raw space destination",
      md: "![a](<a b.png>)",
      expected: "![a](<a b.png>)",
    },
    {
      name: "raw backslash destination",
      md: "![a](a\\b.png)",
      expected: "![a](a\\b.png)",
    },
    {
      name: "encoded %5C destination",
      md: "![a](a%5Cb.png)",
      expected: "![a](a%5Cb.png)",
    },
    {
      name: "encoded %20 destination",
      md: "![a](a%20b.png)",
      expected: "![a](a%20b.png)",
    },
    {
      name: "scheme case preserved",
      md: "![a](HTTP://Example.COM/a.png)",
      expected: "![a](HTTP://Example.COM/a.png)",
    },
    {
      name: "entity destination（&amp; → 语义 &，serialize 以 escape 重发）",
      md: "![a](a&amp;b.png)",
      expected: "![a](a\\&b.png)",
    },
    {
      name: "escaped destination（\\@ → 语义 @）",
      md: "![a](u\\@x.png)",
      expected: "![a](u@x.png)",
    },
  ];

  it.each(CASES)("$name：deserialize→serialize 保真（无 ZWSP 污染）", ({ md, expected }) => {
    const blocks = editor.getApi(MarkdownPlugin).markdown.deserialize(md);
    editor.tf.setValue(blocks as never[]);
    const out = editor.getApi(MarkdownPlugin).markdown.serialize();
    // 精确断言（去尾部换行）：图片语法、字段、位置、顺序不漂移
    expect(out.trim()).toBe(expected);
    // Slate normalize 在 inline void 周围插入的结构性空文本不得以
    // U+200B 形式进入输出（会污染后端 standalone/inline 分类）
    expect(out).not.toContain("\u200B");
  });

  it("wrapped image 的 marks 捕获在 void 内层 text leaf（不进正文文本）", () => {
    const blocks = editor
      .getApi(MarkdownPlugin)
      .markdown.deserialize("**![a](u)**");
    const json = JSON.stringify(blocks);
    expect(json).toContain('"type":"img"');
    // bold mark 落在 void 内层 text leaf（serialize 重建 ** wrapper 的依据）
    expect(json).toContain('"bold":true');
    // alt 在 caption（stock 承载位置），url 不进入任何 text leaf
    expect(json).toContain('"caption":[{"text":"a"}]');
    expect(json).not.toContain('"text":"u"');
  });
});

// ---------------------------------------------------------------------------
// G1P-B-A · HTML native image：实际 InputMarkdownImagePlugin HTML deserializer
// ---------------------------------------------------------------------------

describe("InputMarkdownImagePlugin HTML deserializer（G1P-B-A）", () => {
  function deserializePreparedHtml(html: string): Descendant[] {
    const editor = createPlateEditor({
      plugins: [InputMarkdownImagePlugin],
    });
    return editor.api.html.deserialize({
      element: prepareClipboardHtml(html),
    }) as Descendant[];
  }

  function collectByType(
    nodes: Descendant[],
    type: string,
  ): Array<Record<string, unknown>> {
    const found: Array<Record<string, unknown>> = [];
    const walk = (ns: Descendant[]) => {
      for (const n of ns) {
        const node = n as Record<string, unknown>;
        if (node.type === type) found.push(node);
        if (Array.isArray(node.children)) {
          walk(node.children as Descendant[]);
        }
      }
    };
    walk(nodes);
    return found;
  }

  function allLeafText(nodes: Descendant[]): string {
    let text = "";
    const walk = (ns: Descendant[]) => {
      for (const n of ns) {
        const node = n as Record<string, unknown>;
        if (typeof node.text === "string") text += node.text;
        if (Array.isArray(node.children)) {
          walk(node.children as Descendant[]);
        }
      }
    };
    walk(nodes);
    return text;
  }

  it("standalone safe IMG → 唯一 typed img 节点，url/caption(alt)/title 保真", () => {
    const fragment = deserializePreparedHtml(
      `<img src="https://example.com/alpha.png" alt="alpha-alt" title="The Title">`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0]).toMatchObject({
      type: "img",
      url: "https://example.com/alpha.png",
      caption: [{ text: "alpha-alt" }],
      title: "The Title",
    });
    expect(imgs[0].children).toEqual([{ text: "" }]);
    // URL/alt/title 不进入普通正文 text leaf
    const leafText = allLeafText(fragment);
    expect(leafText).not.toContain("https://example.com/alpha.png");
    expect(leafText).not.toContain("alpha-alt");
    expect(leafText).not.toContain("The Title");
    // 不再产生 link 降级节点
    expect(collectByType(fragment, "a")).toHaveLength(0);
  });

  it("paragraph inline IMG：留在原段落内，前后文本不漂移", () => {
    const fragment = deserializePreparedHtml(
      `<p>before <img src="https://example.com/i.png" alt="mid"> after</p>`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0]).toMatchObject({
      type: "img",
      url: "https://example.com/i.png",
      caption: [{ text: "mid" }],
    });
    const leafText = allLeafText(fragment);
    expect(leafText).toContain("before");
    expect(leafText).toContain("after");
    expect(leafText).not.toContain("i.png");
  });

  it("consecutive IMG：两张图按源序各自 typed，不重复不合并", () => {
    const fragment = deserializePreparedHtml(
      `<img src="https://example.com/1.png" alt="one"><img src="https://example.com/2.png" alt="two">`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(2);
    expect(imgs[0]).toMatchObject({ url: "https://example.com/1.png" });
    expect(imgs[1]).toMatchObject({ url: "https://example.com/2.png" });
  });

  it("empty alt 保持为空：不回退 URL 文本", () => {
    const fragment = deserializePreparedHtml(
      `<img src="https://example.com/i.png" alt="">`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].caption).toEqual([{ text: "" }]);
    expect(allLeafText(fragment)).not.toContain("i.png");
  });

  it("missing alt 同样为空：不从 URL 虚构", () => {
    const fragment = deserializePreparedHtml(
      `<img src="https://example.com/i.png">`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].caption).toEqual([{ text: "" }]);
  });

  it("title absent 不伪造字段；title=\"\" 保持显式空值", () => {
    const absent = deserializePreparedHtml(
      `<img src="https://example.com/i.png" alt="a">`,
    );
    const absentImgs = collectByType(absent, "img");
    expect(absentImgs).toHaveLength(1);
    expect(absentImgs[0]).not.toHaveProperty("title");

    const explicitEmpty = deserializePreparedHtml(
      `<img src="https://example.com/i.png" alt="a" title="">`,
    );
    const emptyImgs = collectByType(explicitEmpty, "img");
    expect(emptyImgs).toHaveLength(1);
    expect(emptyImgs[0]).toHaveProperty("title", "");
  });

  it("sanitized src（危险 scheme 摘除）：节点保留但无 url，不渲染可加载 img", () => {
    const fragment = deserializePreparedHtml(
      `<img src="data:image/png;base64,AAAA" alt="kept-alt" title="T">`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0]).not.toHaveProperty("url");
    expect(imgs[0].caption).toEqual([{ text: "kept-alt" }]);
    expect(imgs[0]).toHaveProperty("title", "T");

    const editor = createPlateEditor({
      plugins: [
        MarkdownPlugin.configure({ options: INPUT_MARKDOWN_PLUGIN_OPTIONS }),
        InputMarkdownImagePlugin,
      ],
    });
    // 产品插入路径（同 handlePaste 的 editor.tf.insertFragment）
    editor.tf.setValue([{ type: "p", children: [{ text: "" }] }] as never[]);
    editor.tf.insertFragment(fragment as never[]);
    const { container } = render(
      <Plate editor={editor}>
        <PlateContent />
      </Plate>,
    );
    // 无安全 src：不产生带 src 的 img（零网络请求）；alt/title 保留在节点字段（上方已断言）
    expect(container.querySelector("img[src]")).toBeNull();
    expect(container.textContent).toContain("链接不安全");
  });

  it("linked image：wrapper 解包后只剩唯一 typed img，无嵌套/重复 link、image AST", () => {
    const fragment = deserializePreparedHtml(
      `<a href="https://example.com/page"><img src="https://example.com/i.png" alt="a"></a>`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0]).toMatchObject({
      url: "https://example.com/i.png",
      caption: [{ text: "a" }],
    });
    expect(collectByType(fragment, "a")).toHaveLength(0);
  });

  it("figure/figcaption：figcaption 不偷当 alt/title，保持可见邻接内容", () => {
    const fragment = deserializePreparedHtml(
      `<figure><img src="https://example.com/i.png" alt="real-alt"><figcaption>the caption</figcaption></figure>`,
    );
    const imgs = collectByType(fragment, "img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].caption).toEqual([{ text: "real-alt" }]);
    expect(imgs[0]).not.toHaveProperty("title");
    // figcaption 作为可见邻接文本保留，不进 typed 字段
    expect(allLeafText(fragment)).toContain("the caption");
  });

  it("serialize 位置合同：inline/consecutive 经 HTML 路径不漂移、无 ZWSP、无额外顶层空段", () => {
    const editor = createPlateEditor({
      plugins: [
        MarkdownPlugin.configure({ options: INPUT_MARKDOWN_PLUGIN_OPTIONS }),
        InputMarkdownImagePlugin,
      ],
    });
    // 与 handlePaste 一致：空编辑器 + insertFragment（产品插入路径）
    const insertIntoEmpty = (fragment: Descendant[]) => {
      editor.tf.setValue([{ type: "p", children: [{ text: "" }] }] as never[]);
      editor.tf.insertFragment(fragment as never[]);
    };

    const inline = deserializePreparedHtml(
      `<p>before <img src="https://example.com/i.png" alt="a"> after</p>`,
    );
    insertIntoEmpty(inline);
    // HTML white-space collapse 标准语义：img 后连续空白折叠；
    // 位置合同 = 图留在原段落内、源序不漂移。
    expect(editor.getApi(MarkdownPlugin).markdown.serialize().trim()).toBe(
      "before ![a](https://example.com/i.png)after",
    );

    const consecutive = deserializePreparedHtml(
      `<img src="https://example.com/1.png" alt="one"><img src="https://example.com/2.png" alt="two">`,
    );
    insertIntoEmpty(consecutive);
    const out = editor.getApi(MarkdownPlugin).markdown.serialize();
    expect(out.trim()).toBe(
      "![one](https://example.com/1.png)![two](https://example.com/2.png)",
    );
    expect(out).not.toContain("\u200B");
    // 无额外顶层空段：只有一段（两张图共处）
    expect(editor.children).toHaveLength(1);
  });

  it("非 IMG 元素带 class=\"slate-img\" 不被认领为图片：可见文本保留、无 typed img", () => {
    const fragment = deserializePreparedHtml(
      `<div class="slate-img">keep me</div>`,
    );
    expect(collectByType(fragment, "img")).toHaveLength(0);
    expect(allLeafText(fragment)).toContain("keep me");
  });
});

// ---------------------------------------------------------------------------
// void children 渲染合同（Slate text leaf 必须进入 DOM）
//
// slate 的 Editor.point/range 把选区 points 解析到 void 内层 text leaf
// （Node.first/last）；slate-react 的 toDOMPoint 对该 leaf 调 toDOMNode，
// 未渲染即 throw（selection-sync 吞错后 removeAllRanges → 选区静默清空、
// reveal 不滚动）。渲染 {children} 后 slate-react 自动把 void leaf 渲染为
// ZeroWidthString（data-slate-zero-width，String 组件 :4370-4376 的
// isVoid(parent) 分支），toDOMPoint 即可解析。
// ---------------------------------------------------------------------------

/** 在编辑器树中定位第一个 img 节点的 path。 */
function findImgPath(editor: {
  children: Array<{ children?: Array<{ type?: string }> }>;
}): number[] | null {
  for (let bi = 0; bi < editor.children.length; bi += 1) {
    const kids = editor.children[bi]?.children ?? [];
    for (let ci = 0; ci < kids.length; ci += 1) {
      if (kids[ci]?.type === "img") return [bi, ci];
    }
  }
  return null;
}

describe("InputImageElement void DOM 合同（children 渲染与选区）", () => {
  it("图片 element 渲染 Slate text leaf（ZeroWidth），且只渲染一次", () => {
    const { container } = renderInputImage("https://example.com/a.png", "alt");
    const wrapper = container.querySelector("[data-image-input]");
    expect(wrapper).not.toBeNull();
    const leaves = wrapper?.querySelectorAll("[data-slate-leaf]");
    expect(leaves?.length).toBe(1);
    const zeroWidth = wrapper?.querySelectorAll("[data-slate-zero-width]");
    expect(zeroWidth?.length).toBe(1);
  });

  it("UI chrome 仍为 contentEditable=false，leaf 位于其外（标准 void 结构）", () => {
    const { container } = renderInputImage("https://example.com/a.png", "alt");
    const wrapper = container.querySelector("[data-image-input]") as HTMLElement;
    const chrome = wrapper.querySelector('[contenteditable="false"]');
    expect(chrome).not.toBeNull();
    // leaf 不在 contentEditable=false 区域内（children 是 chrome 的兄弟）
    const leaf = wrapper.querySelector("[data-slate-leaf]");
    expect(leaf).not.toBeNull();
    expect(chrome?.contains(leaf as Node)).toBe(false);
    // alt/url/title 不得进入 leaf 文本
    expect(leaf?.textContent).not.toContain("alt");
    expect(leaf?.textContent).not.toContain("example.com");
  });

  it("tf.select(图片 path) 后 DOM selection 落在图片 leaf 内，不再静默清空", async () => {
    const { editor, container } = renderInputImage(
      "https://example.com/a.png",
      "alt",
    );
    const path = findImgPath(editor);
    expect(path).not.toBeNull();
    await act(async () => {
      editor.tf.select(path as number[]);
      editor.tf.focus();
    });
    // slate-react 的 focus 在 editor 有 pending operations 时延迟 10ms
    // （slate-react focus:1650-1656），延迟到期后由 focus 自身把 DOM
    // selection 设置到 editor.selection（:1660-1667）；等待窗口必须覆盖。
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    const selection = window.getSelection();
    const wrapper = container.querySelector("[data-image-input]");
    expect(wrapper).not.toBeNull();
    // 未渲染 leaf 时 toDOMPoint throw → sync 吞错 → removeAllRanges（清空）
    expect(selection?.rangeCount ?? 0).toBeGreaterThanOrEqual(1);
    expect(
      wrapper?.contains(selection?.anchorNode ?? null),
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Clipboard rejected Promise 不产生 unhandled rejection
// ---------------------------------------------------------------------------

describe("复制链接的 Clipboard rejection", () => {
  it("writeText 拒绝时不产生 unhandled rejection；占位与修改入口仍在；不重试", async () => {
    // 注：不使用 vi.fn——vitest 的 mock 机制会 attach then（异步结果跟踪），
    // 把 rejected promise 标记为 handled，吞掉 unhandledRejection 信号；
    // 普通闭包 + 手工计数才能让 rejection 真正 unhandled。
    let writeTextCalls = 0;
    const writeText = () => {
      writeTextCalls += 1;
      return Promise.reject(new Error("clipboard denied"));
    };
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    try {
      const { container } = renderInputImage("https://example.com/a.png", "alt");
      const img = container.querySelector("img");
      await act(async () => {
        fireEvent(img as Element, new Event("error"));
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
      });
      // flush microtask/macrotask，让 rejection 事件到达监听器
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      // 只调用一次（不重试、无 execCommand fallback、无 toast）
      expect(writeTextCalls).toBe(1);
      // 失败占位与手动恢复入口仍在
      expect(
        container.querySelector("[data-image-state='load_failed']"),
      ).not.toBeNull();
      expect(screen.getByRole("button", { name: "修改链接" })).toBeTruthy();
      // 核心断言：同步 try/catch 捕不到异步 rejection；必须显式 .catch
      expect(unhandled).toHaveLength(0);
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });
});

// ---------------------------------------------------------------------------
// reference-style unsafe destination（渲染层）
// ---------------------------------------------------------------------------

describe("reference-style unsafe destination（渲染层）", () => {
  it("unsafe 引用图片：typed img 保留原 URL，永不进入 img.src", async () => {
    const md = "![a][x]\n\n[x]: javascript:alert(1)";
    const deserializer = createPlateEditor({
      plugins: [
        MarkdownPlugin.configure({ options: INPUT_MARKDOWN_PLUGIN_OPTIONS }),
      ],
    });
    const blocks = deserializer
      .getApi(MarkdownPlugin)
      .markdown.deserialize(md);
    // 在 blocks 中定位 img 节点（引用解析后 url 原样保留）
    const collect: Array<{ url?: string }> = [];
    const walk = (ns: unknown) => {
      if (!Array.isArray(ns)) return;
      for (const n of ns as Array<Record<string, unknown>>) {
        if (n?.type === "img") collect.push(n as { url?: string });
        if (Array.isArray(n?.children)) walk(n.children);
      }
    };
    walk(blocks);
    expect(collect).toHaveLength(1);
    expect(collect[0].url).toBe("javascript:alert(1)");

    // 渲染层：unsafe URL 永不进入 img.src（与 §10.2 reject 行一致）
    const editor = createPlateEditor({
      plugins: [
        MarkdownPlugin.configure({ options: INPUT_MARKDOWN_PLUGIN_OPTIONS }),
        InputMarkdownImagePlugin,
      ],
    });
    editor.tf.setValue(blocks as never[]);
    const { container } = render(
      <Plate editor={editor}>
        <PlateContent />
      </Plate>,
    );
    expect(container.querySelector("img[src]")).toBeNull();
    expect(container.textContent).toContain("链接不安全");
    // NARROW-REPAIR 契约：普通表面不显示 raw URL（节点级上方已断言保真）
    expect(container.textContent).not.toContain("javascript:alert(1)");
  });
});
