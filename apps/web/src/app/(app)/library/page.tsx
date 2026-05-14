import Link from "next/link";
import type { Route } from "next";
import { getRecordList, type RecordsBffStatus } from "@/services/bff/records";

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
            <Link
              key={record.id}
              href={readerRoute(record.id)}
              className="group p-5 bg-surface rounded-note border border-hairline shadow-surface-quiet hover:border-muted transition-colors flex flex-col md:flex-row justify-between md:items-center gap-4"
            >
              <div className="flex flex-col gap-2 flex-1">
                <h2 className="font-headline font-semibold text-[1.25rem] text-ink line-clamp-2 leading-snug">
                  {record.title}
                </h2>
                <div className="flex items-center gap-3 text-[0.8125rem] text-muted">
                  <span>{new Date(record.createdAt).toLocaleDateString("zh-CN")}</span>
                  <span className="w-1 h-1 rounded-full bg-hairline"></span>
                  <span>{record.wordCount} words</span>
                  <span className="w-1 h-1 rounded-full bg-hairline"></span>
                  <span className="px-2 py-0.5 bg-surface-warm rounded-sm border border-hairline text-subtle">{goalLabel[record.readingGoal] ?? record.readingGoal}</span>
                </div>
              </div>
              <div className="text-muted group-hover:text-lens-blue transition-colors self-end md:self-center">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12h14"></path>
                  <path d="m12 5 7 7-7 7"></path>
                </svg>
              </div>
            </Link>
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
