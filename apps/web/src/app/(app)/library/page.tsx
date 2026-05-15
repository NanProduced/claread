import { Plus, Search } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { getRecordList, type RecordsBffStatus } from "@/services/bff/records";
import { LibraryClient } from "./LibraryClient";

const readRoute = "/read" as Route;

const statusLabel: Record<RecordsBffStatus, string> = {
  ready: "已同步",
  unauthenticated: "会话已过期",
  mock_session: "会话不可用",
  upstream_unavailable: "服务暂不可用",
  upstream_error: "读取失败",
};

export default async function HistoryPage() {
  const result = await getRecordList({ limit: 100 });

  return (
    <main className="paper-grain min-h-screen px-5 py-7 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto grid max-w-7xl gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0">
          <header className="mb-7 flex flex-col gap-5 border-b border-hairline pb-6 md:flex-row md:items-end md:justify-between">
            <div className="max-w-2xl">
              <p className="mb-3 text-xs font-semibold text-muted">阅读档案</p>
              <h1 className="font-headline text-[2.15rem] font-semibold leading-tight tracking-normal text-ink sm:text-[2.65rem]">
                阅读记录
              </h1>
              <p className="mt-3 text-sm leading-6 text-muted">
                回到读过的文章，继续阅读、找回收藏和批注。第一版先做标题与原文片段搜索。
              </p>
              {result.message ? (
                <p className="mt-3 text-sm leading-6 text-muted">{result.message}</p>
              ) : null}
            </div>
            <Link
              href={readRoute}
              className="focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-pill bg-lens-blue px-5 text-sm font-semibold text-surface transition-opacity hover:opacity-90"
            >
              <Plus aria-hidden="true" className="h-4 w-4" />
              新解读
            </Link>
          </header>

          <LibraryClient records={result.records} status={result.status} />
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <section className="rounded-panel border border-hairline bg-surface p-5 shadow-surface-quiet">
            <h2 className="text-sm font-semibold text-ink">档案状态</h2>
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              <div>
                <dt className="text-xs text-muted">总记录</dt>
                <dd className="mt-1 font-semibold text-ink">{result.total}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">同步</dt>
                <dd className="mt-1 font-semibold text-ink">{statusLabel[result.status]}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-panel border border-hairline bg-reader-paper p-5">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Search aria-hidden="true" className="h-4 w-4 text-lens-blue" />
              搜索范围
            </h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              当前只在已加载记录的标题和原文片段中查找。后续语义搜索归入后端能力评审。
            </p>
          </section>
        </aside>
      </div>
    </main>
  );
}
