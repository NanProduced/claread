import { ArrowLeft, BookOpen } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

const examples: Record<string, { label: string; title: string; body: string }> = {
  "news-brief": {
    label: "新闻示例",
    title: "A short piece of global news",
    body: "The talks resumed after weeks of uncertainty, with officials describing the outcome as cautious but constructive.",
  },
  "academic-abstract": {
    label: "学术示例",
    title: "An abstract with dense logic",
    body: "This paper argues that institutional memory shapes policy choices by narrowing the range of acceptable interpretations.",
  },
  "exam-passage": {
    label: "考试示例",
    title: "A passage with trap options",
    body: "The author is less interested in defending the invention than in questioning the assumptions that made it seem inevitable.",
  },
};

export function generateStaticParams() {
  return Object.keys(examples).map((slug) => ({ slug }));
}

export default async function ExamplePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const example = examples[slug] ?? examples["news-brief"];

  return (
    <main className="min-h-screen bg-[oklch(97%_0.012_84)] px-5 py-6 text-ink sm:px-8 lg:px-12">
      <div className="mx-auto max-w-5xl">
        <header className="flex items-center justify-between gap-6">
          <Link href="/login" className="focus-ring inline-flex items-center gap-2 rounded-pill text-sm font-semibold text-muted hover:text-ink">
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            返回登录页
          </Link>
          <Image
            src="/brand/claread-horizontal-bilingual.png"
            alt="Claread 透读"
            width={260}
            height={76}
            priority
            className="h-auto w-40"
          />
        </header>

        <article className="reading-paper mt-8 rounded-[2rem] border border-hairline px-6 py-8 shadow-[0_28px_80px_rgba(35,28,18,0.12)] sm:px-12 sm:py-12">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-lens-blue">
            {example.label}
          </p>
          <h1 className="mt-5 max-w-3xl font-headline text-[2.5rem] font-semibold leading-tight tracking-normal text-ink sm:text-[3.4rem]">
            {example.title}
          </h1>
          <div className="mt-10 max-w-[68ch] font-reading text-[1.28rem] leading-[1.95] text-ink">
            <p>
              {example.body.split(" ").slice(0, 5).join(" ")}{" "}
              <span className="rounded-sm bg-lens-blue-soft px-1.5 py-0.5 text-[#174ea6]">
                {example.body.split(" ")[5]}
              </span>{" "}
              {example.body.split(" ").slice(6).join(" ")}
            </p>
          </div>
          <section className="mt-10 grid gap-4 border-t border-hairline pt-6 md:grid-cols-2">
            <div>
              <h2 className="text-sm font-semibold text-ink">标注预览</h2>
              <p className="mt-2 text-sm leading-6 text-muted">
                公开示例只展示 Claread 的解读形态，不消耗模型额度。
              </p>
            </div>
            <div>
              <h2 className="text-sm font-semibold text-ink">继续使用</h2>
              <Link
                href="/login?next=/read"
                className="focus-ring mt-2 inline-flex min-h-10 items-center gap-2 rounded-pill bg-lens-blue px-4 text-sm font-semibold text-surface"
              >
                <BookOpen aria-hidden="true" className="h-4 w-4" />
                解读我的文章
              </Link>
            </div>
          </section>
        </article>
      </div>
    </main>
  );
}
