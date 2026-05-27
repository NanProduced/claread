import { CheckCircle2, Plus } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { appReadRoute } from "@/lib/routes";
import { getRecordList, type RecordsBffStatus } from "@/services/bff/records";
import { LibraryClient } from "./LibraryClient";

const statusLabel: Record<RecordsBffStatus, string> = {
  ready: "已同步",
  unauthenticated: "会话已过期",
  limited_debug: "调试态受限",
  upstream_unavailable: "服务暂不可用",
  upstream_error: "读取失败",
};

export default async function HistoryPage() {
  const result = await getRecordList({ limit: 100 });

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-[oklch(96.8%_0.012_84)] px-4 py-4 text-ink sm:px-8 sm:py-6 lg:px-12 lg:py-8">
      <div className="mx-auto flex min-h-0 w-full max-w-[1400px] flex-1 flex-col">
        
        <div className="mb-8 shrink-0 flex flex-col sm:flex-row sm:items-start justify-between gap-6 pl-2 lg:mb-10">
          <div>
            <div className="mb-3 flex items-center gap-4 lg:mb-5">
              <p className="text-[0.65rem] font-bold uppercase tracking-[0.2em] text-lens-blue">Library</p>
              <div className="h-[1px] w-12 bg-hairline" />
            </div>
            <h1 className="font-headline text-[2.5rem] font-medium leading-[1.05] tracking-tight text-ink md:text-[3.5rem] lg:text-[4.5rem]">
              Reading<br />Archive.
            </h1>
            <p className="mt-4 max-w-xl text-[0.95rem] leading-relaxed text-muted lg:mt-6">
              回顾并继续你过去的阅读与标注。所有的精读记录都在此为你保留。
            </p>
          </div>
          <div className="pt-2">
            <Button asChild variant="primary" className="rounded-pill bg-ink text-surface hover:bg-ink-soft border-transparent shadow-xl min-w-[120px]">
              <Link href={appReadRoute}>
                <Plus aria-hidden="true" className="h-4 w-4" />
                新解读
              </Link>
            </Button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-8 lg:gap-12 lg:grid-cols-[minmax(0,1fr)_280px] xl:gap-16 xl:grid-cols-[minmax(0,1.8fr)_340px]">
          <div className="flex min-h-0 min-w-0 flex-col">
            <LibraryClient records={result.records} status={result.status} />
          </div>

          <aside className="hidden min-w-0 space-y-12 overflow-y-auto pb-8 pr-2 lg:block lg:pr-4 xl:pt-[2rem]">
            {/* OVERVIEW */}
            <div>
              <h3 className="mb-6 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-ink">Overview</h3>
              <div className="space-y-8">
                <div>
                  <p className="text-[0.65rem] font-bold uppercase tracking-[0.15em] text-muted">Articles Read</p>
                  <p className="mt-2 font-headline text-[2.5rem] font-semibold leading-none text-ink tracking-tight">{result.total}</p>
                </div>
                <div>
                  <p className="text-[0.65rem] font-bold uppercase tracking-[0.15em] text-muted">Archive Status</p>
                  <p className="mt-2 font-headline text-[1.8rem] font-semibold leading-none text-[#F5A623] tracking-tight">
                    {statusLabel[result.status]}
                  </p>
                </div>
                {result.status === "ready" && (
                  <div className="flex items-center gap-2 text-[0.75rem] font-medium text-emerald-600">
                    <CheckCircle2 className="h-[14px] w-[14px]" />
                    Synced to cloud
                  </div>
                )}
              </div>
            </div>

            {/* SEARCH TIPS */}
            <div>
              <h3 className="mb-6 text-[0.65rem] font-bold uppercase tracking-[0.2em] text-ink">Search Tips</h3>
              <p className="text-[0.8rem] leading-relaxed text-muted">
                当前支持通过文章标题和原文片段搜索。你的个人笔记和生词释义可在各自的模块中独立检索。
              </p>
            </div>
          </aside>
        </div>
      </div>
    </main>
  );
}
