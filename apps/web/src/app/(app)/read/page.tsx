import Link from "next/link";
import type { Route } from "next";
import { getRecordList } from "@/services/bff/records";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";

const libraryRoute = "/library" as Route;

function readerRoute(recordId: string): Route {
  return `/reader/${recordId}` as Route;
}

export default async function PasteToReadPage() {
  const { records: recentRecords } = await getRecordList({ limit: 2 });

  return (
    <main className="flex-1 flex flex-col items-center pt-16 pb-12 px-6">
      <div className="w-full max-w-2xl flex flex-col gap-6">
        <header className="mb-4">
          <h1 className="text-[1.75rem] font-headline font-semibold text-ink mb-2">
            透读英文文章
          </h1>
          <p className="text-[0.9375rem] text-muted leading-relaxed">
            粘贴英文文章、论文摘要或新闻。Claread 将为你提供结构化解析、长难句拆解和词汇标注，生成一份可读的精读笔记。
          </p>
        </header>

        <AnalyzeSubmitForm />

        {/* Recent Records (Optional minimal list) */}
        <section className="mt-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[1.125rem] font-title font-semibold text-ink">最近记录</h2>
            <Link href={libraryRoute} className="text-[0.8125rem] font-semibold text-muted hover:text-ink">
              查看全部
            </Link>
          </div>
          <div className="flex flex-col gap-3">
            {recentRecords.map((record) => (
              <Link
                key={record.id}
                href={readerRoute(record.id)}
                className="group p-4 bg-surface rounded-md border border-hairline hover:border-muted transition-colors flex justify-between items-center"
              >
                <div className="flex flex-col gap-1">
                  <span className="font-headline font-semibold text-[1.125rem] text-ink line-clamp-1">{record.title}</span>
                  <span className="text-[0.8125rem] text-muted">{record.wordCount} words · {record.inlineMarkCount} 处标注</span>
                </div>
                <div className="text-muted group-hover:text-ink transition-colors">
                  →
                </div>
              </Link>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
