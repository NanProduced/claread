import Link from "next/link";
import { BrandLockup } from "@/components/brand/BrandMarks";
import { ProductStickerWall } from "./ProductStickerWall";
import { homeRoute, privacyRoute, termsRoute } from "@/lib/routes";

export function ProductFooter() {
  return (
    <footer className="bg-[#111115] text-[#FAF9F6]/90 pt-16 pb-12 px-5 sm:px-8 lg:px-12 border-t border-white/5">
      <div className="mx-auto max-w-[76rem] flex flex-col gap-12">
        {/* Top Section */}
        <div className="flex flex-col justify-between gap-8 sm:flex-row sm:items-start">
          <div className="flex flex-col gap-2 max-w-md">
            <BrandLockup
              href={homeRoute}
              imageClassName="!w-28 sm:!w-32 brightness-0 invert opacity-90"
            />
            <p className="text-xs text-[#FAF9F6]/50 leading-relaxed font-sans mt-3">
              专为深度阅读设计的心流空间。提供语境义标注、语法旁注、长难句结构拆解与行间 AI 追问，帮你跨越词典与翻译假象，真正读懂英文原文。
            </p>
          </div>

          <div className="flex gap-16 font-sans text-xs text-[#FAF9F6]/70">
            <div className="flex flex-col gap-3">
              <span className="font-semibold text-white/40 tracking-wider uppercase">法律条文</span>
              <Link href={privacyRoute} className="hover:text-white transition-colors">
                隐私政策
              </Link>
              <Link href={termsRoute} className="hover:text-white transition-colors">
                服务条款
              </Link>
            </div>
            <div className="flex flex-col gap-3">
              <span className="font-semibold text-white/40 tracking-wider uppercase">关于项目</span>
              <Link href="/help" className="hover:text-white transition-colors">
                透读方法
              </Link>
              <Link href="/about" className="hover:text-white transition-colors">
                关于我们
              </Link>
            </div>
          </div>
        </div>

        {/* Sticker Wall Section */}
        <div className="w-full h-[400px] sm:h-[480px]">
          <ProductStickerWall />
        </div>

        {/* Bottom Section */}
        <div className="flex flex-col items-center justify-between gap-4 border-t border-white/5 pt-8 text-xs text-white/30 font-sans sm:flex-row">
          <div className="flex items-center gap-1">
            <span>用 ☕ & 🧡 制作</span>
          </div>
          <div>
            <span>© {new Date().getFullYear()} Claread. 保留所有权利。</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
