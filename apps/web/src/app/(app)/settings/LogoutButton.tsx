"use client";

import { useState } from "react";
import type { Route } from "next";
import { useRouter } from "next/navigation";

const loginRoute = "/login" as Route;

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function handleLogout() {
    setPending(true);
    await fetch("/api/web/auth/logout", { method: "POST" });
    router.refresh();
    router.push(loginRoute);
  }

  return (
    <button
      className="focus-ring inline-flex min-h-10 items-center rounded-pill border border-error-red/25 bg-surface px-4 text-sm font-semibold text-error-red transition-colors hover:bg-error-red/5 disabled:cursor-not-allowed disabled:opacity-50"
      disabled={pending}
      onClick={handleLogout}
      type="button"
    >
      {pending ? "正在退出..." : "退出登录"}
    </button>
  );
}
