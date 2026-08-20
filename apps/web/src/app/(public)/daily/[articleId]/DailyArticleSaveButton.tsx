"use client";

import { BookMarked } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "@/components/primitives/toast";
import { cn } from "@/lib/cn";

interface DailyArticleSaveButtonProps {
  articleId: string;
  autoSave?: boolean;
  canFavorite: boolean;
  loginHref: string;
}

const actionClassName =
  "dr-font-ui focus-ring mt-5 inline-flex min-h-11 items-center gap-2 px-5 text-[length:var(--dr-type-caption-size)] font-semibold transition-opacity hover:opacity-90";

interface FavoriteResult {
  ok: boolean;
  favorited?: boolean;
  code?: string;
  message?: string;
}

async function readFavoriteResult(response: Response): Promise<FavoriteResult> {
  try {
    return (await response.json()) as FavoriteResult;
  } catch {
    return { ok: false, message: "收藏服务暂时不可用，请稍后重试。" };
  }
}

export function DailyArticleSaveButton({
  articleId,
  autoSave = false,
  canFavorite,
  loginHref,
}: DailyArticleSaveButtonProps) {
  const [favorited, setFavorited] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "saving" | "error">(
    canFavorite ? "loading" : "ready",
  );
  const [errorCode, setErrorCode] = useState<string>();
  const [message, setMessage] = useState("");
  const endpoint = `/api/web/daily-reader/${encodeURIComponent(articleId)}/favorite`;

  useEffect(() => {
    if (!canFavorite) return;

    let active = true;
    const load = async () => {
      try {
        const result = await readFavoriteResult(await fetch(endpoint, { cache: "no-store" }));
        if (!active) return;
        if (!result.ok) {
          setState("error");
          setErrorCode(result.code);
          setMessage(result.message ?? "收藏状态读取失败，请重试。");
          return;
        }

        let nextFavorited = Boolean(result.favorited);
        if (autoSave) {
          const saved = await readFavoriteResult(await fetch(endpoint, { method: "POST" }));
          if (!active) return;
          if (!saved.ok) {
            setState("error");
            setErrorCode(saved.code);
            setMessage(saved.message ?? "收藏失败，请重试。");
            return;
          }
          nextFavorited = Boolean(saved.favorited);
          toast.success("已加入阅读记录");
          const url = new URL(window.location.href);
          url.searchParams.delete("intent");
          window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
        }

        setFavorited(nextFavorited);
        setErrorCode(undefined);
        setState("ready");
      } catch {
        if (!active) return;
        setState("error");
        setErrorCode(undefined);
        setMessage("收藏状态读取失败，请重试。");
      }
    };

    void load();

    return () => {
      active = false;
    };
  }, [autoSave, canFavorite, endpoint]);

  if (!canFavorite) {
    return (
      <Link
        href={loginHref}
        className={cn(
          actionClassName,
          "bg-[var(--dr-ink)] text-[color:var(--dr-paper)]",
        )}
      >
        <BookMarked aria-hidden="true" className="h-4 w-4" />
        加入我的阅读记录
      </Link>
    );
  }

  const save = async () => {
    if (state === "loading" || state === "saving") return;

    const wasFavorited = favorited;
    setState("saving");
    setErrorCode(undefined);
    setMessage("");

    try {
      const result = await readFavoriteResult(
        await fetch(endpoint, { method: wasFavorited ? "DELETE" : "POST" }),
      );
      if (!result.ok) {
        setState("error");
        setErrorCode(result.code);
        setMessage(result.message ?? `${wasFavorited ? "移除" : "收藏"}失败，请重试。`);
        toast.error(`${wasFavorited ? "移除" : "收藏"}失败，请重试`);
        return;
      }

      const nextFavorited = Boolean(result.favorited);
      setFavorited(nextFavorited);
      setErrorCode(undefined);
      setState("ready");
      toast.success(nextFavorited ? "已加入阅读记录" : "已从阅读记录移除");
    } catch {
      setState("error");
      const action = wasFavorited ? "移除" : "收藏";
      setErrorCode(undefined);
      setMessage(`${action}失败，请检查网络后重试。`);
      toast.error(`${action}失败，请检查网络后重试`);
    }
  };

  const loading = state === "loading";
  const saving = state === "saving";
  const label = loading
    ? "正在读取收藏状态"
    : saving
      ? favorited
        ? "正在移除"
        : "正在加入"
      : state === "error"
        ? favorited
          ? "重试移除"
          : "重试收藏"
        : favorited
          ? "已加入阅读记录"
          : "加入我的阅读记录";

  if (state === "error" && (errorCode === "auth_required" || errorCode === "upstream_auth_failed")) {
    return (
      <div>
        <Link
          href={loginHref}
          className={cn(actionClassName, "bg-[var(--dr-ink)] text-[color:var(--dr-paper)]")}
        >
          <BookMarked aria-hidden="true" className="h-4 w-4" />
          重新登录
        </Link>
        <p
          role="status"
          className="dr-font-zh mt-3 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]"
        >
          {message}
        </p>
      </div>
    );
  }

  return (
    <div>
      <button
        type="button"
        aria-label={label}
        aria-pressed={favorited}
        aria-busy={loading || saving}
        disabled={loading || saving}
        onClick={save}
        className={cn(
          actionClassName,
          favorited
            ? "border border-[color:var(--dr-ink)] bg-transparent text-[color:var(--dr-ink)]"
            : "bg-[var(--dr-ink)] text-[color:var(--dr-paper)]",
          (loading || saving) && "cursor-wait opacity-60",
        )}
      >
        <BookMarked aria-hidden="true" className="h-4 w-4" />
        {label}
      </button>
      {state === "error" ? (
        <p
          role="status"
          className="dr-font-zh mt-3 text-[length:var(--dr-type-caption-size)] leading-[var(--dr-type-caption-lh)] text-[color:var(--dr-meta)]"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}
