import Link from "next/link";
import type { Route } from "next";
import { getRecordList, type RecordsBffStatus } from "@/services/bff/records";
import { DeleteRecordButton } from "./DeleteRecordButton";

const readRoute = "/read" as Route;

function readerRoute(recordId: string): Route {
  return `/reader/${recordId}` as Route;
}

const goalLabel: Record<string, string> = {
  academic: "学术摘要",
  daily_reading: "日常阅读",
  exam: "备考精读",
};

const statusLabel: Record<RecordsBffStatus, string> = {
  ready: "已同步",
  unauthenticated: "未登录",
  mock_session: "登录态不可用",
  upstream_unavailable: "服务暂不可用",
  upstream_error: "读取失败",
};

export default async function HistoryPage() {
  const { records, status, message, total } = await getRecordList({ limit: 50 });
  const hasRecords = records.length > 0;

  return (
    <main className="flex-1 flex justify-center py-10 px-6">
      <div className="w-full max-w-3xl flex flex-col gap-8">
        <header className="flex items-start justify-between border-b border-hairline pb-4 gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-[1.75rem] font-headline font-semibold text-ink">
              历史记录
            </h1>
            <p className="text-[0.75rem] text-muted">
              {total} 条记录 · {statusLabel[status]}
            </p>
            {message ? <p className="mt-2 max-w-2xl text-[0.8125rem] text-muted">{message}</p> : null}
          </div>
          <Link href={readRoute} className="rounded-pill bg-surface border border-hairline px-4 py-2 text-[0.8125rem] font-semibold text-ink hover:border-muted transition-colors">
            新解析
          </Link>
        </header>

        <section className="flex flex-col gap-4">
          {hasRecords ? records.map((record) => (
            <article
              key={record.id}
              className="group flex flex-col gap-4 rounded-note border border-hairline bg-surface p-5 shadow-surface-quiet transition-colors hover:border-muted md:flex-row md:items-center md:justify-between"
            >
              <Link href={readerRoute(record.id)} className="flex min-w-0 flex-1 flex-col gap-2">
                <h2 className="line-clamp-2 font-headline text-[1.25rem] font-semibold leading-snug text-ink">
                  {record.title}
                </h2>
                <div className="flex flex-wrap items-center gap-3 text-[0.8125rem] text-muted">
                  <span>{new Date(record.createdAt).toLocaleDateString("zh-CN")}</span>
                  <span className="h-1 w-1 rounded-full bg-hairline"></span>
                  <span>{record.wordCount} words</span>
                  <span className="h-1 w-1 rounded-full bg-hairline"></span>
                  <span className="rounded-sm border border-hairline bg-surface-warm px-2 py-0.5 text-subtle">
                    {goalLabel[record.readingGoal] ?? record.readingGoal}
                  </span>
                </div>
              </Link>
              <div className="flex items-start gap-3 self-end md:self-center">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12h14"></path>
                  <path d="m12 5 7 7-7 7"></path>
                </svg>
                <DeleteRecordButton recordId={record.id} title={record.title} />
              </div>
            </article>
          )) : (
            <div className="rounded-note border border-hairline bg-surface p-8 text-center shadow-surface-quiet">
              <h2 className="font-headline text-xl font-semibold text-ink">
                {status === "ready" ? "还没有阅读记录" : statusLabel[status]}
              </h2>
              <p className="mt-2 text-sm leading-6 text-muted">
                {message ?? "提交一次真实解析后，这里会显示你的阅读记录。"}
              </p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
