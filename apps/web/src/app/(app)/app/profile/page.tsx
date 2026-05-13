import { mockQuota } from "@/lib/mock-data";

export default function ProfilePage() {
  const quotaPercent = Math.round((mockQuota.quotaUsed / mockQuota.quotaLimit) * 100);

  return (
    <main className="flex-1 flex justify-center py-10 px-6">
      <div className="w-full max-w-2xl flex flex-col gap-8">
        <header className="border-b border-hairline pb-4">
          <h1 className="text-[1.75rem] font-headline font-semibold text-ink">
            账户与设置
          </h1>
        </header>

        {/* User Info & Quota */}
        <section className="flex flex-col gap-4">
          <h2 className="text-[1.125rem] font-title font-semibold text-ink">订阅配额</h2>
          <div className="bg-surface rounded-note border border-hairline shadow-surface-quiet p-6 flex flex-col gap-6">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-lens-blue-soft text-lens-blue flex items-center justify-center font-display font-bold text-xl">
                U
              </div>
              <div className="flex flex-col">
                <span className="font-semibold text-ink">Web User</span>
                <span className="text-sm text-muted">手机号登录待接入</span>
              </div>
            </div>
            
            <div className="border-t border-hairline pt-5">
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm font-semibold text-ink">当月解析额度</span>
                <span className="text-sm text-muted">{mockQuota.quotaUsed} / {mockQuota.quotaLimit} 篇</span>
              </div>
              <div className="w-full h-2 bg-web-canvas rounded-full overflow-hidden">
                <div className="h-full bg-lens-blue rounded-full" style={{ width: `${quotaPercent}%` }}></div>
              </div>
              <p className="text-[0.75rem] text-muted mt-3">额度将在 {new Date(mockQuota.resetAt ?? "").toLocaleDateString("zh-CN")} 重置</p>
            </div>
            
            <button className="w-full py-2.5 bg-ink text-surface rounded-pill text-[0.8125rem] font-semibold mt-2">
              升级订阅
            </button>
          </div>
        </section>

        {/* Reader Settings */}
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

        {/* Actions */}
        <section className="pt-4">
          <button className="text-error-red text-sm font-semibold hover:opacity-80">
            退出登录
          </button>
        </section>
      </div>
    </main>
  );
}
