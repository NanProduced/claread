"use client";

import { cn } from "@/lib/cn";
import { ChevronDown, FileText, Globe } from "lucide-react";
import type { ComponentProps, HTMLAttributes, ReactNode } from "react";
import { createContext, useContext, useMemo, useState } from "react";

interface SourcesContextValue {
  open: boolean;
  setOpen: (value: boolean) => void;
}

const SourcesContext = createContext<SourcesContextValue | null>(null);

const useSources = () => {
  const context = useContext(SourcesContext);
  if (!context) {
    throw new Error("Sources subcomponents must be used within Sources");
  }
  return context;
};

export type SourcesProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  defaultOpen?: boolean;
};

export function Sources({
  children,
  className,
  defaultOpen = false,
  ...props
}: SourcesProps) {
  const [open, setOpen] = useState(defaultOpen);
  const value = useMemo(() => ({ open, setOpen }), [open]);

  return (
    <SourcesContext.Provider value={value}>
      <div
        className={cn("mt-3", className)}
        data-slot="sources"
        {...props}
      >
        {children}
      </div>
    </SourcesContext.Provider>
  );
}

export type SourcesTriggerProps = ComponentProps<"button"> & {
  children: ReactNode;
};

export function SourcesTrigger({
  children,
  className,
  ...props
}: SourcesTriggerProps) {
  const { open, setOpen } = useSources();
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={() => setOpen(!open)}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-medium text-ink-soft hover:bg-muted",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      data-slot="sources-trigger"
      {...props}
    >
      {children}
      <ChevronDown
        className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")}
      />
    </button>
  );
}

export type SourcesContentProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
};

export function SourcesContent({
  children,
  className,
  ...props
}: SourcesContentProps) {
  const { open } = useSources();
  if (!open) return null;
  return (
    <div
      className={cn("mt-2", className)}
      data-slot="sources-content"
      {...props}
    >
      {children}
    </div>
  );
}

export type SourceKind = "article" | "web";

const SOURCE_KIND_META: Record<SourceKind, { icon: typeof FileText; label: string }> = {
  article: { icon: FileText, label: "文章内依据" },
  web: { icon: Globe, label: "网页来源" },
};

export function SourcesItemMeta({ kind }: { kind: SourceKind }) {
  const meta = SOURCE_KIND_META[kind];
  const Icon = meta.icon;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
      <Icon className="h-3 w-3" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export type SourcesItemProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
};

export function SourcesItem({
  children,
  className,
  ...props
}: SourcesItemProps) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 border-t border-border/40 py-2 first:border-t-0 first:pt-0",
        className,
      )}
      data-slot="sources-item"
      {...props}
    >
      {children}
    </div>
  );
}

export type SourcesItemTitleProps = HTMLAttributes<HTMLSpanElement> & {
  children: ReactNode;
};

export function SourcesItemTitle({
  children,
  className,
  ...props
}: SourcesItemTitleProps) {
  return (
    <span
      className={cn("block text-[12px] font-medium text-ink", className)}
      data-slot="sources-item-title"
      {...props}
    >
      {children}
    </span>
  );
}

export type SourcesItemSnippetProps = HTMLAttributes<HTMLParagraphElement> & {
  children: ReactNode;
};

export function SourcesItemSnippet({
  children,
  className,
  ...props
}: SourcesItemSnippetProps) {
  return (
    <p
      className={cn(
        "mt-0.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground",
        className,
      )}
      data-slot="sources-item-snippet"
      {...props}
    >
      {children}
    </p>
  );
}
