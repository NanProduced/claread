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
    <Button variant="danger" disabled={pending} onClick={handleLogout} type="button">
      {pending ? "正在退出..." : "退出登录"}
    </Button>
  );
}
