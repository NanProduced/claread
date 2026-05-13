import { mockVocabularyList } from "@/lib/mock-data";

export default function VocabularyPage() {
  return (
    <main className="flex-1 flex justify-center py-10 px-6">
      <div className="w-full max-w-3xl flex flex-col gap-8">
        <header className="flex items-center justify-between border-b border-hairline pb-4">
          <div>
            <h1 className="text-[1.75rem] font-headline font-semibold text-ink">
              生词本
            </h1>
            <p className="text-sm text-muted mt-1">共收录 {mockVocabularyList.length} 个单词</p>
          </div>
          <button className="rounded-pill bg-surface border border-hairline px-4 py-2 text-[0.8125rem] font-semibold text-ink hover:border-muted transition-colors">
            开始复习
          </button>
        </header>

        <section className="bg-surface rounded-note border border-hairline shadow-surface-quiet overflow-hidden">
          <div className="flex flex-col">
            {mockVocabularyList.map((item) => (
              <div
                key={item.id}
                className="flex flex-col sm:flex-row sm:items-center justify-between p-5 border-b border-hairline last:border-b-0 hover:bg-surface-warm transition-colors gap-2"
              >
                <div className="flex flex-col gap-1 sm:w-1/3">
                  <span className="font-display font-semibold text-[1.125rem] text-ink">{item.word}</span>
                  <span className="text-[0.75rem] text-muted">{new Date(item.createdAt).toLocaleDateString("zh-CN")} 加入</span>
                </div>
                <div className="flex-1 text-[0.9375rem] text-ink-soft">
                  {item.contextTranslation ?? item.contextSentence ?? item.partOfSpeech ?? "暂无语境"}
                </div>
                <div className="text-muted hover:text-ink cursor-pointer pl-4 self-end sm:self-center">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 5v14"></path>
                    <path d="M5 12h14"></path>
                  </svg>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
