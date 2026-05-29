"use client";

import { Heart } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "@/components/primitives/toast";

type FavoriteState = "idle" | "saving" | "error";

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

interface LibraryFavoriteButtonProps {
  recordId: string;
  initialFavorited: boolean;
  compact?: boolean;
  onFavoritedChange?: (favorited: boolean) => void;
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

export function LibraryFavoriteButton({
  recordId,
  initialFavorited,
  compact = false,
  onFavoritedChange,
}: LibraryFavoriteButtonProps) {
  const router = useRouter();
  const [favorited, setFavorited] = useState(initialFavorited);
  const [state, setState] = useState<FavoriteState>("idle");
  const [message, setMessage] = useState<string | null>(null);

  async function toggleFavorite() {
    if (state === "saving") {
      return;
    }

    const previous = favorited;
    setState("saving");
    setFavorited(!previous);
    setMessage(null);

    try {
      const response = previous
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
        setFavorited(previous);
        setState("error");
        setMessage(result.message);
        toast.error(result.message);
        return;
      }

      setFavorited(result.favorited);
      onFavoritedChange?.(result.favorited);
      setState("idle");
      setMessage(null);
      router.refresh();
    } catch (error) {
      const nextMessage = error instanceof Error ? error.message : "收藏操作失败。";
      setFavorited(previous);
      setState("error");
      setMessage(nextMessage);
      toast.error(nextMessage);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={toggleFavorite}
        disabled={state === "saving"}
        aria-pressed={favorited}
        aria-label={favorited ? "取消收藏" : "加入收藏"}
        title={favorited ? "已收藏" : "加入收藏"}
        className={
          compact
            ? `focus-ring group inline-flex items-center justify-center h-8 w-8 rounded-md transition-all duration-200 ${
                favorited
                  ? "text-vocab-amber hover:scale-110 active:scale-95"
                  : "text-muted hover:text-ink hover:scale-110 active:scale-95"
              }`
            : `focus-ring inline-flex items-center gap-1.5 rounded-pill px-3 py-2 text-[0.72rem] font-semibold tracking-[0.08em] transition-colors ${
                favorited
                  ? "text-vocab-amber hover:bg-[rgba(228,176,0,0.08)]"
                  : "text-muted hover:bg-surface-warm hover:text-ink"
              }`
        }
      >
        <Heart
          aria-hidden="true"
          className={
            compact
              ? `h-4.5 w-4.5 transition-all duration-200 stroke-[1.8] ${
                  favorited
                    ? "fill-vocab-amber text-vocab-amber stroke-[1.8]"
                    : "text-muted group-hover:text-ink group-hover:stroke-[2.3] group-hover:fill-ink/10"
                }`
              : `h-3.5 w-3.5 ${favorited ? "fill-vocab-amber text-vocab-amber" : ""}`
          }
        />
        {!compact && (favorited ? "已收藏" : "收藏")}
      </button>
      {state === "error" && message ? (
        <p className="max-w-44 text-right text-[0.6875rem] leading-4 text-error-red">{message}</p>
      ) : null}
    </div>
  );
}
