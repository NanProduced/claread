import type { ReactNode } from "react";

export type LegalDocumentSection = {
  id: string;
  title: string;
  content: ReactNode;
};

type LegalDocumentPageProps = {
  title: string;
  summary: string;
  header: ReactNode;
  sections: readonly LegalDocumentSection[];
};

export function LegalDocumentPage({
  title,
  summary,
  header,
  sections,
}: LegalDocumentPageProps) {
  return (
    <main className="min-h-screen overflow-x-clip bg-surface text-ink">
      <div className="border-b border-hairline">
        <div className="px-5 sm:px-8 lg:px-12">{header}</div>
      </div>

      <div className="mx-auto w-full max-w-[52rem] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
        <article
          id="legal-document"
          aria-labelledby="legal-document-title"
          className="min-w-0"
        >
          <div
            role="note"
            className="flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-hairline py-3 font-sans text-sm text-ink-soft"
          >
            <span className="font-semibold text-ink">测试期草案</span>
            <span aria-hidden="true" className="text-subtle">
              ·
            </span>
            <span>非最终法律意见</span>
          </div>

          <header className="pt-10">
            <h1
              id="legal-document-title"
              className="font-reading text-4xl font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-5xl"
            >
              {title}
            </h1>
            <p className="mt-5 font-reading text-lg leading-8 text-ink-soft">{summary}</p>
            <dl className="mt-8 grid gap-4 border-y border-hairline py-5 font-sans text-sm sm:grid-cols-2">
              <div>
                <dt className="text-subtle">版本</dt>
                <dd className="mt-1 font-medium text-ink">v0.1（测试期草案）</dd>
              </div>
              <div>
                <dt className="text-subtle">更新日期</dt>
                <dd className="mt-1 font-medium text-ink">
                  <time dateTime="2026-09-02">2026 年 9 月 2 日</time>
                </dd>
              </div>
            </dl>
          </header>

          <nav
            aria-label="文档目录"
            className="mt-10 border-b border-hairline pb-6 font-sans"
          >
            <h2 className="text-sm font-semibold text-ink">目录</h2>
            <ul className="mt-3 grid gap-x-8 gap-y-1 sm:grid-cols-2">
              {sections.map((section) => (
                <li key={section.id} className="min-w-0">
                  <a
                    href={`#${section.id}`}
                    className="focus-ring inline-flex min-h-11 max-w-full items-center rounded-sm text-sm text-ink-soft underline decoration-hairline underline-offset-4 transition-colors hover:text-ink"
                  >
                    <span className="break-words">{section.title}</span>
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          <div className="mt-12 space-y-10 font-reading text-base leading-8 text-ink-soft">
            {sections.map((section) => (
              <section
                key={section.id}
                id={section.id}
                aria-labelledby={`${section.id}-heading`}
                tabIndex={-1}
                className="scroll-mt-24 border-t border-hairline pt-8 first:border-t-0 first:pt-0"
              >
                <h2
                  id={`${section.id}-heading`}
                  className="font-sans text-xl font-semibold leading-8 text-ink"
                >
                  {section.title}
                </h2>
                <div className="mt-4 min-w-0 space-y-4 break-words">{section.content}</div>
              </section>
            ))}
          </div>
        </article>
      </div>
    </main>
  );
}
