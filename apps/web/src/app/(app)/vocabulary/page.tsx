import { Play, Search } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { InfoCard, PageHeader, StatCard } from "@/components/composed";
import { getVocabularyList, type VocabularyBffStatus } from "@/services/bff/vocabulary";
import { VocabularyClient } from "./VocabularyClient";

const reviewRoute = "/review" as Route;

function statusLabel(status: VocabularyBffStatus): string {
  switch (status) {
    case "ready":
      return "已同步";
    case "unauthenticated":
      return "会话已过期";
    case "mock_session":
      return "会话不可用";
    case "upstream_unavailable":
      return "服务暂不可用";
    case "upstream_error":
      return "读取失败";
  }
}

export default async function VocabularyPage() {
  const vocabulary = await getVocabularyList();
  const learningCount = vocabulary.items.filter((item) => !item.mastered).length;
  const masteredCount = vocabulary.items.length - learningCount;
  const canReview = learningCount > 0;

  return (
    <main className="paper-grain min-h-screen px-5 py-7 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto grid max-w-7xl gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0">
          <PageHeader
            eyebrow="词汇资产"
            title="生词本"
            description="只保存你主动加入的原词、上下文和来源文章。词典保持轻量，给后续 AI 词汇增强留下干净数据。"
            message={vocabulary.message}
            actions={
              canReview ? (
                <Button asChild variant="primary">
                  <Link href={reviewRoute}>
                    <Play aria-hidden="true" className="h-4 w-4 fill-current" />
                    开始复习 {Math.min(learningCount, 20)} 个
                  </Link>
                </Button>
              ) : (
                <Button variant="outline" disabled>
                  暂无待复习
                </Button>
              )
            }
          />

          <VocabularyClient items={vocabulary.items} status={vocabulary.status} />
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <StatCard
            title="生词状态"
            items={[
              { label: "学习中", value: learningCount },
              { label: "已掌握", value: masteredCount },
              { label: "同步", value: statusLabel(vocabulary.status) },
            ]}
            className="[&_dl>div:last-child]:col-span-2"
          />

          <InfoCard
            title="查询历史不保存"
            icon={Search}
            description="点词查询只服务当前阅读。进入生词本的，是用户明确保存过的词和上下文。"
            tone="paper"
          />
        </aside>
      </div>
    </main>
  );
}
