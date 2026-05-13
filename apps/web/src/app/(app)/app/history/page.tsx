import Link from "next/link";
import { getRecordList, type RecordsDataSource } from "@/services/bff/records";

const goalLabel: Record<string, string> = {
  academic: "学术摘要",
  daily_reading: "日常阅读",
  exam: "备考精读",
};

const dataSourceLabel: Record<RecordsDataSource, string> = {
  upstream: "已连接云端记录",
  "mock-fallback": "当前显示示例记录",
};

export default async function HistoryPage() {
  const { records, dataSource } = await getRecordList({ limit: 50 });

  return (
    <main className="flex-1 flex justify-center py-10 px-6">
      <div className="w-full max-w-3xl flex flex-col gap-8">
        <header className="flex items-start justify-between border-b border-hairline pb-4 gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-[1.75rem] font-headline font-semibold text-ink">
              历史记录
            </h1>
            <p className="text-[0.75rem] text-muted">
              {dataSourceLabel[dataSource]}
            </p>
          </div>
          <Link href="/app" className="rounded-pill bg-surface border border-hairline px-4 py-2 text-[0.8125rem] font-semibold text-ink hover:border-muted transition-colors">
            新解析
          </Link>
        </header>

        <section className="flex flex-col gap-4">
          {records.map((record) => (
            <Link
              key={record.id}
              href={`/app/reader/${record.id}`}
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
          ))}
        </section>
      </div>
    </main>
  );
}
