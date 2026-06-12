import { AlertCircle, ArrowRight, HelpCircle, Layers } from "lucide-react";

const painPoints = [
  {
    title: "词典困境",
    subtitle: "查了每一个词，合起来依然看不懂句意",
    problem: "传统的查词软件只提供孤立的词条解释，但英语长句中复杂的短语修饰、习惯搭配和语境义常常导致‘字面认识但句子不通’。",
    solution: "Claread 提供语境义和短语联合标注，帮你把词汇放回具体的语义环境中解释。",
    icon: HelpCircle,
  },
  {
    title: "翻译假象",
    subtitle: "一键变中文，其实你并没有读懂原文",
    problem: "整段整篇的划词翻译虽然快，但它代替你完成了阅读过程。大脑没有经历英文句法拆解的刺激，阅读能力便无法真正提升。",
    solution: "Claread 保留原文为主体，仅在你停顿的难句下方架设语法桥梁，引导你真正读懂它。",
    icon: AlertCircle,
  },
  {
    title: "总结碎片",
    subtitle: "对话框堆满冗余回复，打碎阅读心流",
    problem: "通用 AI 助手习惯以长篇大论的对话框回复你，或者脱离原文生成摘要。你需要频繁在对话框和文章之间切回，阅读节奏支离破碎。",
    solution: "Claread 采用行内旁注设计，所有注解、拆解和追问都像笔记一样锚定在具体句子下，用完即收，心流不断。",
    icon: Layers,
  },
];

export function ProductPainPoints() {
  return (
    <section className="relative overflow-hidden border-b border-hairline/80 bg-web-canvas px-5 pb-16 pt-12 sm:px-6 sm:pb-24 sm:pt-16 lg:px-8 lg:pb-28 lg:pt-20">
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(circle_at_50%_-20%,rgba(245,160,0,0.03),transparent_40%)]" />

      <div className="mx-auto max-w-[76rem]">
        <div className="max-w-3xl text-left">
          <p className="text-sm font-semibold tracking-wider text-lens-blue uppercase">Why Claread</p>
          <h2 className="mt-3 font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl md:text-5xl">
            读英文，不是拼凑单词的游戏。
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted">
            每一个英语阅读者都经历过这些隐形的摩擦。传统的翻译与词典工具只是替你跳过问题，而没有帮你真正看清句子。
          </p>
        </div>

        <div className="mt-14 grid gap-8 md:grid-cols-3">
          {painPoints.map(({ title, subtitle, problem, solution, icon: Icon }) => (
            <article
              key={title}
              className="group relative flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] transition-all hover:translate-y-[-2px] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)]"
            >
              <div>
                <div className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-lens-blue-soft text-lens-blue">
                  <Icon className="h-5 w-5" aria-hidden="true" />
                </div>
                <h3 className="mt-6 text-lg font-semibold text-ink group-hover:text-lens-blue transition-colors">
                  {title}
                </h3>
                <p className="mt-1.5 text-sm font-medium text-ink-soft/90 leading-relaxed">
                  {subtitle}
                </p>
                <p className="mt-4 text-sm leading-6 text-muted border-t border-hairline/60 pt-4">
                  {problem}
                </p>
              </div>

              <div className="mt-8 rounded-lg bg-web-canvas/60 p-4 border border-hairline/40">
                <span className="flex items-center gap-2 text-xs font-semibold text-lens-blue uppercase tracking-wider">
                  Claread Solution <ArrowRight className="h-3.5 w-3.5" />
                </span>
                <p className="mt-2 text-xs leading-5 text-ink-soft">
                  {solution}
                </p>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
