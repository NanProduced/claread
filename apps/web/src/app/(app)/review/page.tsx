import Link from "next/link";
import type { Route } from "next";
import { FileText } from "lucide-react";

import { getReviewQueue } from "@/services/bff/review";
import { Button } from "@/components/primitives/button";
import { EmptyState, PageHeader } from "@/components/composed";
import type { ReviewQueueVm } from "@/types/view/ReviewQueueVm";

import { ReviewQueueClient } from "./ReviewQueueClient";

const loginRoute = "/login" as Route;
const readRoute = "/read" as Route;
const reviewLimit = 20;

const stateTitle: Record<ReviewQueueVm["state"], string> = {
  ready: "待复习",
  empty: "暂无待复习",
  anonymous: "会话已过期",
  mock_session: "会话不可用",
  upstream_unavailable: "复习服务不可用",
  error: "复习队列读取失败",
};

function statusPanel(queue: ReviewQueueVm) {
  if (queue.state === "ready") {
    return null;
  }

  const action =
    queue.state === "anonymous" ? (
      <Button asChild variant="outline">
        <Link href={loginRoute}>重新登录</Link>
      </Button>
    ) : queue.state === "empty" ? (
      <Button asChild variant="outline">
        <Link href={readRoute}>解析新文章</Link>
      </Button>
    ) : null;

  return (
    <EmptyState
      icon={FileText}
      title={stateTitle[queue.state]}
      description={queue.message ?? "没有可展示的复习项目。"}
      action={action}
      className="border-t-0 py-0"
    />
  );
}

export default async function ReviewPage() {
  const queue = await getReviewQueue(reviewLimit);

  return (
    <main className="paper-grain min-h-screen px-5 py-7 text-ink sm:px-8 lg:px-10">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-8">
        <PageHeader
          eyebrow="间隔复习"
          title="复习"
          description="复习从真实生词本生成，按熟悉程度推进下一次复习时间。"
          actions={
            <div className="shrink-0 rounded-pill border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(251,250,246,0.985))] px-3 py-1 text-xs font-semibold text-muted shadow-surface-quiet">
              {queue.total} / {queue.limit}
            </div>
          }
        />

        {queue.state === "ready" ? (
          <ReviewQueueClient initialItems={queue.items} />
        ) : (
          statusPanel(queue)
        )}
      </div>
    </main>
  );
}
