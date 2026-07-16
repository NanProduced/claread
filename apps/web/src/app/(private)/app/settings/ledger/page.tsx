import { ChevronRight, HelpCircle } from "lucide-react";
import Link from "next/link";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import { appSettingsRoute } from "@/lib/routes";
import { getProfileSettings } from "@/services/bff/profile";
import type { QuotaVm } from "@/types/view/QuotaVm";
import { CreditLedgerPanel } from "../CreditLedgerPanel";

function formatPoints(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return new Intl.NumberFormat("zh-CN").format(value);
}

function CreditStatement({ quota }: { quota: QuotaVm | null }) {
  const dailyFree = quota?.dailyFreePoints ?? quota?.quotaLimit ?? null;
  const dailyUsed = quota?.dailyUsedPoints ?? quota?.quotaUsed ?? null;
  const dailyRemaining =
    typeof dailyFree === "number" && typeof dailyUsed === "number"
      ? Math.max(0, dailyFree - dailyUsed)
      : null;
  const bonusPoints = quota?.bonusPoints ?? null;
  const remainingPoints = quota?.remainingPoints ?? null;
  const usedPercent =
    typeof dailyFree === "number" && dailyFree > 0 && typeof dailyUsed === "number"
      ? Math.min(100, Math.max(0, (dailyUsed / dailyFree) * 100))
      : 0;

  const stats = [
    { label: "当前余额", value: formatPoints(remainingPoints) },
    { label: "今日免费剩余", value: formatPoints(dailyRemaining) },
    { label: "奖励点数", value: formatPoints(bonusPoints) },
  ];

  return (
    <section aria-label="Credit statement summary" className="mb-8 rounded-panel border border-hairline bg-surface/40 px-4 py-3.5 shadow-[var(--cl-shadow-1)] sm:px-5">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-ink">积分账户</p>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label="积分规则说明"
                    className="focus-ring inline-flex size-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-ink"
                  >
                    <HelpCircle className="size-4" aria-hidden="true" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  <p>每日免费点数优先消耗；失败的 Ask Claread 或 AI 词典请求会自动退回未使用点数。</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <Link href={appSettingsRoute} className="focus-ring rounded-control-md text-sm font-semibold text-lens-blue transition-colors hover:text-ink hover:underline">
            回到设置
          </Link>
        </div>

        <div className="grid gap-px overflow-hidden rounded-note border border-hairline bg-hairline sm:grid-cols-3">
          {stats.map((item) => (
            <div key={item.label} className="bg-surface px-4 py-3">
              <p className="text-xs font-semibold text-muted-foreground">{item.label}</p>
              <p className="mt-1.5 font-headline text-2xl font-semibold leading-none text-ink">{item.value}</p>
            </div>
          ))}
        </div>

        {typeof dailyFree === "number" && dailyFree > 0 ? (
          <div>
            <div className="flex items-center justify-between gap-3 text-xs font-medium text-muted-foreground">
              <span>每日免费点数使用进度</span>
              <span>{formatPoints(dailyUsed)} / {formatPoints(dailyFree)} · {Math.round(usedPercent)}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-hairline" aria-hidden="true">
              <div className="h-full rounded-full bg-lens-blue transition-[width] duration-300 ease-out-expo" style={{ width: `${usedPercent}%` }} />
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default async function LedgerPage() {
  const settings = await getProfileSettings();

  return (
    <main className="flex h-dvh flex-col overflow-y-auto bg-surface-canvas px-4 py-8 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex w-full max-w-[920px] flex-col pb-24 pt-4 lg:pt-12">
        <div className="mb-8 border-b border-hairline pb-8">
          <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-2 text-[0.68rem] font-bold tracking-[0.15em] text-muted-foreground">
            <Link href={appSettingsRoute} className="hover:text-ink hover:underline underline-offset-4 decoration-hairline transition-colors">
              Preferences
            </Link>
            <ChevronRight className="h-3 w-3 text-hairline" />
            <span className="text-ink">Credit Ledger</span>
          </nav>
          <h1 className="font-headline text-[2.5rem] font-semibold leading-[1] tracking-tight text-ink md:text-[3.2rem]">
            Credit Ledger.
          </h1>
          <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground">
            积分明细用于核对每一次阅读分析、Ask Claread 能力调用、奖励到账与积分退回。
          </p>
        </div>

        <CreditStatement quota={settings.quota} />

        <div className="min-h-0" aria-label="Credit ledger entries">
          <CreditLedgerPanel />
        </div>
      </div>
    </main>
  );
}
