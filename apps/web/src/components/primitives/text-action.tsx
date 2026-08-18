import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

/**
 * TextAction — 三级操作（链接式文字按钮）的统一形态。
 *
 * 动作语法分层：主 CTA = primary-ink 药丸（一页一个）；次级 = secondary
 * 方盒；三级 = 本组件（去修改 / 撤销 / 稍后处理 / 更换 / 移除这类低权重
 * 操作）。三级操作的 hover 只有一种配方：色深 + underline，不再混用
 * ghost 按钮与裸文字按钮。
 */
export function TextAction({
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type={type}
      className={cn(
        "focus-ring inline-flex min-h-8 cursor-pointer items-center gap-1 whitespace-nowrap rounded-[var(--cl-radius-control-sm)] px-1.5 text-[0.78rem] font-medium text-muted-foreground underline-offset-4 transition-colors duration-150 hover:text-ink hover:underline disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
        className,
      )}
      {...props}
    />
  );
}
