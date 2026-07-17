import { ArrowRight } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";
import { appSettingsRoute } from "@/lib/routes";
import type { QuotaVm } from "@/types/view/QuotaVm";
import { CreditLedgerPanel } from "../CreditLedgerPanel";

interface UsageSectionProps {
  quota: QuotaVm | null;
  quotaUsed: number;
  quotaLimit: number;
  quotaPercentage: number;
  showLedger?: boolean;
}

export function UsageSection({
  quota,
  quotaUsed,
  quotaLimit,
  quotaPercentage,
  showLedger = false,
}: UsageSectionProps) {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-medium text-muted-foreground mb-2">今日解析点数</p>
        <div className="flex items-baseline gap-2">
          <span className="font-display text-[3.5rem] leading-none tracking-tight text-ink">
            {quota ? quotaUsed : "--"}
          </span>
          <span className="text-lg font-medium text-muted-foreground">
            / {quota ? quotaLimit : "--"}
          </span>
        </div>

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
          <span className="text-muted-foreground">
            剩余可用 <strong className="font-semibold text-ink">{quota.remainingPoints ?? 0}</strong>
          </span>
          <span className="hidden sm:inline text-hairline">|</span>
          <span className="text-muted-foreground">
            额外奖励 <strong className="font-semibold text-ink">{quota.bonusPoints ?? 0}</strong>
          </span>
        </div>
      )}

      {quota && (
        <div className="pt-2">
          <Link
            href={`${appSettingsRoute}/ledger` as Route}
            className="group inline-flex items-center gap-1.5 text-sm font-semibold text-lens-blue transition-colors hover:text-lens-blue-dark"
          >
            <span>查看明细账单</span>
            <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
          </Link>
        </div>
      )}

      {showLedger ? <CreditLedgerPanel /> : null}
    </div>
  );
}
