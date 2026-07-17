/**
 * Placeholder for the "用量与积分" settings section.
 *
 * The previous usage counters, progress bar, credit ledger, filters, and
 * "查看明细账单" link have been removed. The ledger route and panel source
 * code remain untouched; this component simply no longer renders them.
 */
export function UsageSection() {
  return (
    <div className="space-y-4">
      <p className="max-w-[65ch] text-sm leading-6 text-muted-foreground">
        用量与积分能力将随新的 Agentic orchestration 统一适配。
      </p>
      <p className="text-sm leading-6 text-ink">当前无需操作。</p>
    </div>
  );
}
