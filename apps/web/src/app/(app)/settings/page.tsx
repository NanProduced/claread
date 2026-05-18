import { MessageSquare, SlidersHorizontal, UserRound } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { Select } from "@/components/primitives/select";
import { getProfileSettings, type ProfileBffStatus } from "@/services/bff/profile";
import { FeedbackForm } from "./FeedbackForm";
import { LogoutButton } from "./LogoutButton";

const statusLabel: Record<ProfileBffStatus, string> = {
  ready: "已连接账户",
  unauthenticated: "会话已过期",
  mock_session: "会话不可用",
  upstream_unavailable: "账户服务暂不可用",
  upstream_error: "账户读取失败",
};
const loginRoute = "/login" as Route;

const paperToneItems = [
  { label: "Warm Paper (默认)", value: "warm", description: "暖纸底色，适合长时阅读。" },
  { label: "Clean White", value: "white", description: "更干净，更接近文档阅读器。" },
  { label: "Sage Green", value: "sage", description: "轻微冷静感，适合注释密度高的场景。" },
];

const readingDensityItems = [
  { label: "标准 (默认)", value: "standard" },
  { label: "偏大", value: "large" },
  { label: "紧凑", value: "compact" },
];

const translationToneItems = [
  { label: "柔和 (默认)", value: "muted" },
  { label: "清晰", value: "standard" },
  { label: "隐藏", value: "hidden" },
];

function PreferenceRow({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-3 border-t border-hairline py-5 md:grid-cols-[minmax(0,1fr)_220px] md:items-center">
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
      </div>
      {children}
    </div>
  );
}

export default async function SettingsPage() {
  const settings = await getProfileSettings();
  const quota = settings.quota;
  const quotaPercent = quota
    ? Math.min(100, Math.round((quota.quotaUsed / Math.max(quota.quotaLimit, 1)) * 100))
    : 0;
  const displayName = settings.profile?.nickname || settings.session.phone || "Web User";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";

  return (
    <main className="paper-grain min-h-screen px-5 py-7 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto grid max-w-7xl gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0">
          <header className="mb-7 max-w-3xl border-b border-hairline pb-6">
            <p className="mb-3 text-xs font-semibold text-muted">账户与偏好</p>
            <h1 className="font-headline text-[2.15rem] font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
              设置
            </h1>
            <p className="mt-3 text-sm leading-6 text-muted">
              查看账户状态、解析额度和阅读偏好。反馈入口放在这里，不打断阅读链路。
            </p>
            {settings.message ? (
              <p className="mt-3 text-sm leading-6 text-muted">{settings.message}</p>
            ) : null}
          </header>

          <div className="space-y-8">
            <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet sm:p-6">
              <div className="flex items-start gap-4">
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-lens-blue-soft font-headline text-xl font-semibold text-lens-blue">
                  {avatarText}
                </div>
                <div className="min-w-0 flex-1">
                  <h2 className="text-base font-semibold text-ink">
                    {settings.session.phone ? `手机号用户 ${settings.session.phone}` : displayName}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-muted">{statusLabel[settings.status]}</p>
                </div>
              </div>

              <div className="mt-6 border-t border-hairline pt-5">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-ink">今日解析点数</h3>
                    <p className="mt-1 text-xs text-muted">
                      {quota
                        ? `${quota.dailyUsedPoints ?? quota.quotaUsed} / ${
                            quota.dailyFreePoints ?? quota.quotaLimit
                          } 点`
                        : "不可用"}
                    </p>
                  </div>
                  {quota ? (
                    <p className="text-xs text-muted">
                      剩余 {quota.remainingPoints ?? 0} 点 · 奖励 {quota.bonusPoints ?? 0} 点
                    </p>
                  ) : null}
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-reader-paper">
                  <div className="h-full rounded-full bg-lens-blue" style={{ width: `${quotaPercent}%` }} />
                </div>
              </div>
            </section>

            <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet sm:p-6">
              <div className="mb-1 flex items-center gap-2">
                <SlidersHorizontal aria-hidden="true" className="h-4 w-4 text-lens-blue" />
                <h2 className="text-base font-semibold text-ink">阅读偏好</h2>
              </div>
              <PreferenceRow title="背景纸色" description="选择 Reader 模式的默认底色。">
                <Select items={paperToneItems} defaultValue="warm" />
              </PreferenceRow>
              <PreferenceRow title="字号与行距" description="影响英文正文显示。">
                <Select items={readingDensityItems} defaultValue="standard" />
              </PreferenceRow>
              <PreferenceRow title="默认翻译显示" description="进入 Reader 时译文的可见性。">
                <Select items={translationToneItems} defaultValue="muted" />
              </PreferenceRow>
              <p className="border-t border-hairline pt-5 text-xs leading-5 text-muted">
                偏好保存能力后续接入。当前控件先确定 Web 端阅读设置的形态。
              </p>
            </section>

            <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet sm:p-6">
              <div className="mb-5 flex items-center gap-2">
                <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue" />
                <h2 className="text-base font-semibold text-ink">反馈</h2>
              </div>
              <FeedbackForm />
            </section>
          </div>
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <UserRound aria-hidden="true" className="h-4 w-4 text-lens-blue" />
              当前会话
            </h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              {settings.session.phone ? `已登录手机号 ${settings.session.phone}` : "当前会话不可用。"}
            </p>
            <div className="mt-5 border-t border-hairline pt-4">
              {settings.status === "unauthenticated" || settings.status === "mock_session" ? (
                <Link
                  href={loginRoute}
                  className="focus-ring inline-flex min-h-10 items-center rounded-pill border border-hairline bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:border-muted"
                >
                  重新登录
                </Link>
              ) : (
                <LogoutButton />
              )}
            </div>
          </section>

          <section className="rounded-panel border border-hairline bg-reader-paper p-5">
            <h2 className="text-sm font-semibold text-ink">订阅升级</h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              暂未开放。第一版只展示可用额度，不引入付费配置流程。
            </p>
          </section>
        </aside>
      </div>
    </main>
  );
}
