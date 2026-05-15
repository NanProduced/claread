"use client";

import { useEffect, useMemo, useState } from "react";

import type { WebAnnotationCreateRequest, WebAnnotationVm } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

interface ReaderAnnotationsProps {
  recordId: string;
  initialItems: WebAnnotationVm[];
}

interface SentenceAnnotationActionProps {
  recordId: string;
  sentence: SentenceModel;
}

export const ANNOTATION_CREATED_EVENT = "claread:annotation-created";

function annotationLabel(item: WebAnnotationVm): string {
  return item.type === "note" ? "笔记" : "高亮";
}

export function ReaderAnnotationList({ recordId, initialItems }: ReaderAnnotationsProps) {
  const [items, setItems] = useState(initialItems);

  useEffect(() => {
    function handleCreated(event: Event) {
      const item = (event as CustomEvent<WebAnnotationVm>).detail;
      if (item.recordId === recordId || item.recordId === null) {
        setItems((current) => [item, ...current.filter((existing) => existing.id !== item.id)]);
      }
    }

    window.addEventListener(ANNOTATION_CREATED_EVENT, handleCreated);
    return () => window.removeEventListener(ANNOTATION_CREATED_EVENT, handleCreated);
  }, [recordId]);

  const recordItems = useMemo(
    () => items.filter((item) => item.recordId === recordId || item.recordId === null),
    [items, recordId],
  );

  return (
    <section className="rounded-note border border-hairline bg-surface-warm p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-ink">我的批注</h3>
        <span className="shrink-0 rounded-pill bg-surface px-2 py-0.5 text-[0.6875rem] text-muted">
          {recordItems.length}
        </span>
      </div>
      {recordItems.length === 0 ? (
        <p className="text-sm leading-6 text-muted">还没有保存批注。</p>
      ) : (
        <div className="space-y-3">
          {recordItems.map((item) => (
            <article key={item.id} className="border-l-2 border-structure-green/50 pl-3">
              <div className="mb-1 text-xs font-semibold text-muted">
                {annotationLabel(item)}
                {item.sentenceId ? <span className="ml-2 font-normal">{item.sentenceId}</span> : null}
              </div>
              <p className="line-clamp-3 text-sm leading-6 text-ink-soft">{item.selectedText}</p>
              {item.note ? (
                <p className="mt-1 whitespace-pre-line text-sm leading-6 text-ink">{item.note}</p>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export function SentenceAnnotationAction({
  recordId,
  sentence,
}: SentenceAnnotationActionProps) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  async function submitAnnotation(useNote: boolean) {
    setState({ kind: "saving" });

    const body: WebAnnotationCreateRequest = {
      recordId,
      paragraphId: sentence.paragraphId,
      sentenceId: sentence.sentenceId,
      selectedText: sentence.text,
      note: useNote ? note : undefined,
    };

    try {
      const response = await fetch("/api/web/annotations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as
        | { ok: true; item: WebAnnotationVm }
        | { ok: false; message?: string };

      if (!response.ok || !payload.ok) {
        setState({
          kind: "error",
          message: payload.ok === false && payload.message ? payload.message : "批注保存失败。",
        });
        return;
      }

      window.dispatchEvent(
        new CustomEvent<WebAnnotationVm>(ANNOTATION_CREATED_EVENT, { detail: payload.item }),
      );
      setNote("");
      setOpen(false);
      setState({ kind: "saved", message: "已保存。" });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "批注保存失败。",
      });
    }
  }

  return (
    <div className="pt-1">
      <button
        type="button"
        className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-xs font-semibold text-muted transition-colors hover:border-muted hover:text-ink md:opacity-0 md:group-hover/sentence:opacity-100 md:focus-visible:opacity-100"
        onClick={() => setOpen((value) => !value)}
      >
        批注
      </button>
      {open ? (
        <div className="mt-2 rounded-md border border-hairline bg-surface-warm p-3">
          <textarea
            className="min-h-20 w-full resize-y rounded-md border border-hairline bg-surface px-3 py-2 text-sm leading-6 text-ink outline-none focus:border-muted"
            placeholder="写一句笔记；留空可直接保存高亮。"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="rounded-pill bg-structure-green px-3 py-1.5 text-xs font-semibold text-surface disabled:opacity-60"
              disabled={state.kind === "saving"}
              onClick={() => submitAnnotation(false)}
            >
              高亮本句
            </button>
            <button
              type="button"
              className="rounded-pill border border-hairline bg-surface px-3 py-1.5 text-xs font-semibold text-ink disabled:opacity-60"
              disabled={state.kind === "saving" || note.trim().length === 0}
              onClick={() => submitAnnotation(true)}
            >
              保存笔记
            </button>
            {state.kind === "saving" ? <span className="text-xs text-muted">保存中...</span> : null}
          </div>
        </div>
      ) : null}
      {state.kind === "saved" ? (
        <p className="mt-1 text-xs text-structure-green">{state.message}</p>
      ) : null}
      {state.kind === "error" ? (
        <p className="mt-1 text-xs text-error-red">{state.message}</p>
      ) : null}
    </div>
  );
}
