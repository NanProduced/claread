"use client";

import { useEffect, useState } from "react";
import { Heart } from "lucide-react";
import { Button } from "@/components/primitives/button";

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
  const statusLabel =
    state === "loading"
      ? "同步中"
      : state === "saving"
        ? (favorited ? "保存中" : "移除中")
        : state === "error"
          ? "稍后重试"
          : favorited
            ? "已收入阅读资产"
            : "加入阅读资产";

  return (
    <div className="flex min-w-[8.75rem] flex-col gap-1.5">
      <Button
        type="button"
        aria-pressed={favorited}
        disabled={disabled}
        onClick={toggleFavorite}
        variant="quiet"
        size="sm"
        className={`min-h-[3.25rem] justify-start rounded-[1rem] px-3.5 py-2 text-left ${
          favorited
            ? "border-[rgba(228,176,0,0.3)] bg-[linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,250,235,0.98))] text-ink shadow-[0_10px_22px_rgba(166,121,0,0.08)]"
            : "border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(249,247,241,0.98))] text-ink-soft shadow-[0_8px_18px_rgba(17,17,17,0.04),inset_0_1px_0_rgba(255,255,255,0.72)]"
        }`}
      >
        <Heart
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 ${favorited ? "fill-vocab-amber text-vocab-amber" : "text-muted"}`}
        />
        <span className="flex min-w-0 flex-col items-start">
          <span className="text-[0.92rem] font-semibold leading-none">{favorited ? "已收藏" : "收藏"}</span>
          <span className="mt-1 text-[0.68rem] font-medium leading-none text-subtle">{statusLabel}</span>
        </span>
      </Button>
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
