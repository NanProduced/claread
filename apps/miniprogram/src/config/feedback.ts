export type FeedbackScope = 'dictionary' | 'app'
export type FeedbackSentiment = 'positive' | 'negative' | 'neutral'

export interface FeedbackTypeOption {
  value: string
  label: string
}

export interface FeedbackScopeConfig {
  title: string
  requiresText: boolean
  placeholder: string
  positiveOptions?: FeedbackTypeOption[]
  negativeOptions?: FeedbackTypeOption[]
  neutralOptions?: FeedbackTypeOption[]
}

export const FEEDBACK_CONFIG_BY_SCOPE: Record<FeedbackScope, FeedbackScopeConfig> = {
  dictionary: {
    title: '反馈词典',
    requiresText: false,
    placeholder: '补充说明（选填）',
    negativeOptions: [
      { value: 'wrong_definition', label: '释义错误' },
      { value: 'missing_definition', label: '释义缺失' },
      { value: 'wrong_pos', label: '词性标注有误' },
      { value: 'wrong_phonetic', label: '音标有误' },
      { value: 'bad_example', label: '例句不当' },
      { value: 'other', label: '其他问题' },
    ],
  },
  app: {
    title: '意见反馈',
    requiresText: true,
    placeholder: '请详细描述您遇到的问题或建议，我们会认真阅读每一条反馈...',
    neutralOptions: [
      { value: 'bug_report', label: '功能异常' },
      { value: 'feature_request', label: '功能建议' },
      { value: 'quota_issue', label: '积分/次数' },
      { value: 'ux_issue', label: '体验不佳' },
      { value: 'other', label: '其他' },
    ],
  },
}

export const FEEDBACK_STATUS_LABELS: Record<string, string> = {
  pending: '已收到',
  triaged: '已进入处理',
  adopted: '已采纳',
  resolved: '已处理',
  dismissed: '未采纳',
}
