"use client";

import { useEffect } from "react";

import { GENERIC_ERROR_MESSAGE, userFacingErrorMessage } from "@/lib/user-facing-error";

/**
 * 全局错误兜底：route handler / 服务端组件抛错时，Next.js 默认错误页
 * 会把技术细节暴露给用户；这里统一为可读中文 + 重试。
 * 原始 error 只进 console，不进 UI。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[Claread] global error boundary", error);
  }, [error]);

  return (
    <html lang="zh-CN">
      <body>
        <main className="grid min-h-screen content-center justify-items-center px-6 text-center">
          <h1 className="text-2xl font-semibold">页面出了点问题</h1>
          <p className="mt-3 text-sm text-neutral-500">
            {userFacingErrorMessage(error, GENERIC_ERROR_MESSAGE)}
          </p>
          <button
            type="button"
            onClick={reset}
            className="mt-6 rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium hover:bg-neutral-100"
          >
            重试
          </button>
        </main>
      </body>
    </html>
  );
}
