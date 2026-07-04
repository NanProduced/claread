"use client";

import { useEffect, useState } from "react";
import {
  clearPendingCandidate,
  readPendingCandidate,
  type PendingCandidate,
} from "../../read/pending-candidate";

interface CandidateConfirmCalloutProps {
  recordId: string;
}

type CalloutState =
  | { kind: "idle" }
  | { kind: "ready"; candidate: PendingCandidate }
  | { kind: "confirming" }
  | { kind: "confirmed"; readingRecordId: string }
  | { kind: "conflict"; candidate: PendingCandidate }
  | { kind: "error"; candidate: PendingCandidate; message: string };

interface ConfirmApiOk {
  ok: true;
  [key: string]: unknown;
}

interface ConfirmApiErr {
  ok: false;
  status: number;
  code?: string;
  message?: string;
}

type ConfirmApiResponse = ConfirmApiOk | ConfirmApiErr;

function isMatchingCandidate(
  candidate: PendingCandidate | null,
  recordId: string,
): boolean {
  if (!candidate) return false;
  return candidate.readingRecordId === recordId;
}

// Avoid `next/navigation` (router / usePathname) — those register the
// component with Next's router state and suspend the page during first
// render under the test environment. Use runtime-native `window.location`.
function refreshPage() {
  if (typeof window !== "undefined") {
    window.location.reload();
  }
}

function pushTo(path: string) {
  if (typeof window !== "undefined") {
    window.location.assign(path);
  }
}

const buttonBase =
  "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full border px-3 py-1 font-sans text-[0.78rem] font-medium transition-colors";

function PrimaryButton(props: { children: React.ReactNode; onClick: () => void; "data-testid"?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      disabled={props.disabled ?? false}
      data-testid={props["data-testid"]}
      className={`${buttonBase} border-transparent bg-ink text-white hover:bg-ink-soft disabled:opacity-50`}
    >
      {props.children}
    </button>
  );
}

function SecondaryButton(props: { children: React.ReactNode; onClick: () => void; "data-testid"?: string }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      data-testid={props["data-testid"]}
      className={`${buttonBase} border-hairline/70 bg-surface/60 text-ink hover:bg-surface`}
    >
      {props.children}
    </button>
  );
}

export function CandidateConfirmCallout({ recordId }: CandidateConfirmCalloutProps) {
  const [state, setState] = useState<CalloutState>({ kind: "idle" });

  useEffect(() => {
    const pending = readPendingCandidate();
    if (pending && isMatchingCandidate(pending, recordId)) {
      setState({ kind: "ready", candidate: pending });
    } else {
      setState({ kind: "idle" });
    }
  }, [recordId]);

  if (state.kind === "idle") return null;
  if (state.kind === "confirmed") return null;

  // Shared POST path used by both the initial confirm and the conflict
  // retry-confirm button. Reads the candidate from current state (ready or
  // conflict), so the retry from a `conflict` state works without flipping
  // back through `ready` first.
  async function postConfirm() {
    if (state.kind !== "ready" && state.kind !== "conflict") return;
    const candidate = state.candidate;
    setState({ kind: "confirming" });
    try {
      const response = await fetch(
        `/api/web/reader-plate/records/${encodeURIComponent(candidate.readingRecordId)}/candidate-documents/${encodeURIComponent(candidate.candidateDocumentId)}/confirm`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ language: "en" }),
        },
      );
      const payload = (await response.json()) as ConfirmApiResponse;
      if (!payload.ok) {
        if (payload.code === "candidate_conflict" || payload.status === 409) {
          setState({ kind: "conflict", candidate });
          return;
        }
        setState({
          kind: "error",
          candidate,
          message: payload.message || "确认失败，请稍后重试。",
        });
        return;
      }
      clearPendingCandidate();
      setState({ kind: "confirmed", readingRecordId: candidate.readingRecordId });
      refreshPage();
    } catch (error: unknown) {
      setState({
        kind: "error",
        candidate,
        message: error instanceof Error ? error.message : "确认失败，请稍后重试。",
      });
    }
  }

  async function handleConfirm() {
    if (state.kind !== "ready") return;
    await postConfirm();
  }

  // Plan A per F3 review: clicking "重试确认" on the conflict branch
  // retries the POST confirm directly. Copy and behavior stay aligned.
  async function handleRetryConfirm() {
    if (state.kind !== "conflict") return;
    await postConfirm();
  }

  function handleDismiss() {
    if (state.kind === "idle" || state.kind === "confirmed") return;
    setState({ kind: "idle" });
  }

  // Plan B for the error branch: "返回确认" returns to ready so the
  // user can re-edit or retry.
  function handleBackToReady() {
    if (state.kind === "idle" || state.kind === "confirmed") return;
    if (state.kind === "ready" || state.kind === "error") {
      setState({ kind: "ready", candidate: state.candidate });
    }
  }

  const previewText =
    state.kind === "ready" || state.kind === "conflict" || state.kind === "error"
      ? state.candidate.canonicalTextPreview
      : null;

  const filename =
    state.kind === "ready" || state.kind === "conflict" || state.kind === "error"
      ? state.candidate.filename
      : null;

  const headerCopy =
    state.kind === "conflict"
      ? "候选文档状态已变化，请刷新后重试。"
      : "已生成候选文档，需要你确认后开始阅读";

  return (
    <section
      role="status"
      aria-live="polite"
      data-testid="candidate-confirm-callout"
      className="paper-grain border-b border-hairline/70 bg-amber-50/70 px-3 py-3 sm:px-4 lg:px-5"
    >
      <div className="mx-auto flex max-w-[82ch] flex-col gap-2">
        <p
          data-testid="candidate-confirm-title"
          className="font-sans text-[0.92rem] font-semibold text-ink"
        >
          {headerCopy}
        </p>
        {filename ? (
          <p className="font-sans text-[0.78rem] text-muted">
            来源文件：<code className="font-mono">{filename}</code>
          </p>
        ) : null}
        {previewText ? (
          <p
            data-testid="candidate-confirm-preview"
            className="max-h-24 overflow-y-auto whitespace-pre-wrap rounded-[8px] border border-hairline/60 bg-reader-paper/60 p-2 font-reading text-[0.86rem] leading-relaxed text-ink/85"
          >
            {previewText.slice(0, 240)}
            {previewText.length > 240 ? "…" : null}
          </p>
        ) : null}

        {state.kind === "error" ? (
          <p
            data-testid="candidate-confirm-error"
            className="font-sans text-[0.78rem] font-medium text-red-700"
          >
            {state.message}
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2 pt-1">
          {state.kind === "conflict" ? (
            <>
              <SecondaryButton onClick={refreshPage}>刷新页面</SecondaryButton>
              <SecondaryButton
                onClick={handleRetryConfirm}
                data-testid="candidate-retry-confirm-button"
              >
                重试确认
              </SecondaryButton>
              <SecondaryButton onClick={() => pushTo("/app/read")}>重新提交</SecondaryButton>
            </>
          ) : state.kind === "error" ? (
            <>
              <SecondaryButton onClick={handleBackToReady}>返回确认</SecondaryButton>
              <SecondaryButton onClick={() => pushTo("/app/read")}>重新提交</SecondaryButton>
            </>
          ) : state.kind === "confirming" ? (
            <PrimaryButton onClick={() => undefined} disabled>
              确认中…
            </PrimaryButton>
          ) : (
            <>
              <PrimaryButton
                onClick={handleConfirm}
                data-testid="candidate-confirm-button"
              >
                确认并开始阅读
              </PrimaryButton>
              <SecondaryButton
                onClick={handleDismiss}
                data-testid="candidate-defer-button"
              >
                稍后处理
              </SecondaryButton>
              <SecondaryButton
                onClick={() => pushTo("/app/read")}
                data-testid="candidate-retry-button"
              >
                重新提交
              </SecondaryButton>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
