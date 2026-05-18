import { Plus, Search } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { InfoCard, PageHeader, StatCard, TopActionBar } from "@/components/composed";
import { getRecordList, type RecordsBffStatus } from "@/services/bff/records";
import { LibraryClient } from "./LibraryClient";

const readRoute = "/read" as Route;
const assetsRoute = "/library/assets" as Route;

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
          <PageHeader
            eyebrow="阅读档案"
            title="阅读记录"
            description="回到读过的文章，继续阅读、找回收藏和批注。第一版先做标题与原文片段搜索。"
            message={result.message}
            actions={
              <TopActionBar>
                <Button asChild variant="primary">
                  <Link href={readRoute}>
                    <Plus aria-hidden="true" className="h-4 w-4" />
                    新解读
                  </Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href={assetsRoute}>学习资产</Link>
                </Button>
              </TopActionBar>
            }
          />

          <LibraryClient records={result.records} status={result.status} />
        </section>

        <aside className="space-y-5 xl:pt-[7.4rem]">
          <StatCard
            title="档案状态"
            items={[
              { label: "总记录", value: result.total },
              { label: "同步", value: statusLabel[result.status] },
            ]}
          />

          <InfoCard
            title="搜索范围"
            icon={Search}
            description="当前只在已加载记录的标题和原文片段中查找。后续语义搜索归入后端能力评审。"
            tone="paper"
          />
        </aside>
      </div>
    </main>
  );
}
