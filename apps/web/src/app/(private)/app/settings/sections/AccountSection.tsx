import Link from "next/link";

import { Button } from "@/components/primitives/button";
import { appReadRoute, loginRoute } from "@/lib/routes";
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
    <div className="divide-y divide-hairline">
      <section className="py-7 first:pt-0" aria-labelledby="account-identity-heading">
        <h3 id="account-identity-heading" className="text-sm font-medium text-ink">账户</h3>
        <div className="mt-5 flex items-center gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-surface-raised text-sm font-semibold text-text-secondary">
            {avatarText}
          </div>
          <NicknameEditor initialNickname={nickname} displayFallback={displayFallback} />
        </div>
      </section>

      <section className="py-7" aria-labelledby="account-login-heading">
        <h3 id="account-login-heading" className="text-sm font-medium text-ink">登录信息</h3>
        <dl className="mt-4 divide-y divide-hairline text-sm">
          <div className="flex min-h-12 items-center justify-between gap-6 py-3 first:pt-0">
            <dt className="text-muted-foreground">账号</dt>
            <dd className="text-right text-ink">{phone || "Web User"}</dd>
          </div>
          <div className="flex min-h-12 items-center justify-between gap-6 py-3 last:pb-0">
            <dt className="text-muted-foreground">状态</dt>
            <dd className="text-right text-muted-foreground">{statusLabel[status]}</dd>
          </div>
        </dl>
      </section>

      <section className="py-7" aria-labelledby="account-session-heading">
        <h3 id="account-session-heading" className="text-sm font-medium text-ink">会话</h3>
        <div className="mt-3">
          {needsReauth ? (
            <Button
              asChild
              variant="ghost"
              className="min-h-11 rounded-[var(--cl-radius-control-sm)] px-4 text-sm font-medium text-text-secondary hover:bg-surface-raised hover:text-text-primary"
            >
              <Link href={loginRoute(appReadRoute)}>重新登录</Link>
            </Button>
          ) : (
            <LogoutButton />
          )}
        </div>
      </section>
    </div>
  );
}