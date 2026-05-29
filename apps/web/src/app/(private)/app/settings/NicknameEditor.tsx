"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Check, X } from "lucide-react";

type EditState = "idle" | "editing" | "saving" | "error" | "saved";

interface NicknameEditorProps {
  initialNickname: string;
  displayFallback?: string;
}

export function NicknameEditor({ initialNickname, displayFallback }: NicknameEditorProps) {
  const router = useRouter();
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
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={50}
          disabled={editState === "saving"}
          placeholder="输入昵称"
          aria-label="编辑昵称"
          className="h-9 flex-1 rounded border border-lens-blue bg-reader-paper px-3 text-sm text-ink outline-none focus:ring-1 focus:ring-lens-blue disabled:opacity-50"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Enter") saveNickname();
            if (e.key === "Escape") cancelEditing();
          }}
        />
        <button
          type="button"
          onClick={saveNickname}
          disabled={editState === "saving"}
          className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded text-structure-green hover:bg-structure-green/10 disabled:opacity-40"
          aria-label="保存昵称"
        >
          <Check className="size-5" strokeWidth={2} />
        </button>
        <button
          type="button"
          onClick={cancelEditing}
          disabled={editState === "saving"}
          className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded text-muted hover:bg-muted/10 disabled:opacity-40"
          aria-label="取消编辑"
        >
          <X className="size-5" strokeWidth={2} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-base font-semibold text-ink">{shownName}</span>
      {editState === "saved" ? (
        <span className="text-xs font-medium text-structure-green">已保存</span>
      ) : null}
      <button
        type="button"
        onClick={startEditing}
        className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded text-muted hover:bg-muted/10 hover:text-ink -ml-2"
        aria-label="编辑昵称"
      >
        <Pencil className="size-4" strokeWidth={1.8} />
      </button>
      {editState === "error" && errorMessage ? (
        <span className="text-xs text-red-500">{errorMessage}</span>
      ) : null}
    </div>
  );
}
