import { MessageSquare, Send, X } from "lucide-react";
import type { SentenceModel } from "@/types/view/ReaderMockVm";

export interface AiWorkspacePanelProps {
  open: boolean;
  activeSentence: SentenceModel | null;
  hideLauncherOnMobile?: boolean;
  hideLauncherInCompactLayout?: boolean;
  onToggle: () => void;
}

export function AiWorkspacePanel({
  open,
  activeSentence,
  hideLauncherOnMobile = false,
  hideLauncherInCompactLayout = false,
  onToggle,
}: AiWorkspacePanelProps) {
  const launcherVisibilityClass = hideLauncherInCompactLayout
    ? "hidden 2xl:inline-flex"
    : hideLauncherOnMobile
      ? "hidden md:inline-flex"
      : "inline-flex";

  if (!open) {
    return (
      <button
        type="button"
        className={`focus-ring fixed bottom-[5.25rem] right-4 z-40 min-h-12 items-center gap-2 rounded-pill border border-hairline bg-surface/96 px-4 text-sm font-semibold text-ink shadow-surface-quiet backdrop-blur-sm transition-colors hover:border-muted md:bottom-6 md:right-6 ${launcherVisibilityClass}`}
        onClick={onToggle}
        aria-label="打开 AI 工作区"
      >
        <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue" />
        <span>Ask Claread</span>
      </button>
    );
  }

  return (
    <aside className="fixed inset-x-3 bottom-3 z-50 flex max-h-[82vh] flex-col overflow-hidden rounded-panel border border-hairline bg-surface shadow-[0_24px_80px_rgba(17,17,17,0.16)] 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[clamp(31rem,calc((100vw-124px-96ch)/2-0.5rem),37.5rem)] 2xl:min-w-0 2xl:max-h-none">
      <div className="flex items-center justify-between gap-3 border-b border-hairline px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-ink">Ask Claread</h2>
          <p className="mt-1 text-xs leading-5 text-muted">
            围绕当前句子、选区或全文继续提问。
          </p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-hairline bg-reader-paper text-muted transition-colors hover:border-muted hover:text-ink"
          onClick={onToggle}
          aria-label="收起 AI 工作区"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
        <div className="border-b border-hairline pb-4">
          <p className="text-xs font-semibold text-muted">当前上下文</p>
          <p className="mt-2 line-clamp-6 reader-serif text-[0.9375rem] leading-7 text-ink-soft">
            {activeSentence?.text ?? "点击正文句子后，这里会带入上下文。"}
          </p>
        </div>
        <div className="flex flex-1 items-center justify-center py-10">
          <div className="max-w-64 text-center">
            <MessageSquare aria-hidden="true" className="mx-auto h-6 w-6 text-lens-blue/75" />
            <p className="mt-3 text-sm font-semibold text-ink">追问稍后接入</p>
            <p className="mt-2 text-sm leading-6 text-muted">
              这里会承载完整对话流、引用回源和当前句上下文，不再挤在窄栏里。
            </p>
          </div>
        </div>
        <label className="block border-t border-hairline pt-4">
          <span className="text-xs font-semibold text-muted">输入框</span>
          <div className="mt-2 flex items-end gap-2 rounded-note border border-hairline bg-reader-paper p-2">
            <textarea
              className="min-h-24 flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-6 text-muted outline-none"
              placeholder="后续接入 Ask Claread。"
              disabled
            />
            <button
              type="button"
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink text-surface opacity-40"
              disabled
              aria-label="发送"
            >
              <Send aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
        </label>
      </div>
    </aside>
  );
}
