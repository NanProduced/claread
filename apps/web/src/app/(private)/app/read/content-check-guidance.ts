/**
 * Content Check 风险项的客户端指引（纯函数，可单测）。
 *
 * 数据来源：PUT/GET confirmed-source 响应里的 `content_check` 数组
 * 后端 R8 structured review item 是 tier、位置、evidence 与 patch 的唯一权威；
 * 本文件只保留安全的用户文案映射与精确 anchor 工具，不再按 code 猜测字段。
 */

import type { ReaderContentCheckItemDto } from "@/types/api/reader-plate";

export interface ContentCheckGuidance {
  /** 风险位置标题（短，用户语言）。 */
  title: string;
  /** 一句可执行建议。不得使用后端 item.message。 */
  suggestion: string;
}

/**
 * 后端真实 code 闭合集（见 docs/architecture/file-upload-parse-chain-markdown.md §9）。
 * 过期别名（unclosed_fence / footnote_ref / image_content / math_content）不下发，不映射。
 */
const GUIDANCE_BY_CODE: Record<string, ContentCheckGuidance> = {
  source_type_review_default: {
    title: "提取的正文需要过目",
    suggestion: "提取的文字建议你看一眼再开始阅读",
  },
  ocr_low_confidence: {
    title: "部分文字识别可能不准",
    suggestion: "对照原图或原文件看一眼关键段落再开始阅读。",
  },
  image_ocr_uncertain: {
    title: "文中含有图片",
    suggestion: "图片里的信息不会自动进入正文，重要的话请补成文字。",
  },
  document_block_degraded: {
    title: "文中含有公式",
    suggestion: "公式可能显示不完整，请确认是否还要保留。",
  },
  footnote_reference: {
    title: "脚注引用",
    suggestion: "脚注无法进入正文结构，建议留作普通文字或改成括号说明。",
  },
  task_list_unsupported: {
    title: "任务列表",
    suggestion: "勾选状态只会当普通文字留下，需要的话请改成普通列表。",
  },
  has_unclosed_fence: {
    title: "代码块未闭合",
    suggestion: "代码块缺少结束围栏，建议补上 ``` 结束标记。",
  },
  table_structure_uncertain: {
    title: "表格结构不确定",
    suggestion: "表格的列对齐或表头可能识别不准，建议检查表格内容是否正确。",
  },
  missing_source_range: {
    title: "部分内容定位缺失",
    suggestion: "部分内容无法对应回原文位置，建议确认该段内容是否完整。",
  },
  layout_order_uncertain: {
    title: "段落顺序可能不准",
    suggestion: "版面阅读顺序不太确定，建议按原文核对段落先后。",
  },
  code_dominant: {
    title: "代码占比过高",
    suggestion: "这份内容以代码为主，批注价值有限，建议确认是否继续。",
  },
  too_long_requires_envelope: {
    title: "篇幅过长",
    suggestion: "全文太长，建议先拆成较短的一段再阅读。",
  },
  unclosed_html_aside: {
    title: "侧栏标记未闭合",
    suggestion: "一段侧栏结构不完整，建议检查附近内容是否被拆乱。",
  },
  raw_html_block: {
    title: "网页标记已清理",
    suggestion: "大段网页标记已去掉，正文已留下，过目即可。",
  },
  inline_html: {
    title: "行内网页标记已去掉",
    suggestion: "夹在句子里的网页标记已去掉，文字还在。",
  },
  unsafe_link_protocol: {
    title: "不安全链接已去掉",
    suggestion: "不安全的链接地址已去掉，链接文字还在。",
  },
  definition_list_degraded: {
    title: "定义列表已按普通文字处理",
    suggestion: "定义列表结构未保留，内容已按普通文字留下。",
  },
  mermaid_static_only: {
    title: "图示按代码留下",
    suggestion: "图示不会画出来，只保留源码文本。",
  },
  strikethrough_extension: {
    title: "删除线已按普通文字处理",
    suggestion: "删除线标记已按普通文字留下。",
  },
};

export const FALLBACK_GUIDANCE: ContentCheckGuidance = {
  title: "需要过目的内容",
  suggestion: "这部分内容的格式系统拿不准，建议过目",
};

export function guidanceForContentCheckCode(code: string): ContentCheckGuidance {
  return GUIDANCE_BY_CODE[code] ?? FALLBACK_GUIDANCE;
}

/**
 * rejected outcome 的用户文案。后端 `suitability.reasons` 是英文诊断句
 * （如 "English content is too short for learning (37 words)."），不上屏；
 * 按 flags（闭合 union，见 ReaderSourceLossFlagDto）映射。
 */
const REJECTED_REASON_BY_FLAG: Record<string, string> = {
  too_short_for_learning: "英文内容太短，补充成一段完整的英文文章再试。",
  non_english_or_mixed_language: "英文占比太低，透读目前为英文材料设计。",
  link_list_dominant: "内容以链接列表为主，缺少可以阅读的正文。",
};

const FALLBACK_REJECTED_REASON = "这份内容暂时无法生成阅读版本，可以调整后重新提交。";

/** 按 flags 映射 rejected 原因（只含命中项，可能为空数组）。 */
export function mapRejectedFlagCopy(flags: readonly string[]): string[] {
  return flags
    .map((flag) => REJECTED_REASON_BY_FLAG[flag])
    .filter((copy): copy is string => Boolean(copy));
}

export function rejectedReasonCopyForFlags(flags: readonly string[]): string[] {
  const mapped = mapRejectedFlagCopy(flags);
  return mapped.length > 0 ? mapped : [FALLBACK_REJECTED_REASON];
}

export type ContentCheckAnchorInspection = {
  status: "document" | "valid" | "changed" | "unavailable";
  excerpt: string | null;
};

async function sha256Hex(text: string): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  try {
    const digest = await globalThis.crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(text),
    );
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
  } catch {
    return null;
  }
}

/** Validate an R8 anchor against the current JavaScript UTF-16 string. */
export async function inspectContentCheckAnchor(
  item: ReaderContentCheckItemDto,
  markdown: string,
): Promise<ContentCheckAnchorInspection> {
  if (item.target_scope === "document") {
    return { status: "document", excerpt: null };
  }
  const anchor = item.source_anchor;
  if (!anchor || "block_id" in anchor) {
    return { status: "unavailable", excerpt: null };
  }
  const { start_utf16: start, end_utf16: end } = anchor;
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end <= start ||
    end > markdown.length
  ) {
    return { status: "changed", excerpt: null };
  }
  const excerpt = markdown.slice(start, end);
  const currentHash = await sha256Hex(excerpt);
  if (currentHash === null) {
    return { status: "unavailable", excerpt: null };
  }
  return currentHash === item.anchor_hash
    ? { status: "valid", excerpt }
    : { status: "changed", excerpt: null };
}

/** Apply only the backend-provided patch to a currently hash-valid range. */
export async function applyContentCheckProposedPatch(
  item: ReaderContentCheckItemDto,
  markdown: string,
): Promise<string | null> {
  const patch = item.evidence.proposed_patch;
  if (!patch?.trim()) return null;
  const inspection = await inspectContentCheckAnchor(item, markdown);
  const anchor = item.source_anchor;
  if (inspection.status !== "valid" || !anchor || "block_id" in anchor) {
    return null;
  }
  return `${markdown.slice(0, anchor.start_utf16)}${patch}${markdown.slice(
    anchor.end_utf16,
  )}`;
}
