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

export interface ContentCheckGuidance {
  /** 风险位置标题（短）。 */
  title: string;
  /** 明确的处理建议文案。 */
  suggestion: string;
  /** 是否存在可机械应用的修复（"采用建议"按钮）。 */
  hasAutoFix: boolean;
}

const GUIDANCE_BY_CODE: Record<string, ContentCheckGuidance> = {
  unclosed_fence: {
    title: "代码块未闭合",
    suggestion: "代码块缺少结束围栏，建议补上 ``` 结束标记。",
    hasAutoFix: true,
  },
  footnote_ref: {
    title: "脚注引用",
    suggestion: "脚注无法进入正文结构，建议保留为普通文字或手动改为括号说明。",
    hasAutoFix: false,
  },
  table_structure_uncertain: {
    title: "表格结构不确定",
    suggestion: "表格的列对齐或表头可能识别不准，建议检查表格内容是否正确。",
    hasAutoFix: false,
  },
  missing_source_range: {
    title: "部分内容定位缺失",
    suggestion: "部分内容无法对应回原文位置，建议确认该段内容是否完整。",
    hasAutoFix: false,
  },
  code_dominant: {
    title: "代码占比过高",
    suggestion: "这份内容以代码为主，透读批注价值有限，建议确认是否继续。",
    hasAutoFix: false,
  },
  image_content: {
    title: "图片内容",
    suggestion: "图片无法直接识别文字，建议确认图片信息是否重要。",
    hasAutoFix: false,
  },
  math_content: {
    title: "公式内容",
    suggestion: "公式可能无法完整进入透读结构，建议确认是否需要保留。",
    hasAutoFix: false,
  },
  ocr_low_confidence: {
    title: "OCR 置信度偏低",
    suggestion: "部分文字识别可能不准，建议对照原文检查关键段落。",
    hasAutoFix: false,
  },
};

const FALLBACK_GUIDANCE: ContentCheckGuidance = {
  title: "需要你确认的位置",
  suggestion: "系统对这部分内容的处理不确定，请确认是否符合预期。",
  hasAutoFix: false,
};

export function guidanceForContentCheckCode(code: string): ContentCheckGuidance {
  return GUIDANCE_BY_CODE[code] ?? FALLBACK_GUIDANCE;
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

  if (code === "unclosed_fence") {
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

  if (code === "footnote_ref") {
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
 * 当前仅支持 unclosed_fence（在文末补结束围栏）。
 */
export function applyContentCheckAutoFix(
  code: string,
  markdown: string,
): string | null {
  if (code !== "unclosed_fence") {
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
