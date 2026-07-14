import type { Meta } from "@ladle/react";
import { ArrowRight, Heart, Trash2 } from "lucide-react";
import { IconButton } from ".";

export default {
  title: "Primitives/IconButton",
} satisfies Meta;

export const Default = () => (
  <div className="flex gap-3">
    <IconButton aria-label="回到原文">
      <ArrowRight className="h-4 w-4" />
    </IconButton>
    <IconButton aria-label="收藏" variant="quiet">
      <Heart className="h-4 w-4" />
    </IconButton>
    <IconButton aria-label="删除" variant="danger">
      <Trash2 className="h-4 w-4" />
    </IconButton>
  </div>
);

export const Sizes = () => (
  <div className="flex items-center gap-3">
    <IconButton aria-label="small" size="sm">
      <Heart className="h-4 w-4" />
    </IconButton>
    <IconButton aria-label="medium" size="md">
      <Heart className="h-4 w-4" />
    </IconButton>
    <IconButton aria-label="large" size="lg">
      <Heart className="h-4 w-4" />
    </IconButton>
  </div>
);

/**
 * Tab into one of the focus-visible entries to see the keyboard-only
 * focus ring sourced from `primitiveFocusRing`. The disabled entries
 * must be reached by Tab too — not driven by a class.
 */
export const States = () => (
  <div className="flex flex-col gap-3">
    <div className="flex items-center gap-3">
      <IconButton aria-label="focus-visible (Tab me)">
        <Heart className="h-4 w-4" />
      </IconButton>
      <IconButton aria-label="disabled" disabled>
        <Heart className="h-4 w-4" />
      </IconButton>
      <IconButton aria-label="disabled danger" variant="danger" disabled>
        <Trash2 className="h-4 w-4" />
      </IconButton>
    </div>
    <p className="text-sm text-muted-foreground">
      focus ring 通过 primitiveFocusRing 锁定为 <code>focus-ring</code> +{" "}
      <code>surface-canvas</code>; 浏览器键盘 Tab 进入按钮时呈现真实焦点态;
      disabled 维持原生 opacity-50; icon-only 必须传 <code>aria-label</code>。
    </p>
  </div>
);

/**
 * Render variants under Light / Dark only. Each container has the
 * matching theme class so tokens resolve correctly inside the preview.
 */
export const ThemeMatrix = () => {
  const themes = [
    { name: "light", className: "light" },
    { name: "dark", className: "dark" },
  ] as const;
  const variants = ["outline", "quiet", "danger"] as const;
  return (
    <div className="flex flex-col gap-6">
      {themes.map((theme) => (
        <section
          key={theme.name}
          className={`${theme.className} rounded-[var(--cl-radius-surface-md)] border border-border-subtle p-6`}
          style={{ background: "var(--surface-canvas)" }}
        >
          <header className="mb-3 text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
            {theme.name}
          </header>
          <div className="flex items-center gap-3">
            {variants.map((variant) => (
              <IconButton key={variant} aria-label={variant} variant={variant}>
                <Heart className="h-4 w-4" />
              </IconButton>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
};
