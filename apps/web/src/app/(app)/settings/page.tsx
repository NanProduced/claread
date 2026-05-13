import { getProfileSettings } from "@/services/bff/profile";

import { LogoutButton } from "./LogoutButton";

export default async function SettingsPage() {
  const settings = await getProfileSettings();
  const quota = settings.quota;
  const quotaPercent = quota
    ? Math.min(100, Math.round((quota.quotaUsed / Math.max(quota.quotaLimit, 1)) * 100))
    : 0;
  const displayName = settings.profile?.nickname || settings.session.phone || "Web User";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";

  return (
    <main className="flex-1 flex justify-center py-10 px-6">
      <div className="w-full max-w-2xl flex flex-col gap-8">
        <header className="border-b border-hairline pb-4">
          <h1 className="text-[1.75rem] font-headline font-semibold text-ink">
            账户与设置
          </h1>
        </header>

        <section className="flex flex-col gap-4">
          <h2 className="text-[1.125rem] font-title font-semibold text-ink">订阅配额</h2>
          <div className="bg-surface rounded-note border border-hairline shadow-surface-quiet p-6 flex flex-col gap-6">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-lens-blue-soft text-lens-blue flex items-center justify-center font-display font-bold text-xl">
                {avatarText}
              </div>
              <div className="flex flex-col">
                <span className="font-semibold text-ink">
                  {settings.session.phone ? `手机号用户 ${settings.session.phone}` : displayName}
                </span>
                <span className="text-sm text-muted">
                  {settings.status === "ready"
                    ? "已连接 FastAPI session"
                    : settings.status === "mock_session"
                      ? "本地 mock 登录态，未连接真实账户"
                      : settings.status === "unauthenticated"
                        ? "未登录"
                        : "上游账户服务暂不可用"}
                </span>
              </div>
            </div>

            <div className="border-t border-hairline pt-5">
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm font-semibold text-ink">今日解析点数</span>
                <span className="text-sm text-muted">
                  {quota ? `${quota.dailyUsedPoints ?? quota.quotaUsed} / ${quota.dailyFreePoints ?? quota.quotaLimit} 点` : "不可用"}
                </span>
              </div>
              <div className="w-full h-2 bg-web-canvas rounded-full overflow-hidden">
                <div className="h-full bg-lens-blue rounded-full" style={{ width: `${quotaPercent}%` }}></div>
              </div>
              {quota ? (
                <p className="text-[0.75rem] text-muted mt-3">
                  剩余 {quota.remainingPoints ?? 0} 点，奖励点数 {quota.bonusPoints ?? 0} 点。
                </p>
              ) : (
                <p className="text-[0.75rem] text-muted mt-3">{settings.message}</p>
              )}
            </div>

            <button
              className="w-full py-2.5 bg-web-canvas text-muted rounded-pill text-[0.8125rem] font-semibold mt-2 cursor-not-allowed"
              disabled
            >
              订阅升级暂未开放
            </button>
          </div>
        </section>

        <section className="flex flex-col gap-4">
          <h2 className="text-[1.125rem] font-title font-semibold text-ink">阅读偏好</h2>
          <div className="bg-surface rounded-note border border-hairline shadow-surface-quiet overflow-hidden">
            <div className="p-5 border-b border-hairline flex justify-between items-center">
              <div className="flex flex-col">
                <span className="font-semibold text-ink text-[0.9375rem]">背景纸色</span>
                <span className="text-xs text-muted">选择 Reader 模式的默认底色</span>
              </div>
              <select className="bg-web-canvas border border-hairline rounded-md px-3 py-1.5 text-sm outline-none">
                <option value="warm">Warm Paper (默认)</option>
                <option value="white">Clean White</option>
                <option value="sage">Sage Green</option>
              </select>
            </div>
            <div className="p-5 border-b border-hairline flex justify-between items-center">
              <div className="flex flex-col">
                <span className="font-semibold text-ink text-[0.9375rem]">字号与行距</span>
                <span className="text-xs text-muted">影响英文正文显示</span>
              </div>
              <select className="bg-web-canvas border border-hairline rounded-md px-3 py-1.5 text-sm outline-none">
                <option value="standard">标准 (默认)</option>
                <option value="large">偏大</option>
                <option value="compact">紧凑</option>
              </select>
            </div>
            <div className="p-5 flex justify-between items-center">
              <div className="flex flex-col">
                <span className="font-semibold text-ink text-[0.9375rem]">默认翻译显示</span>
                <span className="text-xs text-muted">进入 Reader 时译文的可见性</span>
              </div>
              <select className="bg-web-canvas border border-hairline rounded-md px-3 py-1.5 text-sm outline-none">
                <option value="muted">柔和 (默认)</option>
                <option value="standard">清晰</option>
                <option value="hidden">隐藏</option>
              </select>
            </div>
          </div>
        </section>

        <section className="pt-4">
          <LogoutButton />
        </section>
      </div>
    </main>
  );
}
