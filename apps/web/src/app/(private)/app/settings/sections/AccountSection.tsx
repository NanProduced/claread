import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { appSettingsRoute, loginRoute } from "@/lib/routes";
import type { ProfileBffStatus } from "@/services/bff/profile";
import { LogoutButton } from "../LogoutButton";
import { NicknameEditor } from "../NicknameEditor";

const statusLabel: Record<ProfileBffStatus, string> = {
  ready: "已连接",
  unauthenticated: "会话过期",
  limited_debug: "调试受限",
  upstream_unavailable: "服务不可用",
  upstream_error: "读取失败",
};

interface AccountSectionProps {
  nickname: string;
  displayFallback: string;
  phone: string | undefined;
  status: ProfileBffStatus;
  avatarText: string;
}

export function AccountSection({
  nickname,
  displayFallback,
  phone,
  status,
  avatarText,
}: AccountSectionProps) {
  const needsReauth = status === "unauthenticated" || status === "limited_debug";

  return (
    <div className="flex items-start gap-6">
      <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-ink font-display text-2xl font-medium text-surface">
        {avatarText}
      </div>
      <div className="flex-1 space-y-3 pt-1">
        <div>
          <NicknameEditor initialNickname={nickname} displayFallback={displayFallback} />
          <p className="mt-1.5 text-sm text-muted-foreground">
            {phone || "Web User"}
            <span className="mx-2 text-hairline">/</span>
            <span className={status === "ready" ? "text-subtle" : "text-amber-600"}>
              {statusLabel[status]}
            </span>
          </p>
        </div>
        <div className="pt-2">
          {needsReauth ? (
            <Button
              asChild
              variant="ghost"
              className="h-auto p-0 text-sm font-semibold text-lens-blue hover:bg-transparent hover:underline hover:text-lens-blue-dark"
            >
              <Link href={loginRoute(appSettingsRoute)}>重新登录</Link>
            </Button>
          ) : (
            <div className="inline-flex">
              <LogoutButton />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
