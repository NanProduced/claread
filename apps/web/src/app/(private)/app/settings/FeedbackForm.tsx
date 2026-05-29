"use client";

import { useId, useRef, useState } from "react";
import { Check, Loader2, SendHorizontal } from "lucide-react";

import { cn } from "@/lib/cn";
import { MyFeedbackList } from "./MyFeedbackList";

const FEEDBACK_TYPE_OPTIONS = [
  { value: "bug_report", label: "遇到问题", description: "出错、异常或结果不对" },
  { value: "feature_request", label: "功能建议", description: "希望 Claread 增加什么" },
  { value: "quota_issue", label: "配额问题", description: "积分、次数或扣减疑问" },
  { value: "input_page_issue", label: "输入页问题", description: "粘贴、导入或提交体验" },
  { value: "ux_issue", label: "体验不顺", description: "流程、按钮或阅读感受" },
  { value: "other", label: "其他", description: "不属于以上分类" },
] as const;

const SENTIMENT_OPTIONS = [
  {
    value: "positive",
    label: "喜欢",
    src: "/images/feedback/face-happy.png",
    activeClassName: "border-structure-green/30 bg-structure-green/10 text-structure-green",
  },
  {
    value: "neutral",
    label: "建议",
    src: "/images/feedback/face-neutral.png",
    activeClassName: "border-lens-blue/30 bg-lens-blue/10 text-lens-blue",
  },
  {
    value: "negative",
    label: "遇阻",
    src: "/images/feedback/face-sad.png",
    activeClassName: "border-error-red/25 bg-error-red/10 text-error-red",
  },
] as const;

type FeedbackType = (typeof FEEDBACK_TYPE_OPTIONS)[number]["value"];
type Sentiment = (typeof SENTIMENT_OPTIONS)[number]["value"];

type SubmitState =
  | { status: "idle"; message: string }
  | { status: "submitting"; message: string }
  | { status: "success"; message: string }
  | { status: "error"; message: string };

type FeedbackSubmitResponse =
  | {
      ok: true;
      message: string;
    }
  | {
      ok: false;
      message: string;
    };

export function FeedbackForm() {
  const contentId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [feedbackType, setFeedbackType] = useState<FeedbackType>("feature_request");
  const [sentiment, setSentiment] = useState<Sentiment>("neutral");
  const [content, setContent] = useState("");
  const [listRefreshKey, setListRefreshKey] = useState(0);
  const [state, setState] = useState<SubmitState>({
    status: "idle",
    message: "",
  });

  const canSubmit = content.trim().length > 0 && state.status !== "submitting";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!canSubmit) {
      setState({ status: "error", message: "先写下你想反馈的内容。" });
      textareaRef.current?.focus();
      return;
    }

    setState({ status: "submitting", message: "正在提交..." });

    try {
      const response = await fetch("/api/web/feedback", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          feedbackScope: "app",
          targetId: "web-settings",
          sentiment,
          feedbackType,
          content,
          contextJson: {
            entry: "settings",
            userAgent: navigator.userAgent,
          },
          contextSummary: "设置页应用反馈",
          clientPlatform: "web",
          clientSurface: "settings",
          entryPoint: "settings_form",
          appVersion: "web",
        }),
      });

      const result = (await response.json().catch(() => ({
        ok: false,
        message: "反馈提交失败，请稍后重试。",
      }))) as FeedbackSubmitResponse;

      if (!response.ok || !result.ok) {
        setState({
          status: "error",
          message: result.message || "反馈提交失败，内容已保留，请稍后重试。",
        });
        textareaRef.current?.focus();
        return;
      }

      setContent("");
      setFeedbackType("feature_request");
      setSentiment("neutral");
      setListRefreshKey((value) => value + 1);
      setState({ status: "success", message: result.message || "反馈已提交。" });
    } catch {
      setState({
        status: "error",
        message: "网络异常，内容已保留，请稍后重试。",
      });
      textareaRef.current?.focus();
    }
  }

  return (
    <form className="space-y-10" onSubmit={handleSubmit}>
      <style>{`
        @keyframes settings-feedback-soft-pop {
          0% { opacity: 0; transform: translate3d(0, 10px, 0) scale(0.98); }
          100% { opacity: 1; transform: translate3d(0, 0, 0) scale(1); }
        }
        .settings-feedback-panel {
          animation: settings-feedback-soft-pop 420ms cubic-bezier(0.22, 1, 0.36, 1) both;
        }
        .settings-feedback-ambient {
          transform: var(--feedback-rest-transform);
          transition: transform 520ms cubic-bezier(0.22, 1, 0.36, 1), filter 320ms ease, opacity 320ms ease;
          will-change: transform;
        }
        .settings-feedback-panel:hover .settings-feedback-ambient,
        .settings-feedback-panel:focus-within .settings-feedback-ambient {
          transform: var(--feedback-hover-transform);
          filter: saturate(1.04);
        }
        .settings-feedback-ambient[data-orbit="comment"] {
          --feedback-rest-transform: translate3d(0, 0, 0) rotate(-3deg);
          --feedback-hover-transform: translate3d(-5px, -10px, 0) rotate(4deg) scale(1.03);
        }
        .settings-feedback-ambient[data-orbit="love"] {
          --feedback-rest-transform: translate3d(0, 0, 0) rotate(-8deg);
          --feedback-hover-transform: translate3d(8px, -6px, 0) rotate(-2deg) scale(1.08);
          transition-delay: 55ms;
        }
        .settings-feedback-ambient[data-orbit="search"] {
          --feedback-rest-transform: translate3d(0, 0, 0) rotate(8deg);
          --feedback-hover-transform: translate3d(8px, 4px, 0) rotate(-3deg) scale(1.05);
          transition-delay: 95ms;
        }
        @media (prefers-reduced-motion: reduce) {
          .settings-feedback-panel,
          .settings-feedback-ambient {
            animation: none !important;
            transition-duration: 0.01ms !important;
          }
        }
      `}</style>

      <section className="settings-feedback-panel relative isolate overflow-hidden rounded-[24px] border border-hairline/80 bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_86%,transparent),color-mix(in_srgb,var(--surface-warm)_76%,transparent))] p-5 shadow-[0_18px_50px_rgba(28,24,18,0.08),inset_0_1px_0_rgba(255,255,255,0.48)] sm:p-6">
        <div className="pointer-events-none absolute inset-x-5 top-20 h-px bg-hairline/55" />
        <div className="grid gap-6 lg:grid-cols-[1fr_13.5rem] lg:items-start">
          <div className="min-w-0">
            <h2 className="max-w-[14em] font-headline text-[2rem] font-semibold leading-[1.08] text-ink sm:text-[2.35rem]">
              把问题或想法留给 Claread.
            </h2>
            <p className="mt-3 max-w-[36rem] text-sm leading-6 text-muted">
              我们会把这条反馈连同页面上下文一起记录，方便后续定位和处理。
            </p>
          </div>

          <div className="relative hidden min-h-[11rem] sm:block">
            <img
              src="/images/feedback/comment.png"
              alt=""
              data-orbit="comment"
              className="settings-feedback-ambient absolute right-8 top-2 h-[7.2rem] w-[7.2rem] object-contain drop-shadow-[0_18px_26px_rgba(80,52,24,0.16)]"
            />
            <img
              src="/images/feedback/love.png"
              alt=""
              data-orbit="love"
              className="settings-feedback-ambient absolute bottom-2 left-2 h-[4.6rem] w-[4.6rem] object-contain drop-shadow-[0_12px_22px_rgba(170,42,58,0.15)]"
            />
            <img
              src="/images/feedback/search.png"
              alt=""
              data-orbit="search"
              className="settings-feedback-ambient absolute bottom-7 right-1 h-[4.4rem] w-[4.4rem] object-contain drop-shadow-[0_12px_22px_rgba(80,52,24,0.12)]"
            />
          </div>
        </div>

        <div className="mt-7 grid grid-cols-3 gap-3">
          {SENTIMENT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setSentiment(opt.value)}
              aria-pressed={sentiment === opt.value}
              className={cn(
                "group relative min-h-[8.5rem] overflow-hidden rounded-[18px] border bg-surface/58 px-3 py-4 text-left shadow-[inset_0_1px_0_rgba(255,255,255,0.38)] transition-all duration-300 ease-[cubic-bezier(0.25,1,0.5,1)] hover:-translate-y-0.5 hover:border-muted hover:bg-surface",
                sentiment === opt.value
                  ? opt.activeClassName
                  : "border-hairline/85 text-ink-soft",
              )}
            >
              <span className="absolute inset-x-3 top-3 h-px bg-hairline/55" />
              <span className="flex h-full flex-col items-center justify-center gap-3">
                <img
                  src={opt.src}
                  alt=""
                  className={cn(
                    "h-14 w-14 object-contain drop-shadow-[0_12px_18px_rgba(80,52,24,0.14)] transition-transform duration-300 ease-[cubic-bezier(0.25,1,0.5,1)] group-hover:-translate-y-1 group-hover:scale-110",
                    sentiment === opt.value ? "-translate-y-1 scale-110" : "grayscale-[20%] opacity-90",
                  )}
                />
                <span className="text-sm font-semibold">{opt.label}</span>
              </span>
              {sentiment === opt.value ? (
                <span className="absolute right-3 top-3 inline-flex size-5 items-center justify-center rounded-full bg-ink text-background">
                  <Check className="size-3" aria-hidden="true" />
                </span>
              ) : null}
            </button>
          ))}
        </div>

        <div className="mt-6 grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {FEEDBACK_TYPE_OPTIONS.map((option) => (
            <button
              className={cn(
                "group min-h-[4.4rem] rounded-[15px] border px-3.5 py-3 text-left shadow-[inset_0_1px_0_rgba(255,255,255,0.34)] transition-all duration-200",
                feedbackType === option.value
                  ? "border-ink/70 bg-ink text-background shadow-[0_10px_20px_rgba(30,25,18,0.14)]"
                  : "border-hairline/80 bg-secondary/56 text-ink hover:border-muted hover:bg-[var(--app-control-quiet)]",
              )}
              key={option.value}
              onClick={() => setFeedbackType(option.value)}
              type="button"
              aria-pressed={feedbackType === option.value}
            >
              <span className="flex items-start justify-between gap-3">
                <span className="min-w-0">
                  <span className="block text-sm font-semibold leading-5">{option.label}</span>
                  <span
                    className={cn(
                      "mt-1 block text-[12px] leading-4",
                      feedbackType === option.value ? "text-background/72" : "text-muted",
                    )}
                  >
                    {option.description}
                  </span>
                </span>
                <Check
                  className={cn(
                    "mt-0.5 size-4 shrink-0 transition-opacity",
                    feedbackType === option.value ? "opacity-100" : "opacity-0 group-hover:opacity-35",
                  )}
                  aria-hidden="true"
                />
              </span>
            </button>
          ))}
        </div>

        <div className="mt-6 rounded-[18px] border border-hairline/85 bg-reader-paper/72 p-3.5 shadow-[inset_0_2px_6px_rgba(30,25,18,0.025)]">
          <div className="mb-2 flex items-center justify-between gap-3">
            <label className="text-[12px] font-semibold text-muted" htmlFor={contentId}>
              反馈内容
            </label>
            <span className="text-[12px] text-subtle">{content.length} / 2000</span>
          </div>
          <textarea
            ref={textareaRef}
            className="focus-ring min-h-36 w-full resize-y bg-transparent text-sm leading-6 text-ink outline-none placeholder:text-muted/62"
            id={contentId}
            maxLength={2000}
            onChange={(event) => setContent(event.target.value)}
            placeholder="写下具体问题、建议，或你希望 Claread 改进的地方。"
            value={content}
          />
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-hairline/70 pt-4">
          <span
            className={cn(
              "min-h-5 text-xs",
              state.status === "error"
                ? "text-error-red"
                : state.status === "success"
                  ? "text-structure-green"
                  : "text-muted",
            )}
            role={state.message ? "status" : undefined}
          >
            {state.message || " "}
          </span>
          <button
            className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-[14px] bg-ink px-5 text-sm font-semibold text-background shadow-[0_10px_20px_rgba(30,25,18,0.14)] transition-all hover:bg-ink-soft hover:shadow-[0_12px_24px_rgba(30,25,18,0.18)] active:scale-[0.98] disabled:pointer-events-none disabled:bg-muted/55 disabled:shadow-none"
            disabled={!canSubmit}
            type="submit"
          >
            {state.status === "submitting" ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                提交中
              </>
            ) : (
              <>
                <SendHorizontal className="size-4" aria-hidden="true" />
                提交反馈
              </>
            )}
          </button>
        </div>
      </section>

      <section className="border-t border-hairline pt-8">
        <div className="mb-5 flex items-end justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-ink">我的反馈记录</h2>
            <p className="mt-1 text-xs leading-5 text-muted">最近提交的反馈会显示在这里。</p>
          </div>
        </div>
        <MyFeedbackList refreshKey={listRefreshKey} />
      </section>
    </form>
  );
}
