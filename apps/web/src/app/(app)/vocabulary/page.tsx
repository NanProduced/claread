import { getVocabularyList, type VocabularyBffStatus } from "@/services/bff/vocabulary";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

function statusLabel(status: VocabularyBffStatus): string {
  switch (status) {
    case "ready":
      return "已同步";
    case "unauthenticated":
      return "未登录";
    case "mock_session":
      return "本地 mock 登录";
    case "upstream_unavailable":
      return "服务暂不可用";
    case "upstream_error":
      return "读取失败";
  }
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function VocabularyRow({ item }: { item: VocabularyItemVm }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between p-5 border-b border-hairline last:border-b-0 hover:bg-surface-warm transition-colors gap-3">
      <div className="flex flex-col gap-1 sm:w-1/3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-display font-semibold text-[1.125rem] text-ink">
            {item.word}
          </span>
          {item.phonetic ? (
            <span className="text-[0.75rem] text-muted">{item.phonetic}</span>
          ) : null}
        </div>
        <span className="text-[0.75rem] text-muted">{formatDate(item.createdAt)} 加入</span>
      </div>
      <div className="flex-1 text-[0.9375rem] text-ink-soft">
        <div>{item.shortMeaning ?? item.contextSentence ?? "暂无释义"}</div>
        {item.contextSentence ? (
          <div className="mt-1 text-[0.8125rem] text-muted line-clamp-2">
            {item.contextSentence}
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-2 self-end sm:self-center">
        {item.partOfSpeech ? (
          <span className="rounded-pill bg-surface border border-hairline px-2 py-1 text-[0.75rem] text-muted">
            {item.partOfSpeech}
          </span>
        ) : null}
        <span className="rounded-pill bg-surface border border-hairline px-2 py-1 text-[0.75rem] text-muted">
          {item.mastered ? "已掌握" : "待复习"}
        </span>
      </div>
    </div>
  );
}

function EmptyState({
  status,
  message,
}: {
  status: VocabularyBffStatus;
  message?: string;
}) {
  const title = status === "ready" ? "还没有真实生词" : statusLabel(status);

  return (
    <div className="px-6 py-10 text-center">
      <h2 className="text-base font-semibold text-ink">{title}</h2>
      <p className="mt-2 text-sm text-muted">
        {message ?? "完成阅读解析并收藏词汇后，这里会显示你的真实生词数据。"}
      </p>
    </div>
  );
}

export default async function VocabularyPage() {
  const vocabulary = await getVocabularyList();
  const hasItems = vocabulary.items.length > 0;

  return (
    <main className="flex-1 flex justify-center py-10 px-6">
      <div className="w-full max-w-3xl flex flex-col gap-8">
        <header className="flex items-center justify-between border-b border-hairline pb-4">
          <div>
            <h1 className="text-[1.75rem] font-headline font-semibold text-ink">
              生词本
            </h1>
            <p className="text-sm text-muted mt-1">
              共收录 {vocabulary.total} 个单词 · {statusLabel(vocabulary.status)}
            </p>
            {vocabulary.message ? (
              <p className="text-[0.8125rem] text-muted mt-2 max-w-2xl">{vocabulary.message}</p>
            ) : null}
          </div>
          <button
            className="rounded-pill bg-surface border border-hairline px-4 py-2 text-[0.8125rem] font-semibold text-ink hover:border-muted transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!hasItems}
          >
            开始复习
          </button>
        </header>

        <section className="bg-surface rounded-note border border-hairline shadow-surface-quiet overflow-hidden">
          {hasItems ? (
            <div className="flex flex-col">
              {vocabulary.items.map((item) => (
                <VocabularyRow key={item.id} item={item} />
              ))}
            </div>
          ) : (
            <EmptyState status={vocabulary.status} message={vocabulary.message} />
          )}
        </section>
      </div>
    </main>
  );
}
