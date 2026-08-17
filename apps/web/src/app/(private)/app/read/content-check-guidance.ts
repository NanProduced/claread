/**
 * Content Check 风险项的客户端指引（纯函数，可单测）。
 *
 * 数据来源：PUT/GET confirmed-source 响应里的 `content_check` 数组
 * （`ReaderAdaptationRecordDto = {code, message, classification}`，L1 后端
 * 已落地的 AdaptationRecord 形状）。合同只冻结 code/message/classification
 * 三个字段（合同 “PUT whole-document update”：内部 schema 由 L1 gate 产出），因此"原文上下文"
 * 与"处理建议"由客户端按 code 从草稿文本派生——这是 mock 合同的一部分，
 * 联调时若后端直接下发 excerpt/suggestion 字段，可在此层优先采用。
 */

export type ContentCheckTier = "routine" | "attention";

export interface ContentCheckGuidance {
  /** 风险位置标题（短，用户语言）。 */
  title: string;
  /** 一句可执行建议。不得使用后端 item.message。 */
  suggestion: string;
  /** 是否存在可机械应用的修复（"采用建议"按钮）。 */
  hasAutoFix: boolean;
  /** routine = 常规过目；attention = 高影响风险。 */
  tier: ContentCheckTier;
}

/**
 * 后端真实 code 闭合集（见 docs/architecture/file-upload-parse-chain-markdown.md §9）。
 * 过期别名（unclosed_fence / footnote_ref / image_content / math_content）不下发，不映射。
 */
const GUIDANCE_BY_CODE: Record<string, ContentCheckGuidance> = {
  source_type_review_default: {
    title: "提取的正文需要过目",
    suggestion: "提取的文字建议你看一眼再开始阅读",
    hasAutoFix: false,
    tier: "routine",
  },
  ocr_low_confidence: {
    title: "部分文字识别可能不准",
    suggestion: "对照原图或原文件看一眼关键段落再开始阅读。",
    hasAutoFix: false,
    tier: "routine",
  },
  image_ocr_uncertain: {
    title: "文中含有图片",
    suggestion: "图片里的信息不会自动进入正文，重要的话请补成文字。",
    hasAutoFix: false,
    tier: "routine",
  },
  document_block_degraded: {
    title: "文中含有公式",
    suggestion: "公式可能显示不完整，请确认是否还要保留。",
    hasAutoFix: false,
    tier: "routine",
  },
  footnote_reference: {
    title: "脚注引用",
    suggestion: "脚注无法进入正文结构，建议留作普通文字或改成括号说明。",
    hasAutoFix: false,
    tier: "routine",
  },
  task_list_unsupported: {
    title: "任务列表",
    suggestion: "勾选状态只会当普通文字留下，需要的话请改成普通列表。",
    hasAutoFix: false,
    tier: "routine",
  },
  has_unclosed_fence: {
    title: "代码块未闭合",
    suggestion: "代码块缺少结束围栏，建议补上 ``` 结束标记。",
    hasAutoFix: true,
    tier: "attention",
  },
  table_structure_uncertain: {
    title: "表格结构不确定",
    suggestion: "表格的列对齐或表头可能识别不准，建议检查表格内容是否正确。",
    hasAutoFix: false,
    tier: "attention",
  },
  missing_source_range: {
    title: "部分内容定位缺失",
    suggestion: "部分内容无法对应回原文位置，建议确认该段内容是否完整。",
    hasAutoFix: false,
    tier: "attention",
  },
  layout_order_uncertain: {
    title: "段落顺序可能不准",
    suggestion: "版面阅读顺序不太确定，建议按原文核对段落先后。",
    hasAutoFix: false,
    tier: "attention",
  },
  code_dominant: {
    title: "代码占比过高",
    suggestion: "这份内容以代码为主，批注价值有限，建议确认是否继续。",
    hasAutoFix: false,
    tier: "attention",
  },
  too_long_requires_envelope: {
    title: "篇幅过长",
    suggestion: "全文太长，建议先拆成较短的一段再阅读。",
    hasAutoFix: false,
    tier: "attention",
  },
  unclosed_html_aside: {
    title: "侧栏标记未闭合",
    suggestion: "一段侧栏结构不完整，建议检查附近内容是否被拆乱。",
    hasAutoFix: false,
    tier: "attention",
  },
  raw_html_block: {
    title: "网页标记已清理",
    suggestion: "大段网页标记已去掉，正文已留下，过目即可。",
    hasAutoFix: false,
    tier: "routine",
  },
  inline_html: {
    title: "行内网页标记已去掉",
    suggestion: "夹在句子里的网页标记已去掉，文字还在。",
    hasAutoFix: false,
    tier: "routine",
  },
  unsafe_link_protocol: {
    title: "不安全链接已去掉",
    suggestion: "不安全的链接地址已去掉，链接文字还在。",
    hasAutoFix: false,
    tier: "routine",
  },
  definition_list_degraded: {
    title: "定义列表已按普通文字处理",
    suggestion: "定义列表结构未保留，内容已按普通文字留下。",
    hasAutoFix: false,
    tier: "routine",
  },
  mermaid_static_only: {
    title: "图示按代码留下",
    suggestion: "图示不会画出来，只保留源码文本。",
    hasAutoFix: false,
    tier: "routine",
  },
  strikethrough_extension: {
    title: "删除线已按普通文字处理",
    suggestion: "删除线标记已按普通文字留下。",
    hasAutoFix: false,
    tier: "routine",
  },
};

export const FALLBACK_GUIDANCE: ContentCheckGuidance = {
  title: "需要过目的内容",
  suggestion: "这部分内容的格式系统拿不准，建议过目",
  hasAutoFix: false,
  tier: "routine",
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

/**
 * 从草稿 Markdown 中定位风险原文上下文（节选，供风险卡片展示）。
 * 找不到对应位置时返回 null，UI 退化为只展示建议文案。
 */
export function locateContentCheckExcerpt(
  code: string,
  markdown: string,
  maxLength = 160,
): string | null {
  const lines = markdown.split("\n");

  const clip = (text: string): string => {
    const trimmed = text.trim();
    if (!trimmed) return "";
    return trimmed.length > maxLength
      ? `${trimmed.slice(0, maxLength - 1)}…`
      : trimmed;
  };

  if (code === "has_unclosed_fence") {
    // 找最后一个未配对的开围栏所在行。
    let openIndex = -1;
    let open = false;
    lines.forEach((line, index) => {
      if (/^\s*```/.test(line)) {
        open = !open;
        openIndex = index;
      }
    });
    if (open && openIndex >= 0) {
      const context = lines.slice(openIndex, openIndex + 4).join("\n");
      return clip(context) || null;
    }
    return null;
  }

  if (code === "footnote_reference") {
    const index = lines.findIndex((line) => /\[\^[^\]]+\]/.test(line));
    return index >= 0 ? clip(lines[index]) || null : null;
  }

  if (code === "table_structure_uncertain") {
    const index = lines.findIndex((line) => /^\s*\|.*\|\s*$/.test(line));
    if (index >= 0) {
      const context = lines.slice(index, index + 3).join("\n");
      return clip(context) || null;
    }
    return null;
  }

  return null;
}

/**
 * 机械修复：返回修复后的整篇 Markdown；无法机械修复时返回 null。
 * 当前仅支持 has_unclosed_fence（在文末补结束围栏）。
 */
export function applyContentCheckAutoFix(
  code: string,
  markdown: string,
): string | null {
  if (code !== "has_unclosed_fence") {
    return null;
  }
  let open = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*```/.test(line)) {
      open = !open;
    }
  }
  if (!open) {
    return null;
  }
  return `${markdown.replace(/\s+$/, "")}\n\`\`\`\n`;
}
