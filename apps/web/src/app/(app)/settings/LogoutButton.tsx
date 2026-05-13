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
      className="text-sm font-semibold text-error-red hover:opacity-80 disabled:opacity-50"
      disabled={pending}
      onClick={handleLogout}
      type="button"
    >
      {pending ? "正在退出..." : "退出登录"}
    </button>
  );
}
