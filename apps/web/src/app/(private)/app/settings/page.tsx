import { MessageSquare, Palette } from "lucide-react";
import Link from "next/link";
import { ApertureWatermark } from "@/components/brand/BrandMarks";
import { SectionCard } from "@/components/composed";
import { Button } from "@/components/primitives/button";
import { ScrollArea } from "@/components/primitives/scroll-area";
import { appSettingsRoute, loginRoute } from "@/lib/routes";
import { getProfileSettings, type ProfileBffStatus } from "@/services/bff/profile";
import { CreditLedgerSection } from "./CreditLedgerSection";
import { FeedbackForm } from "./FeedbackForm";
import { LogoutButton } from "./LogoutButton";
import { ThemePreferencesSection } from "./ThemePreferencesSection";

const statusLabel: Record<ProfileBffStatus, string> = {
  ready: "已连接账户",
  unauthenticated: "会话已过期",
  limited_debug: "调试态受限",
  upstream_unavailable: "账户服务暂不可用",
  upstream_error: "账户读取失败",
};

export default async function SettingsPage() {
  const settings = await getProfileSettings();
  const quota = settings.quota;
  const quotaPercent = quota
    ? Math.min(100, Math.round((quota.quotaUsed / Math.max(quota.quotaLimit, 1)) * 100))
    : 0;
  const displayName = settings.profile?.nickname || settings.session.phone || "Web User";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-reader-paper px-4 py-6 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex min-h-0 w-full max-w-[1300px] flex-1 flex-col">
        <div className="grid min-h-0 flex-1 gap-12 lg:gap-20 xl:gap-28 lg:grid-cols-[minmax(0,1fr)_280px] xl:grid-cols-[minmax(0,1fr)_320px]">
          {/* Left scrollable panel */}
          <div className="flex h-full min-h-0 flex-col space-y-2 lg:py-12">
            {/* Settings Header */}
            <div className="mb-6 shrink-0 flex flex-col sm:flex-row sm:items-end justify-between gap-4 pl-2 border-b border-hairline pb-5">
              <div>
                <div className="mb-2 flex items-center gap-3">
                  <p className="text-[0.6rem] font-bold uppercase tracking-[0.2em] text-lens-blue">Settings</p>
                  <div className="h-[1px] w-8 bg-hairline" />
                </div>
                <h1 className="font-headline text-[2rem] font-semibold leading-[1] tracking-tight text-ink md:text-[2.5rem] lg:text-[3rem]">
                  Preferences.
                </h1>
              </div>
            </div>

            {/* Form list in ScrollArea */}
            <ScrollArea className="min-h-0 flex-1">
              <div className="space-y-10 pr-5 pb-8">
                {/* Account & Quota Meter */}
                <SectionCard>
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
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-reader-paper border border-hairline/50">
                      <div className="h-full rounded-full bg-lens-blue" style={{ width: `${quotaPercent}%` }} />
                    </div>
                    {quota ? <CreditLedgerSection /> : null}
                  </div>
                </SectionCard>

                {/* Theme options */}
                <SectionCard
                  title="主题"
                  icon={Palette}
                  footer={
                    <p className="text-xs leading-5 text-muted">
                      当前主题会同时影响全站壳层、功能页和 Reader，不再区分单独的 Reader 默认纸面。
                    </p>
                  }
                >
                  <ThemePreferencesSection />
                </SectionCard>

                {/* Feedback form */}
                <SectionCard title="反馈" icon={MessageSquare}>
                  <FeedbackForm />
                </SectionCard>
              </div>
            </ScrollArea>
          </div>

          {/* Hanging Bookmark Card on the Right */}
          <aside className="relative hidden min-w-0 lg:block">
            <div className="sticky top-8 px-2 pb-16">
              <div className="relative mx-auto w-full max-w-[18.5rem]">
                {/* Paper Clip Hook */}
                <div className="pointer-events-none absolute -top-6 right-5 z-30 text-muted/40">
                  <svg width="20" height="42" viewBox="0 0 24 48" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 36V12a4 4 0 0 0-8 0v28a6 6 0 0 0 12 0V12a8 8 0 0 0-16 0v24" />
                  </svg>
                </div>

                {/* Bookmark Body */}
                <div className="overflow-hidden rounded-t-[1.45rem] border border-hairline border-b-0 bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_82%,white)_0%,color-mix(in_srgb,var(--reader-paper)_88%,white)_100%)] px-7 pb-8 pt-10 shadow-[0_16px_40px_rgba(28,24,18,0.08)]">
                  <div className="mb-6">
                    <p className="text-[0.58rem] font-bold uppercase tracking-[0.18em] text-subtle">Claread Preferences</p>
                    <h2 className="mt-1.5 font-headline text-[1.2rem] font-semibold leading-tight text-ink">账户与会话书签</h2>
                  </div>

                  <div className="space-y-8">
                    {/* Session status info */}
                    <section>
                      <p className="font-reading text-[0.98rem] leading-[1.75] text-ink">
                        当前会话为手机号用户 <span className="font-semibold text-lens-blue">{settings.session.phone || "Web User"}</span>，账户状态已验证。
                      </p>
                      <p className="mt-2 font-sans text-[0.76rem] font-medium tracking-[0.02em] text-muted">
                        状态标示：{statusLabel[settings.status]}
                      </p>
                      {settings.message && <p className="mt-1 text-xs text-muted">{settings.message}</p>}
                    </section>

                    {/* Action buttons */}
                    <section className="border-t border-hairline pt-7">
                      <div className="mb-4">
                        <h3 className="text-[0.62rem] font-bold uppercase tracking-[0.16em] text-subtle">
                          会话管理
                        </h3>
                      </div>
                      <div className="w-full">
                        {settings.status === "unauthenticated" || settings.status === "limited_debug" ? (
                          <Button asChild variant="primary-ink" className="w-full py-2.5 text-center text-[0.8rem] font-semibold tracking-[0.08em] justify-center">
                            <Link href={loginRoute(appSettingsRoute)}>重新登录</Link>
                          </Button>
                        ) : (
                          <LogoutButton />
                        )}
                      </div>
                    </section>

                    {/* Subscription info */}
                    <section className="border-t border-hairline pt-7">
                      <h3 className="text-[0.68rem] font-bold uppercase tracking-[0.14em] text-ink">订阅升级</h3>
                      <p className="mt-2 text-[0.75rem] leading-relaxed text-muted">
                        Claread 订阅计划暂未开放。当前第一版本仅展示每日解析点数额度，尚未引入付费配置流程。
                      </p>
                    </section>
                  </div>
                </div>

                {/* Bottom Fold Cutout */}
                <div
                  className="relative -mt-px h-[4.5rem] overflow-hidden border-x border-b border-hairline bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_84%,white)_0%,color-mix(in_srgb,var(--reader-paper)_92%,white)_100%)] shadow-[0_18px_32px_rgba(28,24,18,0.06)]"
                  style={{ clipPath: "polygon(0 0, 100% 0, 100% 100%, 66% 100%, 50% 70%, 34% 100%, 0 100%)" }}
                >
                  <ApertureWatermark
                    size={120}
                    className="absolute bottom-[-3.5rem] right-[-2.5rem] opacity-[0.04] saturate-0"
                  />
                </div>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </main>
  );
}
