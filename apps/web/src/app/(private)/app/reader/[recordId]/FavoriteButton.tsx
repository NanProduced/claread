"use client";

import { useEffect, useState } from "react";
import { Heart } from "lucide-react";
import { cn } from "@/lib/cn";
import { readerCommandControl } from "@/components/reader/interaction";

type FavoriteState = "loading" | "ready" | "saving" | "error";

type FavoriteApiResult =
  | {
      ok: true;
      favorited: boolean;
      message?: string;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
    };

interface FavoriteButtonProps {
  recordId: string;
  variant?: "default" | "action-bar";
}

function favoriteButtonShellClassName(favorited: boolean, variant: FavoriteButtonProps["variant"]) {
  return cn(
    readerCommandControl,
    variant === "action-bar"
      ? "flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5"
      : "min-h-[3.25rem] w-full justify-start rounded-[1rem] border-hairline px-3.5 py-2 text-left shadow-[var(--app-secondary-shadow)]",
    favorited
      ? variant === "action-bar"
        ? "text-vocab-amber"
        : "bg-surface-raised text-ink shadow-[var(--app-panel-shadow-quiet)]"
      : variant === "action-bar"
        ? "text-ink hover:text-ink-soft"
        : "bg-surface-raised text-ink-soft hover:text-ink-soft",
  );
}

async function readFavoriteResponse(response: Response): Promise<FavoriteApiResult> {
  const payload = (await response.json().catch(() => null)) as FavoriteApiResult | null;

  if (payload) {
    return payload;
  }

  return {
    ok: false,
    status: response.status,
    code: "bad_response",
    message: "收藏服务返回了无法识别的响应。",
  };
}

export function FavoriteButton({ recordId, variant = "default" }: FavoriteButtonProps) {
  const [favorited, setFavorited] = useState(false);
  const [state, setState] = useState<FavoriteState>("loading");
  const [message, setMessage] = useState("正在读取收藏状态...");

  useEffect(() => {
    let active = true;

    async function loadFavoriteState() {
      setState("loading");
      setMessage("正在读取收藏状态...");

      const response = await fetch(
        `/api/web/reader/records/${encodeURIComponent(recordId)}/favorite`,
        { cache: "no-store" },
      );
      const result = await readFavoriteResponse(response);

      if (!active) {
        return;
      }

      if (result.ok) {
        setFavorited(result.favorited);
        setState("ready");
        setMessage(result.favorited ? "已收藏" : "未收藏");
        return;
      }

      setState("error");
      setMessage(result.message);
    }

    loadFavoriteState().catch((error: unknown) => {
      if (!active) {
        return;
      }

      setState("error");
      setMessage(error instanceof Error ? error.message : "收藏状态读取失败。");
    });

    return () => {
      active = false;
    };
  }, [recordId]);

  async function toggleFavorite() {
    if (state === "saving" || state === "loading") {
      return;
    }

    const previousFavorited = favorited;
    setState("saving");
    setFavorited(!previousFavorited);
    setMessage(previousFavorited ? "正在取消收藏..." : "正在收藏...");

    try {
      const response = previousFavorited
        ? await fetch(`/api/web/reader/records/${encodeURIComponent(recordId)}/favorite`, {
            method: "DELETE",
          })
        : await fetch(`/api/web/reader/records/${encodeURIComponent(recordId)}/favorite`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ recordId }),
          });
      const result = await readFavoriteResponse(response);

      if (!result.ok) {
        setFavorited(previousFavorited);
        setState("error");
        setMessage(result.message);
        return;
      }

      setFavorited(result.favorited);
      setState("ready");
      setMessage(result.message ?? (result.favorited ? "已收藏" : "未收藏"));
    } catch (error) {
      setFavorited(previousFavorited);
      setState("error");
      setMessage(error instanceof Error ? error.message : "收藏操作失败。");
    }
  }

  const disabled = state === "loading" || state === "saving";
  const statusLabel =
    state === "loading"
      ? "同步中"
      : state === "saving"
        ? (favorited ? "保存中" : "移除中")
        : state === "error"
          ? "稍后重试"
          : favorited
            ? "已收藏此文"
            : "加入阅读资产";

  if (variant === "action-bar") {
    return (
      <>
        <button
          type="button"
          aria-pressed={favorited}
          disabled={disabled}
          onClick={toggleFavorite}
          className={favoriteButtonShellClassName(favorited, variant)}
        >
          <Heart
            aria-hidden="true"
            className={`h-[18px] w-[18px] shrink-0 ${
              favorited ? "fill-vocab-amber text-vocab-amber" : "text-muted-foreground"
            }`}
            strokeWidth={1.5}
          />
          <span className="flex min-w-0 flex-col items-start leading-none whitespace-nowrap">
            <span className="text-[0.85rem] font-semibold whitespace-nowrap">{favorited ? "已收藏" : "收藏"}</span>
            <span className="hidden sm:block mt-1 text-[0.65rem] font-medium text-subtle whitespace-nowrap">{statusLabel}</span>
          </span>
        </button>
        <p
          aria-live="polite"
          className="sr-only"
        >
          {message}
        </p>
      </>
    );
  }

  return (
    <div className="flex min-w-[8.75rem] flex-col gap-1.5">
      <button
        type="button"
        aria-pressed={favorited}
        disabled={disabled}
        onClick={toggleFavorite}
        className={favoriteButtonShellClassName(favorited, variant)}
      >
        <Heart
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 ${favorited ? "fill-vocab-amber text-vocab-amber" : "text-muted-foreground"}`}
        />
        <span className="flex min-w-0 flex-col items-start">
          <span className="text-[0.92rem] font-semibold leading-none">{favorited ? "已收藏" : "收藏"}</span>
          <span className="mt-1 text-[0.68rem] font-medium leading-none text-subtle">{statusLabel}</span>
        </span>
      </button>
      <p
        aria-live="polite"
        className={`sr-only max-w-40 text-right text-[0.6875rem] leading-4 ${
          state === "error" ? "text-red-600" : "text-subtle"
        }`}
      >
        {message}
      </p>
    </div>
  );
}
