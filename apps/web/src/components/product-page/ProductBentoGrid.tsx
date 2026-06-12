import { BookOpen, Calendar, Highlighter, MessageSquare, Plus, Save } from "lucide-react";

export function ProductBentoGrid() {
  return (
    <section className="relative overflow-hidden bg-web-canvas px-5 py-16 sm:px-6 sm:py-24 lg:px-8 lg:py-28 border-b border-hairline/80">
      {/* Decorative Radial Lights */}
      <div className="absolute top-1/4 left-10 -z-10 h-72 w-72 rounded-full bg-lens-blue-soft/30 blur-3xl" />
      <div className="absolute bottom-1/4 right-10 -z-10 h-72 w-72 rounded-full bg-[rgba(255,214,112,0.15)] blur-3xl" />

      <div className="mx-auto max-w-[76rem]">
        <div className="max-w-3xl text-left">
          <p className="text-sm font-semibold tracking-wider text-lens-blue uppercase">Ecosystem & Features</p>
          <h2 className="mt-3 font-headline text-3xl font-semibold leading-tight text-ink sm:text-4xl md:text-5xl">
            把每一次透读，沉淀为个人资产。
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-8 text-muted">
            Claread 不仅是个阅读工具。每一次阅读中的划线、词典查询、追问回答，都会自动收录进你的知识库，供你随时复习与回源。
          </p>
        </div>

        {/* Bento Grid */}
        <div className="mt-14 grid grid-cols-1 md:grid-cols-6 gap-6">
          
          {/* Cell 1: 每日精读 (Daily Curated) - 3 col */}
          <div className="md:col-span-3 flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)] transition-all">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold text-lens-blue uppercase tracking-wider">
                <Calendar className="h-4 w-4" />
                <span>每日精读 | Daily Curated</span>
              </div>
              <h3 className="mt-4 text-xl font-headline font-semibold text-ink">
                每日推送，严选外刊优质输入
              </h3>
              <p className="mt-2 text-sm leading-6 text-muted">
                精选来自《经济学人》、《纽约时报》等权威外刊的社论和长文，配以精准标注，帮助你养成习惯，在真实场景中习得英语。
              </p>
            </div>

            {/* Illustration / UI Placement */}
            <div className="mt-6">
              {/* Mini Article Card */}
              <div className="rounded-lg border border-hairline bg-surface-warm p-4 shadow-sm relative overflow-hidden group">
                {/* Visual Image Placeholder */}
                <div className="h-32 w-full rounded bg-gradient-to-tr from-amber-100/40 via-purple-100/30 to-blue-100/40 flex flex-col justify-center items-center border border-dashed border-hairline relative">
                  <span className="text-[10px] text-muted/80 uppercase font-semibold tracking-widest">Illustration Placeholder</span>
                  <span className="text-[9px] text-muted/60 mt-1">Daily Editorial Cover (Ratio 4:3)</span>
                </div>
                
                <div className="mt-3 flex items-center justify-between text-[10px] text-muted font-sans">
                  <span>The Economist</span>
                  <span>12 Min Read</span>
                </div>
                <h4 className="mt-1.5 font-headline text-sm font-semibold text-ink leading-snug">
                  The Rise of Synthetic Reading and the Future of Literacy
                </h4>
              </div>
            </div>
          </div>

          {/* Cell 2: 点词查询与语境释义 (Vocab Loop) - 3 col */}
          <div className="md:col-span-3 flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)] transition-all">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold text-[#e4b000] uppercase tracking-wider">
                <BookOpen className="h-4 w-4" />
                <span>点词与生词本 | Vocab Loop</span>
              </div>
              <h3 className="mt-4 text-xl font-headline font-semibold text-ink">
                点词查询，自动关联语境释义
              </h3>
              <p className="mt-2 text-sm leading-6 text-muted">
                阅读中随时点击任意单词，AI 会给出该词在当句上下文中的精确释义，并支持一键加入你的个人生词本。
              </p>
            </div>

            {/* High-Fidelity UI Component */}
            <div className="mt-6 rounded-lg border border-hairline bg-reader-paper p-4 font-reading text-sm space-y-4">
              <p className="text-ink leading-relaxed">
                Reading is not just mapping symbols, but{" "}
                <span className="border border-amber-400 bg-amber-50 px-1 py-0.5 rounded text-ink font-semibold select-none cursor-pointer">
                  parsing
                </span>{" "}
                intentions.
              </p>
              
              {/* Lookup Card */}
              <div className="rounded bg-surface border border-hairline p-3 font-sans text-xs shadow-sm">
                <div className="flex items-center justify-between">
                  <div>
                    <strong className="text-sm text-ink">parsing</strong>
                    <span className="text-[10px] text-muted ml-2">/ˈpɑːrsɪŋ/</span>
                  </div>
                  <button className="flex items-center gap-1 rounded-full bg-[#EAF1FF] text-lens-blue border border-lens-blue/20 px-2 py-0.5 text-[10px] font-semibold hover:bg-lens-blue hover:text-white transition-colors duration-150 cursor-pointer">
                    <Plus className="h-3 w-3" />
                    <span>入生词本</span>
                  </button>
                </div>
                <div className="mt-2 text-muted leading-relaxed pt-2 border-t border-hairline/60">
                  <span className="text-[#c49200] font-semibold">语境释义：</span>
                  此处特指对作者真实句意与论证逻辑的“句法分析”。
                </div>
              </div>
            </div>
          </div>

          {/* Cell 3: 高亮与笔记 (Highlights & Notes) - 3 col */}
          <div className="md:col-span-3 flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)] transition-all">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold text-emerald-600 uppercase tracking-wider">
                <Highlighter className="h-4 w-4" />
                <span>高亮与笔记 | Highlights & Notes</span>
              </div>
              <h3 className="mt-4 text-xl font-headline font-semibold text-ink">
                沉浸标注，留下你的专属思考
              </h3>
              <p className="mt-2 text-sm leading-6 text-muted">
                用不同颜色的高亮标示优美句子或疑难片段，并像在纸书边缘一样，随时随地写下你的个人阅读感悟。
              </p>
            </div>

            {/* UI Component: Floating Sticky Note */}
            <div className="mt-6 rounded-lg border border-hairline bg-reader-paper p-4 font-reading text-sm relative">
              <p className="text-ink leading-relaxed">
                We must inspect the terminology and{" "}
                <span className="bg-emerald-50 text-ink border-b-2 border-emerald-500/30 px-0.5">
                  logical relations
                </span>{" "}
                without losing sight of the core argument.
              </p>

              {/* Sticky Note overlapping */}
              <div className="mt-3 rounded border border-amber-200 bg-[#FBF9F4] p-3 font-sans text-xs shadow-md max-w-[85%] ml-auto">
                <div className="text-[10px] text-muted flex items-center justify-between mb-1">
                  <span>我的笔记</span>
                  <span>1 分钟前</span>
                </div>
                <p className="text-ink-soft italic">
                  “注意此处的 without，表示不要丢失对核心论证的全局把握。”
                </p>
              </div>
            </div>
          </div>

          {/* Cell 4: AI 追问与保存笔记 (AI Cards) - 3 col */}
          <div className="md:col-span-3 flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)] transition-all">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold text-purple-600 uppercase tracking-wider">
                <Save className="h-4 w-4" />
                <span>AI 追问与沉淀 | AI Cards</span>
              </div>
              <h3 className="mt-4 text-xl font-headline font-semibold text-ink">
                行间 AI 追问，一键存入卡片
              </h3>
              <p className="mt-2 text-sm leading-6 text-muted">
                不理解句意时随时向 AI 提问。AI 生成的深度句法解释和翻译卡片，都支持一键保存为你的阅读资产，供以后复习。
              </p>
            </div>

            {/* UI Component: AI card save */}
            <div className="mt-6 rounded-lg border border-hairline bg-reader-paper p-4 font-sans text-xs space-y-3">
              <div className="flex items-center gap-2">
                <span className="h-5 px-1.5 flex items-center justify-center rounded bg-purple-50 border border-purple-100 text-purple-700 font-semibold text-[10px]">AI 追问</span>
                <span className="text-muted">“这句话里的 without 指什么？”</span>
              </div>
              
              {/* Answer Card */}
              <div className="rounded bg-surface border border-hairline p-3 relative group">
                <button className="absolute right-2 top-2 flex items-center gap-1 rounded bg-[#EAF1FF] text-lens-blue px-2 py-0.5 text-[9px] font-semibold border border-lens-blue/10 hover:bg-lens-blue hover:text-white transition-colors">
                  <Plus className="h-2.5 w-2.5" />
                  <span>存入笔记</span>
                </button>
                
                <h4 className="font-semibold text-ink">AI 回答</h4>
                <p className="mt-1.5 text-ink-soft leading-relaxed pr-12">
                  这里的 <strong>without</strong> 引导状语成分，表示‘在不失去对核心论据关注的前提下’。
                </p>
              </div>
            </div>
          </div>

          {/* Cell 5: Ask Claread (双栏宽) - 6 col */}
          <div className="md:col-span-6 flex flex-col justify-between rounded-xl border border-hairline bg-surface p-6 shadow-[0_4px_18px_rgba(17,17,17,0.03)] hover:shadow-[0_12px_28px_rgba(17,17,17,0.06)] transition-all">
            <div className="grid gap-6 md:grid-cols-2">
              <div>
                <div className="flex items-center gap-2 text-xs font-semibold text-lens-blue uppercase tracking-wider">
                  <MessageSquare className="h-4 w-4" />
                  <span>上下文 AI Chat | Ask Claread</span>
                </div>
                <h3 className="mt-4 text-xl font-headline font-semibold text-ink sm:text-2xl">
                  行间侧栏，与整篇文章上下文对话
                </h3>
                <p className="mt-2.5 text-sm leading-6 text-muted">
                  当你需要对整篇文章的逻辑关系、论证方法进行梳理时，右侧行间 AI Chat 随时待命。它天然理解你在读哪个段落、哪句话，回答永远锚定上下文，绝不跑题。
                </p>
                
                {/* Illustration Placeholder */}
                <div className="mt-6 h-36 w-full rounded-lg bg-gradient-to-br from-blue-50/50 via-indigo-50/50 to-purple-50/50 flex flex-col justify-center items-center border border-dashed border-hairline">
                  <span className="text-[10px] text-muted/80 uppercase font-semibold tracking-widest">Illustration Placeholder</span>
                  <span className="text-[9px] text-muted/60 mt-1">Context Chat Flow mockup (Ratio 16:9)</span>
                </div>
              </div>

              {/* Split-Pane UI Component */}
              <div className="rounded-lg border border-hairline bg-reader-paper shadow-sm overflow-hidden flex flex-col h-[280px]">
                <div className="border-b border-hairline/60 bg-surface/50 px-4 py-2 text-[10px] font-semibold text-muted flex items-center justify-between">
                  <span>Reader Sidebar</span>
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                </div>
                
                <div className="p-4 flex-1 overflow-y-auto space-y-4 font-sans text-xs [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
                  {/* User Message */}
                  <div className="flex justify-end">
                    <div className="bg-lens-blue text-white rounded-lg p-2.5 max-w-[85%] shadow-sm">
                      <p className="leading-relaxed">能分析一下第二段作者的语气吗？</p>
                    </div>
                  </div>
                  
                  {/* AI Message */}
                  <div className="flex justify-start">
                    <div className="bg-surface border border-hairline rounded-lg p-2.5 max-w-[85%] shadow-sm">
                      <p className="font-semibold text-lens-blue mb-1">Ask Claread</p>
                      <p className="text-ink-soft leading-relaxed">
                        作者的语气是<strong>中立偏审慎</strong>的。他使用了如 <i>“reduces the risk”</i>（降低风险）和 <i>“while allowing”</i>（在允许的同时）等限定词，暗示这一工作流设计在解决核心矛盾的同时具有很强的工程可行度。
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>
      </div>
    </section>
  );
}
