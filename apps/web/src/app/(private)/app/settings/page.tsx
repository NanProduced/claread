import { ArrowRight, HelpCircle } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";
import { Button } from "@/components/primitives/button";
import { ScrollArea } from "@/components/primitives/scroll-area";
import { Tooltip, TooltipProvider, TooltipTrigger, TooltipContent } from "@/components/primitives/tooltip";
import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import { appSettingsRoute, loginRoute } from "@/lib/routes";
import { getProfileSettings, type ProfileBffStatus } from "@/services/bff/profile";
import { LogoutButton } from "./LogoutButton";
import { NicknameEditor } from "./NicknameEditor";
import { ReadingDefaultsSection } from "./ReadingDefaultsSection";
import { ThemePreferencesSection } from "./ThemePreferencesSection";

const statusLabel: Record<ProfileBffStatus, string> = {
  ready: "已连接",
  unauthenticated: "会话过期",
  limited_debug: "调试受限",
  upstream_unavailable: "服务不可用",
  upstream_error: "读取失败",
};

function Section({ id, title, children }: { id?: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="group flex flex-col md:grid md:grid-cols-[140px_1fr] md:gap-x-16 gap-y-6 py-14">
      <div className="shrink-0 pt-1.5">
        <h2 className="text-[0.7rem] font-bold uppercase tracking-[0.25em] text-subtle md:text-right">
          {title}
        </h2>
      </div>
      <div className="min-w-0 max-w-2xl">
        {children}
      </div>
    </section>
  );
}

export default async function SettingsPage() {
  const settings = await getProfileSettings();
  const quota = settings.quota;
  const displayName = settings.profile?.nickname || settings.session.phone || "Web User";
  const realNickname = settings.profile?.nickname || "";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";
  const readingDefaults = readReadingDefaultsFromSettings(settings.profile?.settings);
  const canEditSharedDefaults = settings.status === "ready";

  const quotaLimit = quota ? (quota.dailyFreePoints ?? quota.quotaLimit) : 0;
  const quotaUsed = quota ? (quota.dailyUsedPoints ?? quota.quotaUsed) : 0;
  const quotaPercentage = quotaLimit > 0 ? Math.min(100, Math.max(0, (quotaUsed / quotaLimit) * 100)) : 0;

  return (
    <ScrollArea className="h-dvh bg-reader-paper text-ink">
      <main className="flex flex-col px-6 py-16 sm:px-12 lg:px-24 xl:px-32 mx-auto w-full max-w-[1200px]">
        <div className="mx-auto w-full max-w-[880px] pb-32">
        {/* Title */}
        <div className="mb-12">
          <h1 className="font-display text-[3.5rem] font-semibold leading-[1.05] tracking-tight text-ink md:text-[4.5rem]">
            Preferences.
          </h1>
        </div>

        <div className="divide-y divide-hairline">
          {/* Account */}
          <Section title="Account">
            <div className="flex items-start gap-6">
              <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-ink font-display text-2xl font-medium text-surface">
                {avatarText}
              </div>
              <div className="flex-1 space-y-3 pt-1">
                <div>
                  <NicknameEditor initialNickname={realNickname} displayFallback={displayName} />
                  <p className="mt-1.5 text-sm text-muted">
                    {settings.session.phone || "Web User"} 
                    <span className="mx-2 text-hairline">/</span> 
                    <span className={settings.status === "ready" ? "text-subtle" : "text-amber-600"}>
                      {statusLabel[settings.status]}
                    </span>
                  </p>
                </div>
                <div className="pt-2">
                  {settings.status === "unauthenticated" || settings.status === "limited_debug" ? (
                    <Button asChild variant="ghost" className="h-auto p-0 text-sm font-semibold text-lens-blue hover:bg-transparent hover:underline hover:text-lens-blue-dark">
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
          </Section>

          {/* Quota */}
          <Section title="Quota">
            <div className="space-y-6">
              <div>
                <p className="text-sm font-medium text-muted mb-2">今日解析点数</p>
                <div className="flex items-baseline gap-2">
                  <span className="font-display text-[3.5rem] leading-none tracking-tight text-ink">
                    {quota ? quotaUsed : "--"}
                  </span>
                  <span className="text-lg font-medium text-muted">/ {quota ? quotaLimit : "--"}</span>
                </div>
                
                {/* Subtle Progress Track */}
                {quota && (
                  <div className="mt-4 h-[2px] w-full max-w-[280px] bg-hairline rounded-full overflow-hidden">
                    <div 
                      className="h-full bg-lens-blue rounded-full transition-all duration-500 ease-out-expo" 
                      style={{ width: `${quotaPercentage}%` }}
                    />
                  </div>
                )}
              </div>
              
              {quota && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                  <span className="text-muted">剩余可用 <strong className="font-semibold text-ink">{quota.remainingPoints ?? 0}</strong></span>
                  <span className="hidden sm:inline text-hairline">|</span>
                  <span className="text-muted">额外奖励 <strong className="font-semibold text-ink">{quota.bonusPoints ?? 0}</strong></span>
                </div>
              )}

              {quota && (
                <div className="pt-2">
                  <Link href={`${appSettingsRoute}/ledger` as Route} className="group inline-flex items-center gap-1.5 text-sm font-semibold text-lens-blue transition-colors hover:text-lens-blue-dark">
                    <span>查看明细账单</span>
                    <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                  </Link>
                </div>
              )}
            </div>
          </Section>

          {/* Appearance */}
          <Section title="Appearance">
            <div className="mb-6">
              <h3 className="font-headline text-2xl font-semibold text-ink">主题偏好</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">
                当前主题会同时影响全站外壳、功能页和阅读器视图。
              </p>
            </div>
            <ThemePreferencesSection />
          </Section>

          {/* Reading Defaults */}
          <Section title="Reading">
            <div className="mb-6">
              <div className="flex items-center gap-2">
                <h3 className="font-headline text-2xl font-semibold text-ink">默认透读设置</h3>
                <TooltipProvider>
                  <Tooltip delayDuration={300}>
                    <TooltipTrigger className="text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue rounded-full p-1 transition-colors cursor-help">
                      <HelpCircle className="h-5 w-5" />
                    </TooltipTrigger>
                    <TooltipContent side="right" className="max-w-[280px]">
                      <p>这里的设置仅作为每次新建阅读时的初始默认值。在实际解析文章前，您依然可以针对单篇文章自由调整。</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-muted">
                新建阅读任务时优先带入的目标与难度。
              </p>
            </div>
            <ReadingDefaultsSection
              readingGoal={readingDefaults.readingGoal}
              readingVariant={readingDefaults.readingVariant}
              canEdit={canEditSharedDefaults}
            />
          </Section>

          {/* Support */}
          <Section title="Support">
            <div>
              <Link href={`${appSettingsRoute}/feedback` as Route} className="group inline-flex items-center gap-2 font-headline text-2xl font-semibold text-ink transition-colors hover:text-lens-blue">
                <span>提交建议与反馈</span>
                <ArrowRight className="h-5 w-5 text-muted transition-transform group-hover:translate-x-1.5 group-hover:text-lens-blue" />
              </Link>
              <p className="mt-3 text-sm text-muted max-w-md leading-relaxed">
                遇到体验问题或有新的想法？查阅历史记录或直接告诉 Claread 团队。
              </p>
            </div>
          </Section>
        </div>
      </div>
    </main>
    </ScrollArea>
  );
}
