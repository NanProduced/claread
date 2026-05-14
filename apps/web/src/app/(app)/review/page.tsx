import Link from "next/link";
import type { Route } from "next";

import { getReviewQueue } from "@/services/bff/review";
import type { ReviewQueueVm } from "@/types/view/ReviewQueueVm";

import { ReviewQueueClient } from "./ReviewQueueClient";

const loginRoute = "/login" as Route;
const readRoute = "/read" as Route;
const reviewLimit = 20;

const stateTitle: Record<ReviewQueueVm["state"], string> = {
  ready: "待复习",
  empty: "暂无待复习",
  anonymous: "需要登录",
  mock_session: "登录态不可用",
  upstream_unavailable: "复习服务不可用",
  error: "复习队列读取失败",
};

function statusPanel(queue: ReviewQueueVm) {
  if (queue.state === "ready") {
    return null;
  }

  const action =
    queue.state === "anonymous" ? (
      <Link
        href={loginRoute}
        className="rounded-pill border border-hairline bg-surface px-4 py-2 text-sm font-semibold text-ink transition-colors hover:border-muted"
      >
        去登录
      </Link>
    ) : queue.state === "empty" ? (
      <Link
        href={readRoute}
        className="rounded-pill border border-hairline bg-surface px-4 py-2 text-sm font-semibold text-ink transition-colors hover:border-muted"
      >
        解析新文章
      </Link>
    ) : null;

  return (
    <section className="rounded-note border border-hairline bg-surface p-8 shadow-surface-quiet">
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="font-headline text-xl font-semibold text-ink">
            {stateTitle[queue.state]}
          </h2>
          <p className="mt-2 text-sm leading-6 text-muted">
            {queue.message ?? "没有可展示的复习项目。"}
          </p>
        </div>
        {action}
      </div>
    </section>
  );
}

export default async function ReviewPage() {
  const queue = await getReviewQueue(reviewLimit);

  return (
    <main className="flex-1 px-6 py-10">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
        <header className="flex items-start justify-between gap-4 border-b border-hairline pb-4">
          <div>
            <h1 className="font-headline text-[1.75rem] font-semibold text-ink">
              复习
            </h1>
            <p className="mt-1 text-sm leading-6 text-muted">
              复习从真实生词本生成，按熟悉程度推进下一次复习时间。
            </p>
          </div>
          <div className="shrink-0 rounded-pill border border-hairline bg-surface px-3 py-1 text-xs font-semibold text-muted">
            {queue.total} / {queue.limit}
          </div>
        </header>

        {queue.state === "ready" ? (
          <ReviewQueueClient initialItems={queue.items} />
        ) : (
          statusPanel(queue)
        )}
      </div>
    </main>
  );
}
