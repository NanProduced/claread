import { MessageSquare, SlidersHorizontal, UserRound } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { InfoCard, PageHeader, SectionCard, SelectField } from "@/components/composed";
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
          <PageHeader
            eyebrow="账户与偏好"
            title="设置"
            description="查看账户状态、解析额度和阅读偏好。反馈入口放在这里，不打断阅读链路。"
            message={settings.message}
            className="max-w-3xl"
          />

          <div className="space-y-8">
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
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-reader-paper">
                  <div className="h-full rounded-full bg-lens-blue" style={{ width: `${quotaPercent}%` }} />
                </div>
              </div>
            </SectionCard>

            <SectionCard
              title="阅读偏好"
              icon={SlidersHorizontal}
              footer={
                <p className="text-xs leading-5 text-muted">
                  偏好保存能力后续接入。当前控件先确定 Web 端阅读设置的形态。
                </p>
              }
            >
              <SelectField label="背景纸色" description="选择 Reader 模式的默认底色。" items={paperToneItems} defaultValue="warm" />
              <SelectField label="字号与行距" description="影响英文正文显示。" items={readingDensityItems} defaultValue="standard" />
              <SelectField label="默认翻译显示" description="进入 Reader 时译文的可见性。" items={translationToneItems} defaultValue="muted" />
            </SectionCard>

            <SectionCard title="反馈" icon={MessageSquare}>
              <FeedbackForm />
            </SectionCard>
          </div>
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <InfoCard
            title="当前会话"
            icon={UserRound}
            description={settings.session.phone ? `已登录手机号 ${settings.session.phone}` : "当前会话不可用。"}
            footer={
              settings.status === "unauthenticated" || settings.status === "mock_session" ? (
                <Button asChild variant="outline">
                  <Link href={loginRoute}>重新登录</Link>
                </Button>
              ) : (
                <LogoutButton />
              )
            }
          />

          <InfoCard
            title="订阅升级"
            tone="paper"
            description="暂未开放。第一版只展示可用额度，不引入付费配置流程。"
          />
        </aside>
      </div>
    </main>
  );
}
