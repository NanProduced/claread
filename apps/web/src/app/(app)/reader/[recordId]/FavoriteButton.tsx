"use client";

import { useEffect, useState } from "react";
import { Heart } from "lucide-react";

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

export function FavoriteButton({ recordId }: FavoriteButtonProps) {
  const [favorited, setFavorited] = useState(false);
  const [state, setState] = useState<FavoriteState>("loading");
  const [message, setMessage] = useState("正在读取收藏状态...");

  useEffect(() => {
    let active = true;

    async function loadFavoriteState() {
      setState("loading");
      setMessage("正在读取收藏状态...");

      const response = await fetch(
        `/api/web/favorites?recordId=${encodeURIComponent(recordId)}`,
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
        ? await fetch(`/api/web/favorites/${encodeURIComponent(recordId)}`, {
            method: "DELETE",
          })
        : await fetch("/api/web/favorites", {
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

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        aria-pressed={favorited}
        disabled={disabled}
        onClick={toggleFavorite}
        className="inline-flex h-9 items-center gap-2 rounded-pill border border-hairline bg-surface-warm px-3 text-xs font-semibold text-ink-soft transition hover:border-lens-blue/40 hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Heart
          aria-hidden="true"
          className={`h-4 w-4 ${favorited ? "fill-vocab-amber text-vocab-amber" : "text-muted"}`}
        />
        <span>{favorited ? "已收藏" : "收藏"}</span>
      </button>
      <p
        aria-live="polite"
        className={`max-w-40 text-right text-[0.6875rem] leading-4 ${
          state === "error" ? "text-red-600" : "text-subtle"
        }`}
      >
        {message}
      </p>
    </div>
  );
}
