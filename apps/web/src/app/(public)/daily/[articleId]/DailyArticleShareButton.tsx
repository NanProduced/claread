"use client";

import { Share2 } from "lucide-react";
import { toast } from "@/components/primitives/toast";

/**
 * 详情页 byline 分享按钮：优先 navigator.share，
 * 不支持时复制当前链接；无论走哪条路径都给 toast 反馈。
 */
export function DailyArticleShareButton({ title }: { title: string }) {
  const handleShare = async () => {
    const url = window.location.href;

    if (typeof navigator.share === "function") {
      try {
        await navigator.share({ title, url });
        toast.success("已打开系统分享");
        return;
      } catch (error) {
        // 用户取消分享时不提示，也不回退到复制。
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
      }
    }

    try {
      await navigator.clipboard.writeText(url);
      toast.success("链接已复制，去分享给朋友吧");
    } catch {
      toast.error("复制失败，请手动复制浏览器地址栏链接");
    }
  };

  return (
    <button
      type="button"
      onClick={handleShare}
      className="focus-ring inline-flex min-h-11 min-w-11 items-center justify-center text-[color:var(--dr-meta)] transition-colors hover:text-[color:var(--dr-accent)]"
      aria-label="分享"
    >
      <Share2 aria-hidden="true" className="h-[18px] w-[18px]" />
    </button>
  );
}
