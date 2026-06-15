const painNotes = [
  {
    label: "词义停顿",
    body: "词都查了，还是不知道这句话里的意思。",
    detail: "孤立释义没有处理短语、搭配和当前语境。",
    railClass: "bg-vocab-amber",
    labelClass: "text-[#8a6a0f]",
    markerClass: "bg-[#f6d67a]/[0.32] text-[#8a6a0f]",
  },
  {
    label: "结构停顿",
    body: "大意能猜，但语法关系没有接上。",
    detail: "真正影响理解的，常常是从句、倒装、修饰边界。",
    railClass: "bg-grammar-violet",
    labelClass: "text-[#5f4e8a]",
    markerClass: "bg-[#d0bff4]/[0.28] text-[#5f4e8a]",
  },
  {
    label: "长句停顿",
    body: "句子太长，读到后面忘了前面。",
    detail: "主干被补充信息盖住，阅读顺序变得不稳定。",
    railClass: "bg-structure-green",
    labelClass: "text-[#276c4d]",
    markerClass: "bg-[#3c8c68]/[0.14] text-[#276c4d]",
  },
  {
    label: "译文停顿",
    body: "中文看懂了，英文却没有真正读过。",
    detail: "整段翻译给出答案，却跳过了英文理解过程。",
    railClass: "bg-context-blue",
    labelClass: "text-[#355f87]",
    markerClass: "bg-[#a5d0ef]/[0.24] text-[#355f87]",
  },
];

export function ProductPainPoints() {
  return (
    <section
      data-product-pain-points
      className="relative overflow-hidden px-5 pb-8 pt-12 text-ink sm:px-6 sm:pb-12 sm:pt-16 lg:px-8"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-hairline/70" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-b from-transparent to-web-canvas" />

      <div className="relative mx-auto grid max-w-[76rem] gap-10 lg:grid-cols-[0.86fr_1.14fr] lg:items-center lg:gap-16">
        <div className="max-w-2xl">
          <p className="text-sm font-semibold text-lens-blue">常见阅读卡点</p>
          <h2 className="mt-4 max-w-[36rem] font-headline text-3xl font-semibold leading-[1.08] text-ink sm:text-4xl md:text-5xl">
            英文阅读卡住，通常不是因为少一个中文翻译。
          </h2>
          <p className="mt-5 max-w-[34rem] text-base leading-8 text-muted">
            很多时候，问题发生在原文里的某一个停顿。词义、结构、长句和译文依赖混在一起时，工具越多，阅读越乱。
          </p>
          <p className="mt-6 max-w-[32rem] rounded-[0.85rem] border border-hairline/80 bg-reader-paper/70 px-4 py-3 text-sm leading-7 text-ink-soft">
            Claread 从这些停顿开始，把不同卡点交给不同标注处理。
          </p>
        </div>

        <div className="relative">
          <div className="pointer-events-none absolute left-4 top-5 hidden h-[calc(100%-2.5rem)] w-px bg-hairline/80 sm:block" />
          <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
            {painNotes.map((note, index) => (
              <article
                key={note.label}
                className="group relative min-h-[10.25rem] overflow-hidden rounded-[0.75rem] border border-hairline/80 bg-surface-warm/90 px-5 py-4 shadow-[0_1px_2px_rgba(23,21,17,0.03),0_8px_20px_rgba(23,21,17,0.035)] transition duration-200 hover:-translate-y-0.5 hover:bg-surface"
              >
                <span className={`absolute inset-y-4 left-0 w-px ${note.railClass}`} aria-hidden="true" />
                <div className="flex items-center justify-between gap-3">
                  <span className={`text-sm font-semibold ${note.labelClass}`}>{note.label}</span>
                  <span
                    className={`inline-flex size-7 items-center justify-center rounded-full text-xs font-semibold ${note.markerClass}`}
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                </div>
                <p className="mt-4 font-reading text-[1.1rem] leading-7 text-ink sm:text-[1.16rem]">{note.body}</p>
                <p className="mt-3 text-sm leading-6 text-muted">{note.detail}</p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
