/**
 * Feedback display config for the settings "我的反馈" surface.
 *
 * DATA-SCHEMA-BASELINE D2: relocated from the deleted zero-consumer
 * `components/reader/FeedbackSheet.tsx`. Only the dictionary/app scopes
 * exist — the sentence scope has no producer and was removed.
 */

import type {
  FeedbackScopeDto,
  FeedbackTypeDto,
} from "@/types/api/feedback";

export interface FeedbackScopeDisplayConfig {
  title: string;
  placeholder: string;
  requiresText: boolean;
  positiveOptions?: { value: FeedbackTypeDto; label: string }[];
  negativeOptions?: { value: FeedbackTypeDto; label: string }[];
  neutralOptions?: { value: FeedbackTypeDto; label: string }[];
}

export const FEEDBACK_CONFIG_BY_SCOPE: Record<FeedbackScopeDto, FeedbackScopeDisplayConfig> = {
  dictionary: {
    title: "词典反馈",
    placeholder: "可以补充释义、词性或例句的问题",
    requiresText: false,
    negativeOptions: [
      { value: "wrong_definition", label: "释义错误" },
      { value: "missing_definition", label: "释义缺失" },
      { value: "wrong_pos", label: "词性错误" },
      { value: "wrong_phonetic", label: "音标错误" },
      { value: "bad_example", label: "例句不好" },
      { value: "other", label: "其他" },
    ],
  },
  app: {
    title: "应用反馈",
    placeholder: "写下建议，或描述你遇到的问题",
    requiresText: true,
    neutralOptions: [
      { value: "bug_report", label: "遇到问题" },
      { value: "feature_request", label: "功能建议" },
      { value: "quota_issue", label: "配额问题" },
      { value: "input_page_issue", label: "输入页问题" },
      { value: "ux_issue", label: "体验不顺" },
      { value: "other", label: "其他" },
    ],
  },
};
