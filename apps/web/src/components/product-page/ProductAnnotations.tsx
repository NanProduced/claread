import { BookOpen, HelpCircle, Highlighter, Languages } from "lucide-react";

export function ProductAnnotations() {
  return (
    <section className="relative overflow-hidden bg-surface-warm px-5 py-16 sm:px-6 sm:py-24 lg:px-8 lg:py-28 border-b border-hairline/80">
      <div className="absolute inset-0 -z-10 bg-[linear-gradient(180deg,rgba(255,255,255,0.4),rgba(246,243,236,0.3))]" />

      <div className="mx-auto max-w-[76rem]">
        <div className="max-w-3xl text-left">
          <p className="text-sm font-semibold tracking-wider text-lens-blue uppercase">Core Anatomy</p>
          <h2 className="mt-3 font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl md:text-5xl">
            四大标注，还原主干与枝叶。
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted">
            我们不提倡直接以总结代替阅读。Claread 在原文之上架起理解桥梁，把每一个词汇释义、语法线索和结构层次精准锚定到具体的句子上。
          </p>
        </div>

        <div className="mt-14 grid gap-8 md:grid-cols-2">
          {/* Card 1: 词汇与短语 */}
          <div className="flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] transition-all hover:translate-y-[-2px] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)]">
            <div>
              <div className="flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#EAF1FF] text-lens-blue">
                  <Highlighter className="h-4 w-4" />
                </span>
                <h3 className="text-lg font-semibold text-ink">词汇与短语分层</h3>
              </div>
              <p className="mt-3 text-sm leading-6 text-muted">
                不仅仅是查词典。Claread 自动将生词、习惯短语和学术术语归类，给出当下上下文中的精准意释，而非长串无关的字典词条。
              </p>
            </div>

            {/* High-Fidelity Mockup */}
            <div className="mt-8 rounded-lg border border-hairline bg-reader-paper p-5 font-reading relative">
              <p className="text-base text-ink leading-[1.8]">
                Claread treats the source text as an{" "}
                <span className="bg-[#EAF1FF] text-ink font-semibold px-1 py-0.5 rounded cursor-pointer border-b border-lens-blue/30 relative group">
                  anchored reading object
                  {/* Tooltip Mock */}
                  <span className="absolute left-1/2 bottom-[130%] -translate-x-1/2 w-64 bg-surface rounded-lg border border-hairline p-3 shadow-md font-sans text-xs text-ink opacity-100 z-20 pointer-events-none transition-opacity">
                    <span className="font-semibold text-lens-blue block mb-1">术语解释 | Term Note</span>
                    可被句子和标注稳定引用的原文基本阅读单元。
                  </span>
                </span>{" "}
                to prevent explanations from becoming detached.
              </p>

              <div className="mt-5 flex flex-wrap gap-2 font-sans border-t border-hairline/60 pt-4">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-lens-blue-soft px-2.5 py-0.5 text-xs font-medium text-lens-blue border border-lens-blue/10">
                  <span className="h-1.5 w-1.5 rounded-full bg-lens-blue" />
                  term_note (专业术语)
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-[rgba(255,214,112,0.22)] px-2.5 py-0.5 text-xs font-medium text-[#c49200] border border-[#e4b000]/20">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#e4b000]" />
                  context_gloss (语境义)
                </span>
              </div>
            </div>
          </div>

          {/* Card 2: 语法旁注 */}
          <div className="flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] transition-all hover:translate-y-[-2px] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)]">
            <div>
              <div className="flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-50 text-purple-600">
                  <HelpCircle className="h-4 w-4" />
                </span>
                <h3 className="text-lg font-semibold text-ink">语法旁注</h3>
              </div>
              <p className="mt-3 text-sm leading-6 text-muted">
                专治看得懂单词却理不清句子关系的“语法痛点”。用浅紫色下划线标记从句、倒装、非谓语等，并在行内提供简要旁注。
              </p>
            </div>

            {/* High-Fidelity Mockup */}
            <div className="mt-8 rounded-lg border border-hairline bg-reader-paper p-5 font-reading">
              <p className="text-base text-ink leading-[1.8]">
                For students, the difficulty is often{" "}
                <span className="border-b-2 border-dashed border-[#746694] px-0.5 cursor-pointer">
                  not that every word is unknown
                </span>
                ,{" "}
                <span className="border-b-2 border-dashed border-[#746694] px-0.5 cursor-pointer">
                  but that a sentence hides
                </span>{" "}
                the key information.
              </p>

              {/* Inline Grammar Note Card */}
              <div className="mt-4 rounded-lg bg-surface border border-hairline/80 p-3 shadow-sm font-sans text-xs">
                <div className="flex items-center justify-between border-b border-hairline/60 pb-1.5 mb-2">
                  <span className="font-semibold text-[#746694]">语法旁注 | not A but B 结构</span>
                  <span className="text-[10px] text-muted">双向绑定</span>
                </div>
                <p className="text-ink-soft leading-relaxed">
                  <strong>not that... but that...</strong> 表示“不是因为……而是因为……”。快速阅读时应直接定位到 <strong>but that</strong> 后面引出的关键信息主干。
                </p>
              </div>
            </div>
          </div>

          {/* Card 3: 长难句结构拆解 */}
          <div className="flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] transition-all hover:translate-y-[-2px] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)]">
            <div>
              <div className="flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
                  <BookOpen className="h-4 w-4" />
                </span>
                <h3 className="text-lg font-semibold text-ink">长难句结构拆解</h3>
              </div>
              <p className="mt-3 text-sm leading-6 text-muted">
                将杂乱修饰的长难句层次化。自动为句子按“主语、谓语、修饰语、从句”切分并缩进展示，清晰看清核心骨架与嵌套层次。
              </p>
            </div>

            {/* High-Fidelity Mockup */}
            <div className="mt-8 rounded-lg border border-hairline bg-reader-paper p-5 font-sans text-xs space-y-2">
              <div className="flex items-center justify-between text-[10px] text-muted border-b border-hairline/60 pb-1.5 mb-2">
                <span>句法骨架分析</span>
                <span>嵌套层级</span>
              </div>
              
              <div className="space-y-1.5 leading-relaxed">
                <div className="flex items-center gap-2">
                  <span className="h-5 px-1.5 flex items-center justify-center rounded bg-blue-50 border border-blue-100 text-blue-700 font-semibold text-[10px]">主干</span>
                  <span className="text-ink font-semibold">This design reduces the risk</span>
                </div>
                <div className="pl-6 border-l border-hairline/80 ml-2 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="h-5 px-1.5 flex items-center justify-center rounded bg-purple-50 border border-purple-100 text-purple-700 font-semibold text-[10px]">定语从句</span>
                    <span className="text-ink-soft">that an explanation becomes detached from the passage</span>
                  </div>
                  <div className="pl-6 border-l border-hairline/80 ml-2">
                    <div className="flex items-center gap-2">
                      <span className="h-5 px-1.5 flex items-center justify-center rounded bg-emerald-50 border border-emerald-100 text-emerald-700 font-semibold text-[10px]">伴随状语</span>
                      <span className="text-muted">while allowing academic readers to inspect terminology.</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Card 4: 双语对照翻译 */}
          <div className="flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] transition-all hover:translate-y-[-2px] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)]">
            <div>
              <div className="flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
                  <Languages className="h-4 w-4" />
                </span>
                <h3 className="text-lg font-semibold text-ink">双语对照翻译</h3>
              </div>
              <p className="mt-3 text-sm leading-6 text-muted">
                不粗暴替换原文，也不强制遮挡。轻巧对照，在每个英文句子下方以更淡的字重提供参考译文，并随阅读模式一键显示或隐藏。
              </p>
            </div>

            {/* High-Fidelity Mockup */}
            <div className="mt-8 rounded-lg border border-hairline bg-reader-paper p-5 relative overflow-hidden">
              <div className="absolute right-4 top-4 flex items-center gap-1.5 rounded-full bg-surface border border-hairline px-2 py-0.5 text-[10px] text-muted shadow-sm cursor-pointer select-none hover:border-lens-blue/30 hover:text-ink transition-colors duration-150">
                <Languages className="h-3 w-3 text-lens-blue" />
                <span>双语对照: 开</span>
              </div>

              <div className="space-y-3 mt-2">
                <div className="font-reading text-base text-ink leading-[1.8] pr-20">
                  This design reduces the risk that an explanation becomes detached from the passage.
                </div>
                <div className="font-sans text-xs text-muted leading-relaxed border-t border-hairline/60 pt-2.5">
                  这一设计降低了解释脱离原文段落的风险。
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
