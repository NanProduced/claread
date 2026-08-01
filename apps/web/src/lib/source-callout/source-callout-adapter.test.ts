/**
 * Source Callout Adapter — 共享识别/归一化逻辑的单元测试。
 *
 * 覆盖：
 * - matchAsideBlock：完整 `<aside>` / 不完整 / 转义 / 其他标签
 * - classifyCalloutKind：class → kind 推断
 * - classifyCalloutKindFromGfmMarker：GFM marker → kind
 * - buildCanonicalAsideMarkdown：canonical 表达与 round-trip
 * - 安全回归：script / iframe / 事件属性 / 普通 div / 转 `\<aside>` / 不完整 aside
 *   都不会被误识别为 callout。
 */

import { describe, expect, it } from "vitest";

import {
  buildCanonicalAsideMarkdown,
  CALLOUT_CSS_CLASSES,
  CALLOUT_ICON_COLORS,
  CALLOUT_ICONS,
  CALLOUT_KINDS,
  classifyCalloutKind,
  classifyCalloutKindFromGfmMarker,
  DEFAULT_CALLOUT_KIND,
  extractGfmAlertMarker,
  GFM_ALERT_MARKER_RE,
  matchAsideBlock,
  stripGfmAlertMarker,
} from "./source-callout-adapter";

describe("matchAsideBlock", () => {
  it("matches a simple <aside>...</aside> block", () => {
    const result = matchAsideBlock("<aside>note body</aside>");
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("note");
    expect(result?.innerContent).toBe("note body");
  });

  it("matches <aside> with class attribute and infers kind", () => {
    const result = matchAsideBlock('<aside class="callout-warning">careful</aside>');
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("warning");
    expect(result?.innerContent).toBe("careful");
  });

  it("matches multi-line <aside> with inner markdown", () => {
    const md = "<aside>\n**bold** and *italic*\n- item\n</aside>";
    const result = matchAsideBlock(md);
    expect(result).not.toBeNull();
    expect(result?.innerContent).toBe("**bold** and *italic*\n- item");
  });

  it("matches <aside> with arbitrary attributes (only class used, rest dropped)", () => {
    const result = matchAsideBlock(
      '<aside class="callout-tip" data-id="x" role="note">tip</aside>',
    );
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("tip");
    expect(result?.innerContent).toBe("tip");
  });

  it("returns null for incomplete <aside> (no closing tag)", () => {
    expect(matchAsideBlock("<aside>no closing")).toBeNull();
  });

  it("returns null for <div> (not aside)", () => {
    expect(matchAsideBlock("<div>not a callout</div>")).toBeNull();
  });

  it("returns null for <script> (not aside)", () => {
    expect(matchAsideBlock("<script>alert(1)</script>")).toBeNull();
  });

  it("returns null for <iframe> (not aside)", () => {
    expect(matchAsideBlock('<iframe src="evil"></iframe>')).toBeNull();
  });

  it("returns null for non-aside raw HTML", () => {
    expect(matchAsideBlock("<p>just a paragraph</p>")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(matchAsideBlock("")).toBeNull();
  });

  it("returns null for plain text", () => {
    expect(matchAsideBlock("just text")).toBeNull();
  });
});

describe("classifyCalloutKind", () => {
  it("returns warning for callout-warning class", () => {
    expect(classifyCalloutKind("callout-warning")).toBe("warning");
  });

  it("returns tip for callout-tip class", () => {
    expect(classifyCalloutKind("callout-tip")).toBe("tip");
  });

  it("returns important for callout-important class", () => {
    expect(classifyCalloutKind("callout-important")).toBe("important");
  });

  it("returns warning for caution/danger class", () => {
    expect(classifyCalloutKind("danger")).toBe("warning");
    expect(classifyCalloutKind("caution")).toBe("warning");
  });

  it("returns abstract for summary/tldr class", () => {
    expect(classifyCalloutKind("summary")).toBe("abstract");
    expect(classifyCalloutKind("tl;dr")).toBe("abstract");
  });

  it("returns note for unknown class (default)", () => {
    expect(classifyCalloutKind("unknown-class")).toBe(DEFAULT_CALLOUT_KIND);
    expect(classifyCalloutKind("")).toBe(DEFAULT_CALLOUT_KIND);
  });

  it("all CALLOUT_KINDS have icon, css class, and icon color entries", () => {
    for (const kind of CALLOUT_KINDS) {
      expect(CALLOUT_ICONS[kind]).toBeDefined();
      expect(CALLOUT_CSS_CLASSES[kind]).toBeDefined();
      expect(CALLOUT_ICON_COLORS[kind]).toBeDefined();
    }
  });
});

describe("classifyCalloutKindFromGfmMarker", () => {
  it("maps NOTE → note", () => {
    expect(classifyCalloutKindFromGfmMarker("NOTE")).toBe("note");
  });

  it("maps TIP → tip", () => {
    expect(classifyCalloutKindFromGfmMarker("TIP")).toBe("tip");
  });

  it("maps WARNING → warning", () => {
    expect(classifyCalloutKindFromGfmMarker("WARNING")).toBe("warning");
  });

  it("maps CAUTION → warning (alias)", () => {
    expect(classifyCalloutKindFromGfmMarker("CAUTION")).toBe("warning");
  });

  it("maps IMPORTANT → important", () => {
    expect(classifyCalloutKindFromGfmMarker("IMPORTANT")).toBe("important");
  });

  it("maps ABSTRACT → abstract", () => {
    expect(classifyCalloutKindFromGfmMarker("ABSTRACT")).toBe("abstract");
  });

  it("maps INFO → info", () => {
    expect(classifyCalloutKindFromGfmMarker("INFO")).toBe("info");
  });

  it("returns note for unknown marker", () => {
    expect(classifyCalloutKindFromGfmMarker("UNKNOWN")).toBe(DEFAULT_CALLOUT_KIND);
  });
});

describe("extractGfmAlertMarker / GFM_ALERT_MARKER_RE", () => {
  it("extracts NOTE from [!NOTE]", () => {
    expect(extractGfmAlertMarker("[!NOTE]")).toBe("NOTE");
  });

  it("extracts WARNING from [!WARNING]", () => {
    expect(extractGfmAlertMarker("[!WARNING]")).toBe("WARNING");
  });

  it("extracts TIP with surrounding whitespace", () => {
    expect(extractGfmAlertMarker("  [!TIP]  ")).toBe("TIP");
  });

  it("extracts NOTE from [!NOTE] followed by newline and content", () => {
    // remark-parse may merge marker line and following content into one
    // text node: "[!NOTE]\nThis is a note callout"
    expect(extractGfmAlertMarker("[!NOTE]\nThis is a note callout")).toBe("NOTE");
  });

  it("extracts WARNING from [!WARNING] followed by newline and content", () => {
    expect(extractGfmAlertMarker("[!WARNING]\nBe careful")).toBe("WARNING");
  });

  it("extracts TIP from [!TIP] followed by space and content", () => {
    expect(extractGfmAlertMarker("[!TIP] content here")).toBe("TIP");
  });

  it("returns null for [!NOTE] immediately followed by non-whitespace", () => {
    // [!NOTE]text (no separator) is NOT a valid GFM alert marker
    expect(extractGfmAlertMarker("[!NOTE]text")).toBeNull();
  });

  it("returns null for non-alert bracket text", () => {
    expect(extractGfmAlertMarker("[!NOTA]")).toBeNull();
    expect(extractGfmAlertMarker("regular text")).toBeNull();
    expect(extractGfmAlertMarker("")).toBeNull();
  });

  it("GFM_ALERT_MARKER_RE matches valid markers (strict full-match)", () => {
    expect(GFM_ALERT_MARKER_RE.test("[!NOTE]")).toBe(true);
    expect(GFM_ALERT_MARKER_RE.test("[!WARNING]")).toBe(true);
    expect(GFM_ALERT_MARKER_RE.test(" [!TIP] ")).toBe(true);
  });

  it("GFM_ALERT_MARKER_RE rejects markers with trailing content (strict)", () => {
    // GFM_ALERT_MARKER_RE is strict: requires whole string to be marker.
    // extractGfmAlertMarker is lenient: allows trailing content.
    expect(GFM_ALERT_MARKER_RE.test("[!NOTE]\ncontent")).toBe(false);
  });

  it("GFM_ALERT_MARKER_RE rejects invalid markers", () => {
    expect(GFM_ALERT_MARKER_RE.test("[!NOTA]")).toBe(false);
    expect(GFM_ALERT_MARKER_RE.test("text")).toBe(false);
  });
});

describe("stripGfmAlertMarker", () => {
  it("removes [!NOTE] marker from start of text", () => {
    expect(stripGfmAlertMarker("[!NOTE]")).toBe("");
  });

  it("removes [!WARNING] marker and trailing whitespace", () => {
    expect(stripGfmAlertMarker("[!WARNING]  ")).toBe("");
  });

  it("removes [!NOTE] marker and preserves remaining content", () => {
    expect(stripGfmAlertMarker("[!NOTE]\nThis is a note callout")).toBe("This is a note callout");
  });

  it("removes [!TIP] marker with leading whitespace", () => {
    expect(stripGfmAlertMarker("  [!TIP]  content")).toBe("content");
  });

  it("returns original text if no marker present", () => {
    expect(stripGfmAlertMarker("regular text")).toBe("regular text");
    expect(stripGfmAlertMarker("[!NOTA]")).toBe("[!NOTA]");
  });

  it("returns empty string for empty input", () => {
    expect(stripGfmAlertMarker("")).toBe("");
  });
});

describe("buildCanonicalAsideMarkdown", () => {
  it("wraps inner markdown in <aside> tags with newlines", () => {
    const result = buildCanonicalAsideMarkdown("note body");
    expect(result).toBe("<aside>\nnote body\n</aside>");
  });

  it("trims surrounding whitespace from inner content", () => {
    const result = buildCanonicalAsideMarkdown("  note body  ");
    expect(result).toBe("<aside>\nnote body\n</aside>");
  });

  it("handles multi-line inner markdown", () => {
    const inner = "**bold**\n- item\n- item2";
    const result = buildCanonicalAsideMarkdown(inner);
    expect(result).toBe("<aside>\n**bold**\n- item\n- item2\n</aside>");
  });

  it("produces empty aside for empty inner content", () => {
    const result = buildCanonicalAsideMarkdown("");
    expect(result).toBe("<aside>\n\n</aside>");
  });

  it("produces empty aside for whitespace-only inner content", () => {
    const result = buildCanonicalAsideMarkdown("   \n  ");
    expect(result).toBe("<aside>\n\n</aside>");
  });

  it("does not encode kind as attribute (canonical is kind-agnostic)", () => {
    const result = buildCanonicalAsideMarkdown("body", "warning");
    expect(result).toBe("<aside>\nbody\n</aside>");
    expect(result).not.toContain("class");
    expect(result).not.toContain("warning");
  });

  it("round-trip: canonical output is re-matchable by matchAsideBlock", () => {
    const canonical = buildCanonicalAsideMarkdown("**bold** note");
    const rematched = matchAsideBlock(canonical);
    expect(rematched).not.toBeNull();
    expect(rematched?.innerContent).toBe("**bold** note");
    expect(rematched?.kind).toBe("note");
  });
});

describe("safety regression — dangerous content must not be matched as callout", () => {
  it("script tag is not matched", () => {
    expect(matchAsideBlock("<script>alert(1)</script>")).toBeNull();
  });

  it("iframe tag is not matched", () => {
    expect(matchAsideBlock('<iframe src="javascript:alert(1)"></iframe>')).toBeNull();
  });

  it("div with onclick is not matched", () => {
    expect(
      matchAsideBlock('<div onclick="steal()">not a callout</div>'),
    ).toBeNull();
  });

  it("aside with event handler attribute still matches (attrs dropped, content kept)", () => {
    // <aside> 本身被匹配；event handler 属性在 sanitizeClipboardHtml 阶段
    // 已被移除，matchAsideBlock 只提取 class，其余属性丢弃。
    const result = matchAsideBlock(
      '<aside onclick="bad()" class="callout-note">safe content</aside>',
    );
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("note");
    expect(result?.innerContent).toBe("safe content");
  });

  it("incomplete aside (no closing tag) is not matched", () => {
    expect(matchAsideBlock("<aside>no closing tag")).toBeNull();
  });

  it("incomplete aside (no opening tag) is not matched", () => {
    expect(matchAsideBlock("no opening</aside>")).toBeNull();
  });

  it("plain text is not matched", () => {
    expect(matchAsideBlock("just plain text")).toBeNull();
  });

  it("empty string is not matched", () => {
    expect(matchAsideBlock("")).toBeNull();
  });
});
