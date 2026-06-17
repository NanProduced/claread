import { HeroCopy } from "@/components/product-page/hero/HeroCopy";
import { HeroScrollScene } from "@/components/product-page/hero/HeroScrollScene";

export function ProductHero({ ctaHref, ctaLabel }: { ctaHref?: string; ctaLabel?: string }) {
  return (
    <section className="relative isolate overflow-x-clip px-5 pb-16 pt-24 sm:px-6 sm:pt-32 lg:px-8 lg:pb-0 lg:pt-32 xl:pt-40">
      <div className="absolute inset-0 -z-30 bg-[radial-gradient(circle_at_50%_6%,rgba(255,255,255,0.84),transparent_31%),linear-gradient(180deg,rgba(255,255,255,0.46),rgba(248,244,234,0.28)_58%,rgba(255,255,255,0.18))]" />

      <div className="relative z-10 mx-auto flex w-full max-w-[76rem] flex-col items-center">
        <HeroCopy ctaHref={ctaHref} ctaLabel={ctaLabel} />
        <HeroScrollScene />
      </div>
    </section>
  );
}
