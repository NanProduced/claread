"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function handleLogout() {
    setPending(true);
    await fetch("/api/web/auth/logout", { method: "POST" });
    router.refresh();
    router.push("/app/login");
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
