import type { Meta } from "@ladle/react";
import { GitBranch, MessageSquare, PencilLine, Search } from "lucide-react";
import { AskComposer } from "./AskComposer";
import { AssistantMessage } from "./AssistantMessage";
import { ConversationShell } from "./ConversationShell";
import { PromptSuggestions } from "./PromptSuggestions";
import { ReasoningPanel } from "./ReasoningPanel";
import { TaskProcessCard } from "./TaskProcessCard";

export default {
  title: "Reader/AskChat",
} satisfies Meta;

const starterSuggestions = [
  {
    prompt: "概括这篇文章的核心观点。",
    entryAction: "ask_about_this" as const,
    icon: MessageSquare,
    iconClassName: "text-grammar-violet",
    badgeClassName: "bg-[rgba(116,102,148,0.12)]",
  },
  {
    prompt: "作者最想说明什么？",
    entryAction: "ask_about_this" as const,
    icon: Search,
    iconClassName: "text-context-blue",
    badgeClassName: "bg-[rgba(76,145,194,0.12)]",
  },
  {
    prompt: "这篇文章是怎么展开论证的？",
    entryAction: "ask_about_this" as const,
    icon: GitBranch,
    iconClassName: "text-structure-green",
    badgeClassName: "bg-[rgba(60,140,104,0.12)]",
  },
  {
    prompt: "基于这篇文章出一道小练习。",
    entryAction: "ask_about_this" as const,
    icon: PencilLine,
    iconClassName: "text-vocab-amber",
    badgeClassName: "bg-[rgba(228,176,0,0.14)]",
  },
];

export const EmptyState = () => (
  <div className="min-h-screen bg-[linear-gradient(180deg,rgba(248,246,240,1),rgba(255,255,255,1))] p-6">
    <div className="mx-auto max-w-[42rem] rounded-[32px] border border-hairline/80 bg-[radial-gradient(circle_at_top,rgba(31,94,255,0.06),transparent_30%),linear-gradient(180deg,rgba(250,249,245,0.98),rgba(255,255,255,0.98))] p-5 shadow-[0_30px_84px_rgba(17,17,17,0.14)]">
      <PromptSuggestions
        title="从这篇文章开始问"
        description="当前文章默认在场，可以直接问核心观点、结构关系或作者意图。"
        contextLabel="当前句子"
        contextPreview="For almost a decade, I told everyone I encountered that they should pursue their passion."
        suggestions={starterSuggestions}
        onPickPrompt={() => {}}
      />
    </div>
  </div>
);

export const ConversationState = () => {
  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,rgba(248,246,240,1),rgba(255,255,255,1))] p-6">
      <div className="mx-auto flex h-[860px] max-w-[42rem] flex-col overflow-hidden rounded-[32px] border border-hairline/80 bg-[radial-gradient(circle_at_top,rgba(31,94,255,0.06),transparent_30%),linear-gradient(180deg,rgba(250,249,245,0.98),rgba(255,255,255,0.98))] shadow-[0_30px_84px_rgba(17,17,17,0.14)]">
        <div className="border-b border-hairline/60 bg-[rgba(255,255,255,0.52)] px-4 py-3 backdrop-blur-md">
          <h2 className="text-[15px] font-semibold tracking-[-0.02em] text-ink">Ask Claread</h2>
        </div>

        <div className="min-h-0 flex-1 pb-2 pt-3">
          <ConversationShell
            hasMessages
            contentClassName="gap-7"
          >
            <div className="flex justify-end">
              <div className="max-w-[88%] rounded-[22px] bg-[linear-gradient(180deg,rgba(28,27,25,0.98),rgba(42,40,36,0.96))] px-4 py-3 text-[14.5px] leading-[1.75] text-[rgba(255,250,242,0.96)] shadow-[0_16px_32px_rgba(17,17,17,0.14)]">
                帮我把这篇文章列成一个大纲。
              </div>
            </div>

            <AssistantMessage
              reasoning={
                <ReasoningPanel
                  reasoningMd={`我先基于文章当前上下文抽取论证主线，再把细节压缩成一个可复用的大纲层级。\n\n接下来需要先确认：\n\n1. 文章是否采用“个人叙事 -> 概念界定 -> 层层反驳”的结构\n2. 是否要保留原文中的关键英文短句作为例证`}
                  reasoningStatus="completed"
                />
              }
              process={
                <TaskProcessCard
                  title="正在组织回答"
                  detail="已经读取当前文章、附件和本轮问题，准备输出结构化大纲。"
                />
              }
              answer={
                <div className="space-y-3 text-[14.5px] leading-[1.8] text-ink-soft">
                  <p>可以把这篇文章整理成五层结构，从个人经历切入，逐步推进到制度批判。</p>
                  <ol className="list-decimal space-y-2 pl-5">
                    <li>个人叙事引入：作者先交代自己多年宣扬“追随激情”。</li>
                    <li>概念界定：提出 the passion principle，说明其在美国职业文化中的流行。</li>
                    <li>承认吸引力：先给出激情原则看似合理的理由。</li>
                    <li>逐层反驳：分别论证它如何助长过度工作与社会不平等。</li>
                    <li>制度性结论：指出机构和雇主是这一叙事的受益者。</li>
                  </ol>
                </div>
              }
            />
          </ConversationShell>
        </div>

        <AskComposer
          onSubmit={() => {}}
          sending={false}
          placeholder="继续问这篇文章..."
          contextStrip={
            <>
              <span className="inline-flex max-w-full items-center gap-2 rounded-full border border-hairline/70 bg-[rgba(255,255,255,0.82)] px-2.5 py-1.5 text-xs font-medium text-ink-soft shadow-[0_8px_18px_rgba(17,17,17,0.03)]">
                “追随激情”职业建议的陷阱与社会不平等
              </span>
              <span className="inline-flex max-w-full items-center gap-2 rounded-full border border-hairline/70 bg-[rgba(255,255,255,0.82)] px-2.5 py-1.5 text-xs font-medium text-ink-soft shadow-[0_8px_18px_rgba(17,17,17,0.03)]">
                当前句子
              </span>
            </>
          }
          actionMenu={
            <div className="rounded-[16px] border border-hairline/70 bg-[rgba(255,255,255,0.92)] p-3 text-[12px] text-muted shadow-[0_8px_18px_rgba(17,17,17,0.03)]">
              添加其他文章
            </div>
          }
          modelOptions={[{ label: "GLM-5.1", value: "glm-5.1" }]}
          selectedModelKey="glm-5.1"
        />
      </div>
    </div>
  );
};
