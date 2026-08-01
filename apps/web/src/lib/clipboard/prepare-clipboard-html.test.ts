/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import {
  adaptNotionCallouts,
  prepareClipboardHtml,
  sanitizeClipboardHtml,
} from "./prepare-clipboard-html";

describe("sanitizeClipboardHtml", () => {
  it("removes script elements and their content", () => {
    const dirty = `<p>hello</p><script>alert(1)</script><p>world</p>`;
    const clean = sanitizeClipboardHtml(dirty);
    expect(clean).not.toContain("script");
    expect(clean).not.toContain("alert");
    expect(clean).toContain("hello");
    expect(clean).toContain("world");
  });

  it("removes iframe/object/embed", () => {
    const dirty = `<p>a</p><iframe src="https://evil.example"></iframe><object data="x.swf"></object><embed src="y.swf"><p>b</p>`;
    const clean = sanitizeClipboardHtml(dirty);
    expect(clean).not.toMatch(/<iframe|<object|<embed/);
    expect(clean).toContain("a");
    expect(clean).toContain("b");
  });

  it("removes on* event handler attributes", () => {
    const dirty = `<p onclick="steal()" onmouseover="x()">text</p><img src="ok.png" onerror="boom()">`;
    const clean = sanitizeClipboardHtml(dirty);
    expect(clean).not.toContain("onclick");
    expect(clean).not.toContain("onmouseover");
    expect(clean).not.toContain("onerror");
    expect(clean).toContain("text");
    expect(clean).toContain("ok.png");
  });

  it("removes javascript:/data:/vbscript: URLs from url attributes", () => {
    const dirty = [
      `<a href="javascript:alert(1)">j</a>`,
      `<a href="JaVaScRiPt:alert(2)">J2</a>`,
      `<a href=" javascript:alert(3)">spaced</a>`,
      `<a href="data:text/html;base64,PGI+">d</a>`,
      `<a href="vbscript:msgbox(1)">v</a>`,
      `<img src="data:image/png;base64,AAAA">`,
      `<a href="https://example.com/ok">ok</a>`,
    ].join("");
    const clean = sanitizeClipboardHtml(dirty);
    expect(clean).not.toMatch(/javascript:|vbscript:|data:text|data:image/i);
    // 安全 https 链接保留
    expect(clean).toContain("https://example.com/ok");
    // 链接文本保留（只摘属性，不摘节点）
    expect(clean).toContain(">j<");
    expect(clean).toContain(">ok<");
  });

  it("keeps ordinary safe markup intact", () => {
    const dirty = `<h1>T</h1><p><strong>b</strong> <a href="https://a.b/c?d=e&f=g">l</a></p><ul><li>i</li></ul>`;
    const clean = sanitizeClipboardHtml(dirty);
    expect(clean).toContain("<h1>T</h1>");
    expect(clean).toContain("<strong>b</strong>");
    expect(clean).toContain('href="https://a.b/c?d=e&amp;f=g"');
    expect(clean).toContain("<li>i</li>");
  });

  it("returns empty string for empty input", () => {
    expect(sanitizeClipboardHtml("")).toBe("");
    expect(sanitizeClipboardHtml("   ")).toBe("");
  });
});

describe("adaptNotionCallouts", () => {
  it("keeps <aside> as-is (no GFM alert marker, no blockquote)", () => {
    const html = `<p>before</p><aside>💡<div>callout body <strong>bold</strong></div></aside><p>after</p>`;
    const out = adaptNotionCallouts(html);
    expect(out).toContain("<aside");
    expect(out).toContain("</aside>");
    // 不得出现 GFM alert marker 或 blockquote 转换
    expect(out).not.toContain("[!NOTE]");
    expect(out).not.toContain("<blockquote");
    expect(out).toContain("callout body");
    expect(out).toContain("<strong>bold</strong>");
    expect(out).toContain("💡");
    expect(out).toContain("before");
    expect(out).toContain("after");
  });

  it("renames Notion-exported callout div to <aside> (preserving class)", () => {
    const html = `<div class="notion-callout"><div>icon</div><div>content text</div></div>`;
    const out = adaptNotionCallouts(html);
    expect(out).toContain("<aside");
    expect(out).toContain("</aside>");
    // class 保留用于 SourceCalloutPlugin 推断 kind
    expect(out).toContain('class="notion-callout"');
    expect(out).toContain("content text");
    // 不得出现可见 GFM marker
    expect(out).not.toContain("[!NOTE]");
    expect(out).not.toContain("<blockquote");
  });

  it("preserves callout-warning / callout-tip class on renamed <aside>", () => {
    const warningHtml = `<div class="callout-warning"><p>careful</p></div>`;
    const warningOut = adaptNotionCallouts(warningHtml);
    expect(warningOut).toContain("<aside");
    expect(warningOut).toContain('class="callout-warning"');
    expect(warningOut).toContain("careful");
    expect(warningOut).not.toContain("[!WARNING]");
    expect(warningOut).not.toContain("<blockquote");

    const tipHtml = `<div class="callout-tip"><p>hint</p></div>`;
    const tipOut = adaptNotionCallouts(tipHtml);
    expect(tipOut).toContain("<aside");
    expect(tipOut).toContain('class="callout-tip"');
    expect(tipOut).not.toContain("[!TIP]");

    const importantHtml = `<div class="callout-important"><p>must</p></div>`;
    const importantOut = adaptNotionCallouts(importantHtml);
    expect(importantOut).toContain("<aside");
    expect(importantOut).toContain('class="callout-important"');
    expect(importantOut).not.toContain("[!IMPORTANT]");
  });

  it("handles nested callout divs (each renamed to <aside>)", () => {
    const html = `<div class="callout"><p>outer</p><div class="callout"><p>inner</p></div></div>`;
    const out = adaptNotionCallouts(html);
    expect(out).not.toContain("<div");
    expect(out.match(/<aside/gi)?.length).toBe(2);
    expect(out).toContain("outer");
    expect(out).toContain("inner");
    expect(out).not.toContain("[!NOTE]");
    expect(out).not.toContain("<blockquote");
  });

  it("does not touch ordinary divs without callout class", () => {
    const html = `<div class="paragraph">plain</div>`;
    const out = adaptNotionCallouts(html);
    expect(out).toContain(`<div class="paragraph">plain</div>`);
    expect(out).not.toContain("<aside");
    expect(out).not.toContain("<blockquote>");
  });
});

describe("adaptImages", () => {
  it("img 降级为可见链接，alt 与 src 不丢失", () => {
    const out = prepareClipboardHtml(
      `<p>fig:</p><img src="https://example.com/d.png" alt="diagram">`,
    );
    expect(out).not.toContain("<img");
    expect(out).toContain('href="https://example.com/d.png"');
    expect(out).toContain(">diagram</a>");
  });

  it("img 无 alt 时用 src 作为可见文本", () => {
    const out = prepareClipboardHtml(`<img src="https://example.com/x.png">`);
    expect(out).toContain(">https://example.com/x.png</a>");
  });

  it("危险 src 被 sanitize 摘除后，alt 仍以纯文本保留", () => {
    const out = prepareClipboardHtml(`<img src="data:image/png;base64,AAAA" alt="kept-alt">`);
    expect(out).not.toContain("<img");
    expect(out).not.toContain("data:image");
    expect(out).toContain("kept-alt");
  });
});

describe("prepareClipboardHtml", () => {
  it("sanitizes then preserves <aside> (script + onclick stripped, no [!NOTE])", () => {
    const dirty = `<aside onclick="x()"><script>bad()</script><p>note</p></aside>`;
    const out = prepareClipboardHtml(dirty);
    expect(out).toContain("<aside");
    expect(out).toContain("</aside>");
    expect(out).toContain("note");
    // 危险内容必须移除
    expect(out).not.toContain("script");
    expect(out).not.toContain("onclick");
    // 不得出现可见 GFM marker
    expect(out).not.toContain("[!NOTE]");
    expect(out).not.toContain("<blockquote");
  });
});
