import { TextDiagnosisPlate } from "@/components/product-page/TextDiagnosisPlate";

export function ProductPainPoints() {
  return (
    <section
      data-product-pain-points
      className="relative z-10 isolate overflow-hidden bg-[#F7F5F0] px-5 pb-16 pt-[clamp(5rem,8vw,7.5rem)] text-ink sm:px-6 sm:pb-20 lg:px-8"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-hairline/80" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-hairline/60" />

      <div className="relative mx-auto max-w-[76rem]">
        <div className="max-w-[42rem]">
          <p className="text-sm font-semibold text-lens-blue">常见阅读卡点</p>
          <h2 className="mt-4 max-w-[38rem] text-balance font-headline text-[clamp(2.35rem,4.2vw,4.6rem)] font-semibold leading-[1.02] text-ink">
            英文阅读卡住，通常不是因为少一个中文翻译。
          </h2>
          <p className="mt-5 max-w-[35rem] text-base leading-8 text-muted">
            真实的停顿发生在文本内部：词义、边界、主干和译文依赖。Claread 先诊断卡点，再给出对应标注。
          </p>
        </div>

        <TextDiagnosisPlate />
      </div>
    </section>
  );
}
