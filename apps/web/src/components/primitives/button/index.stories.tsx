import type { Meta } from "@ladle/react";
import { ArrowRight, Heart, Trash2 } from "lucide-react";
import { Button } from ".";

export default {
  title: "Primitives/Button",
} satisfies Meta;

export const Variants = () => (
  <div className="flex flex-wrap gap-3">
    <Button variant="primary">开始透读</Button>
    <Button variant="secondary">回到原文</Button>
    <Button variant="outline">学习资产</Button>
    <Button variant="quiet">全部</Button>
    <Button variant="danger">退出登录</Button>
    <Button variant="primary-ink">新建笔记</Button>
  </div>
);

export const WithIcon = () => (
  <div className="flex flex-wrap gap-3">
    <Button variant="primary">
      继续阅读
      <ArrowRight className="h-4 w-4" />
    </Button>
    <Button variant="danger" size="sm">
      <Trash2 className="h-4 w-4" />
      删除
    </Button>
  </div>
);

export const Sizes = () => (
  <div className="flex flex-wrap items-center gap-3">
    <Button size="sm">sm</Button>
    <Button size="md">md</Button>
    <Button size="lg">lg</Button>
  </div>
);

/**
 * Tab into one of the focus-visible entries to see the keyboard-only
 * focus ring sourced from `primitiveFocusRing`. The disabled entries
 * must be reached by Tab too — not driven by a class.
 */
export const States = () => (
  <div className="flex flex-col gap-3">
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="primary">focus-visible (Tab me)</Button>
      <Button variant="primary" disabled>
        disabled
      </Button>
      <Button variant="danger" disabled>
        disabled danger
      </Button>
    </div>
    <p className="text-sm text-muted-foreground">
      focus ring 通过 primitiveFocusRing 锁定为 <code>focus-ring</code> +{" "}
      <code>surface-canvas</code>; 浏览器键盘 Tab 进入按钮时呈现真实焦点态;
      disabled 维持原生 opacity-50.
    </p>
  </div>
);

/**
 * Render each variant under Light / Dark only — the resolved theme.
 * Each container has the matching theme class so tokens resolve correctly.
 * next-themes' class strategy applies the same class to <html>, so isolated
 * theme previews cannot use the same hook. Instead we set the class
 * directly on the section element for visual scope.
 */
export const ThemeMatrix = () => {
  const themes = [
    { name: "light", className: "light" },
    { name: "dark", className: "dark" },
  ] as const;
  const variants = [
    "primary",
    "secondary",
    "outline",
    "quiet",
    "danger",
    "primary-ink",
  ] as const;
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
          <div className="flex flex-wrap gap-3">
            {variants.map((variant) => (
              <Button key={variant} variant={variant}>
                {variant}
              </Button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
};
