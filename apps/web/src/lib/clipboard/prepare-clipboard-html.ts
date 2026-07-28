/**
 * Clipboard HTML 清洗与 Notion callout 适配（L1 client presentation sanitization）。
 *
 * 粘贴入口安全合同：任何 text/html 在进入 Plate/DOM 之前先经过
 * `prepareClipboardHtml`：
 *
 * 1. sanitize（`sanitizeClipboardHtml`）：
 *    - 移除 `script` / `iframe` / `object` / `embed` 元素及其内容；
 *    - 移除所有 `on*` event handler 属性（onclick/onerror/...）；
 *    - 移除 URL 属性中 `javascript:` / `data:` / `vbscript:` scheme 的取值
 *      （href / src / xlink:href / formaction / action / srcset）。
 *    解析走 `<template>`，script 不会执行；不引入重型依赖（无 DOMPurify）。
 *
 * 2. Notion callout 适配（`adaptNotionCallouts`）：
 *    - `<aside>` 与 class 含 `callout` 的块级容器（Notion 复制/导出的
 *      callout 表示）映射为 `<blockquote>`，保证 callout 内容以引用语义
 *      进入 Plate —— 可见、不静默丢失、不走 raw HTML 丢弃路径。
 *    - Source Callout 完整支持是 L3 范围，本模块只做语义降级映射。
 *
 * 两个步骤都是纯字符串 → 字符串变换，可用 jsdom 单测。
 */

const BLOCKED_ELEMENTS = ["script", "iframe", "object", "embed"] as const;

/** 携带 URL 的属性；值匹配危险 scheme 时整体移除该属性。 */
const URL_ATTRIBUTES = [
  "href",
  "src",
  "xlink:href",
  "formaction",
  "action",
  "srcset",
] as const;

const DANGEROUS_URL_SCHEME = /^\s*(?:javascript|data|vbscript)\s*:/i;

function parseIntoTemplate(html: string): HTMLTemplateElement {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template;
}

function sanitizeNode(root: ParentNode): void {
  // 先移除危险元素（querySelectorAll 快照，安全删除）
  for (const tag of BLOCKED_ELEMENTS) {
    for (const el of Array.from(root.querySelectorAll(tag))) {
      el.remove();
    }
  }
  // 遍历所有剩余元素，清理属性
  const all: Element[] = [];
  if (root instanceof Element) {
    all.push(root);
  }
  all.push(...Array.from(root.querySelectorAll("*")));
  for (const el of all) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
        continue;
      }
      if (URL_ATTRIBUTES.includes(name as (typeof URL_ATTRIBUTES)[number])) {
        if (DANGEROUS_URL_SCHEME.test(attr.value)) {
          el.removeAttribute(attr.name);
        }
      }
    }
  }
}

/**
 * 清洗 clipboard HTML 字符串。返回清洗后的 HTML 字符串。
 */
export function sanitizeClipboardHtml(html: string): string {
  if (!html || !html.trim()) {
    return "";
  }
  const template = parseIntoTemplate(html);
  sanitizeNode(template.content);
  return template.innerHTML;
}

/** Notion callout 的 class 信号（复制粘贴与 HTML 导出两种来源）。 */
const CALLOUT_CLASS_PATTERN = /(?:^|\s)(?:[\w-]*callout[\w-]*)(?:\s|$)/i;

function isCalloutContainer(el: Element): boolean {
  if (el.tagName === "ASIDE") {
    return true;
  }
  const tag = el.tagName;
  if (tag !== "DIV" && tag !== "FIGURE" && tag !== "SECTION") {
    return false;
  }
  const className = el.getAttribute("class") ?? "";
  return CALLOUT_CLASS_PATTERN.test(className);
}

/**
 * 把 Notion callout 表示（`<aside>` / class 含 callout 的块容器）映射为
 * `<blockquote>` 语义。子内容保留（含 Notion 的 icon 文本），保证可见。
 */
export function adaptNotionCallouts(html: string): string {
  if (!html || !html.trim()) {
    return "";
  }
  const template = parseIntoTemplate(html);
  // 反复扫描直到没有 callout 容器（处理嵌套 aside）
  for (;;) {
    const target = Array.from(
      template.content.querySelectorAll("aside, div, figure, section"),
    ).find(isCalloutContainer);
    if (!target) {
      break;
    }
    const blockquote = document.createElement("blockquote");
    // 保留子内容（搬移而非克隆，保持文本与内联结构）
    while (target.firstChild) {
      blockquote.appendChild(target.firstChild);
    }
    target.replaceWith(blockquote);
  }
  return template.innerHTML;
}

/**
 * 把 `<img>` 降级为可见链接 `<a href="src">alt 或 src</a>`。
 * 输入端不做完整图片编辑支持，但不得静默丢失——图片以可见、可点击的
 * 链接形态进入 Plate。危险 scheme 的 src 已在 sanitize 阶段摘除，
 * 剩下的 http(s)/相对地址原样保留。
 */
export function adaptImages(html: string): string {
  if (!html || !html.trim()) {
    return "";
  }
  const template = parseIntoTemplate(html);
  for (const img of Array.from(template.content.querySelectorAll("img"))) {
    const src = img.getAttribute("src");
    const alt = img.getAttribute("alt") ?? "";
    if (!src) {
      // 无 src 图片（或 src 已被 sanitize 摘除）：保留 alt 为纯文本，不消失
      img.replaceWith(document.createTextNode(alt));
      continue;
    }
    const anchor = document.createElement("a");
    anchor.setAttribute("href", src);
    anchor.textContent = alt || src;
    img.replaceWith(anchor);
  }
  return template.innerHTML;
}

/**
 * 粘贴 HTML 预处理总入口：先 sanitize，再做语义适配
 * （Notion callout → blockquote；img → 可见链接）。
 */
export function prepareClipboardHtml(html: string): string {
  return adaptImages(adaptNotionCallouts(sanitizeClipboardHtml(html)));
}
