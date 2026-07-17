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
    <div className="space-y-0">
      <div className="mb-6 border-b border-hairline pb-6 md:grid md:grid-cols-[6rem_1fr] md:gap-4">
        <div className="mb-2 text-sm text-muted-foreground md:mb-0">账户</div>
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface-raised text-sm font-semibold text-text-secondary">
            {avatarText}
          </div>
          <NicknameEditor initialNickname={nickname} displayFallback={displayFallback} />
        </div>
      </div>

      <div className="mb-6 border-b border-hairline pb-6 md:grid md:grid-cols-[6rem_1fr] md:gap-4">
        <div className="mb-2 text-sm text-muted-foreground md:mb-0">登录信息</div>
        <div className="space-y-1">
          <p className="text-sm text-ink">{phone || "Web User"}</p>
          <p className="text-sm text-muted-foreground">{statusLabel[status]}</p>
        </div>
      </div>

      <div className="md:grid md:grid-cols-[6rem_1fr] md:gap-4">
        <div className="mb-2 text-sm text-muted-foreground md:mb-0">会话</div>
        <div>
          {needsReauth ? (
            <Button
              asChild
              variant="ghost"
              className="min-h-11 px-4 py-2.5 text-sm font-semibold text-text-secondary hover:bg-surface-raised hover:text-text-primary"
            >
              <Link href={loginRoute(appSettingsRoute)}>重新登录</Link>
            </Button>
          ) : (
            <LogoutButton />
          )}
        </div>
      </div>
    </div>
  );
}
