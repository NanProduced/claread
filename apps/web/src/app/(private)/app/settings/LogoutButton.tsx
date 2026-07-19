"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { loginRouteBase } from "@/lib/routes";
import { Button } from "@/components/primitives/button";

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function handleLogout() {
    setPending(true);
    await fetch("/api/web/auth/logout", { method: "POST" });
    router.refresh();
    router.push(loginRouteBase);
  }

  return (
    <Button
      variant="ghost"
      className="min-h-11 border border-hairline bg-surface px-4 py-2.5 text-sm font-medium text-ink hover:bg-surface-raised"
      disabled={pending}
      onClick={handleLogout}
      type="button"
    >
      {pending ? "正在退出..." : "退出登录"}
    </Button>
  );
}
