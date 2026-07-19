"use client";

import { useEffect, useId, useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Check, X } from "lucide-react";

type EditState = "idle" | "editing" | "saving" | "error" | "saved";

interface NicknameEditorProps {
  initialNickname: string;
  displayFallback?: string;
}

export function NicknameEditor({ initialNickname, displayFallback }: NicknameEditorProps) {
  const router = useRouter();
  const errorId = useId();
  const [editState, setEditState] = useState<EditState>("idle");
  const [draft, setDraft] = useState(initialNickname);
  const [errorMessage, setErrorMessage] = useState("");
  const [displayNickname, setDisplayNickname] = useState(initialNickname);

  const shownName = displayNickname || displayFallback || "Web User";

  function startEditing() {
    setDraft(displayNickname);
    setEditState("editing");
    setErrorMessage("");
  }

  function cancelEditing() {
    setEditState("idle");
    setDraft(displayNickname);
    setErrorMessage("");
  }

  async function saveNickname() {
    const trimmed = draft.trim();
    if (!trimmed) {
      setErrorMessage("昵称不能为空");
      return;
    }

    setEditState("saving");
    setErrorMessage("");

    try {
      const res = await fetch("/api/web/profile", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ nickname: trimmed }),
      });

      const data = await res.json();

      if (!res.ok || !data.ok) {
        setEditState("error");
        setErrorMessage(data.message || "保存失败");
        return;
      }

      setDisplayNickname(trimmed);
      setEditState("saved");
      router.refresh();
    } catch {
      setEditState("error");
      setErrorMessage("网络异常，请稍后重试。");
    }
  }

  useEffect(() => {
    if (editState !== "saved") return;
    const timer = setTimeout(() => setEditState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [editState]);

  if (editState === "editing" || editState === "saving") {
    return (
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={50}
            disabled={editState === "saving"}
            placeholder="输入昵称"
            aria-label="编辑昵称"
            aria-invalid={Boolean(errorMessage)}
            aria-describedby={errorMessage ? errorId : undefined}
            className="h-10 min-w-0 flex-1 rounded-[var(--cl-radius-control-sm)] border border-hairline bg-surface px-3 text-sm text-ink outline-none transition-colors focus-visible:ring-2 focus-visible:ring-lens-blue focus-visible:ring-offset-2 disabled:opacity-50"
            autoFocus
            onKeyDown={(event) => {
              if (event.key === "Enter") saveNickname();
              if (event.key === "Escape") cancelEditing();
            }}
          />
          <button
            type="button"
            onClick={saveNickname}
            disabled={editState === "saving"}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-[var(--cl-radius-control-sm)] text-text-secondary transition-colors hover:bg-surface-raised hover:text-ink disabled:opacity-40"
            aria-label="保存昵称"
          >
            <Check className="size-5" strokeWidth={2} />
          </button>
          <button
            type="button"
            onClick={cancelEditing}
            disabled={editState === "saving"}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-[var(--cl-radius-control-sm)] text-muted-foreground transition-colors hover:bg-surface-raised hover:text-ink disabled:opacity-40"
            aria-label="取消编辑"
          >
            <X className="size-5" strokeWidth={2} />
          </button>
        </div>
        {errorMessage ? (
          <p id={errorId} role="alert" className="mt-2 text-xs text-destructive">
            {errorMessage}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="truncate text-base font-semibold text-ink">{shownName}</span>
      {editState === "saved" ? (
        <span className="shrink-0 text-xs font-medium text-feedback-success">已保存</span>
      ) : null}
      <button
        type="button"
        onClick={startEditing}
        className="-ml-2 flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-[var(--cl-radius-control-sm)] text-muted-foreground transition-colors hover:bg-surface-raised hover:text-ink"
        aria-label="编辑昵称"
      >
        <Pencil className="size-4" strokeWidth={1.8} />
      </button>
      {editState === "error" && errorMessage ? (
        <span id={errorId} role="alert" className="text-xs text-destructive">{errorMessage}</span>
      ) : null}
    </div>
  );
}