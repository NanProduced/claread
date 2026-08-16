/**
 * Ask 快捷指令（选区在场时的快捷提问）。
 *
 * 单一事实源：原选区工具栏 AIMenu 的快捷指令迁入 Ask composer chips。
 * 它们只是 prompt 预设（后端不消费 entry_action，见 TMP 分析文档），
 * 点击即以 quick_action 语义直接发送。
 */
import type { ReactNode } from "react";
import { BookOpenText, Search, WandSparkles } from "lucide-react";

import type { ReaderAskEntryActionDto } from "@/types/api/reader-ask";

export interface ReaderAskQuickAction {
  label: string;
  description: string;
  content: string;
  entryAction: ReaderAskEntryActionDto;
  icon: ReactNode;
}

export const READER_ASK_QUICK_ACTIONS: readonly ReaderAskQuickAction[] = [
  {
    label: "解释这段",
    description: "结合上下文解释含义",
    content: "请结合上下文解释这段内容。",
    entryAction: "explain_this",
    icon: <BookOpenText className="size-4" />,
  },
  {
    label: "分析语法",
    description: "拆解语法和句子结构",
    content: "请分析这段内容的语法、句子结构和理解难点。",
    entryAction: "why_here",
    icon: <WandSparkles className="size-4" />,
  },
  {
    label: "提取重点词汇",
    description: "找出值得掌握的词和短语",
    content: "请提取这段内容里的重点词汇和短语，并结合语境解释。",
    entryAction: "lookup_in_context",
    icon: <Search className="size-4" />,
  },
];
