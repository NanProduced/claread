"use client";

import { cn } from "@/lib/cn";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type {
  ComponentProps,
  HTMLAttributes,
  ReactNode,
} from "react";
import {
  Children,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

interface InlineCitationContextValue {
  open: boolean;
  setOpen: (value: boolean) => void;
  openFromHover: () => void;
  scheduleClose: () => void;
  clearScheduledClose: () => void;
}

const InlineCitationContext = createContext<InlineCitationContextValue | null>(
  null,
);

const useInlineCitation = () => {
  const context = useContext(InlineCitationContext);
  if (!context) {
    throw new Error("InlineCitation subcomponents must be used within InlineCitation");
  }
  return context;
};

export type InlineCitationProps = HTMLAttributes<HTMLSpanElement> & {
  children: ReactNode;
  defaultOpen?: boolean;
};

export function InlineCitation({
  children,
  className,
  defaultOpen = false,
  ...props
}: InlineCitationProps) {
  const [open, setOpenState] = useState(defaultOpen);
  const closeTimerRef = useRef<number | null>(null);

  const clearScheduledClose = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const setOpen = useCallback(
    (value: boolean) => {
      if (value) {
        clearScheduledClose();
      }
      setOpenState(value);
    },
    [clearScheduledClose],
  );

  const openFromHover = useCallback(() => setOpen(true), [setOpen]);
  const scheduleClose = useCallback(() => {
    clearScheduledClose();
    closeTimerRef.current = window.setTimeout(() => {
      setOpenState(false);
      closeTimerRef.current = null;
    }, 120);
  }, [clearScheduledClose]);

  useEffect(() => clearScheduledClose, [clearScheduledClose]);

  const value = useMemo(
    () => ({ open, setOpen, openFromHover, scheduleClose, clearScheduledClose }),
    [clearScheduledClose, open, openFromHover, scheduleClose, setOpen],
  );

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <InlineCitationContext.Provider value={value}>
        <span
          className={cn("relative inline-flex items-center gap-1", className)}
          data-slot="inline-citation"
          {...props}
        >
          {children}
        </span>
      </InlineCitationContext.Provider>
    </PopoverPrimitive.Root>
  );
}

export type InlineCitationTextProps = HTMLAttributes<HTMLSpanElement> & {
  children: ReactNode;
};

export function InlineCitationText({
  children,
  className,
  ...props
}: InlineCitationTextProps) {
  return (
    <span
      className={cn("mr-0.5", className)}
      data-slot="inline-citation-text"
      {...props}
    >
      {children}
    </span>
  );
}

export type InlineCitationCardProps = HTMLAttributes<HTMLSpanElement> & {
  children: ReactNode;
};

export function InlineCitationCard({
  children,
  className,
  ...props
}: InlineCitationCardProps) {
  return (
    <span
      className={cn("relative inline-flex items-center", className)}
      data-slot="inline-citation-card"
      {...props}
    >
      {children}
    </span>
  );
}

function hostnameFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function triggerLabel(
  sources: string[],
  children: ReactNode | undefined,
): ReactNode {
  if (children != null) {
    return children;
  }
  if (sources.length === 0) {
    return "来源";
  }
  if (sources.length === 1) {
    return hostnameFromUrl(sources[0]);
  }
  const first = hostnameFromUrl(sources[0]);
  return `${first} +${sources.length - 1}`;
}

export type InlineCitationCardTriggerProps = ComponentProps<"button"> & {
  sources?: string[];
  children?: ReactNode;
};

export function InlineCitationCardTrigger({
  children,
  className,
  sources = [],
  onBlur,
  onFocus,
  onPointerEnter,
  onPointerLeave,
  ...props
}: InlineCitationCardTriggerProps) {
  const { clearScheduledClose, open, openFromHover, scheduleClose } =
    useInlineCitation();
  return (
    <PopoverPrimitive.Trigger asChild>
      <button
        type="button"
        aria-expanded={open}
        onPointerEnter={(event) => {
          onPointerEnter?.(event);
          if (!event.defaultPrevented && event.pointerType !== "touch") {
            openFromHover();
          }
        }}
        onPointerLeave={(event) => {
          onPointerLeave?.(event);
          if (!event.defaultPrevented && event.pointerType !== "touch") {
            scheduleClose();
          }
        }}
        onFocus={(event) => {
          onFocus?.(event);
          if (!event.defaultPrevented) {
            clearScheduledClose();
          }
        }}
        onBlur={(event) => {
          onBlur?.(event);
          if (!event.defaultPrevented) {
            scheduleClose();
          }
        }}
        className={cn(
          "inline-flex items-center justify-center text-[12px] font-medium text-muted-foreground underline decoration-transparent underline-offset-2",
          "transition-colors hover:text-foreground hover:decoration-current focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
        data-slot="inline-citation-card-trigger"
        {...props}
      >
        {triggerLabel(sources, children)}
      </button>
    </PopoverPrimitive.Trigger>
  );
}

/** Backwards-compatible alias for code that imported the old name. */
export const InlineCitationTrigger = InlineCitationCardTrigger;

export type InlineCitationCardBodyProps = ComponentProps<
  typeof PopoverPrimitive.Content
> & {
  children: ReactNode;
};

export function InlineCitationCardBody({
  children,
  className,
  onBlurCapture,
  onFocusCapture,
  onOpenAutoFocus,
  onPointerEnter,
  onPointerLeave,
  ...props
}: InlineCitationCardBodyProps) {
  const { clearScheduledClose, open, scheduleClose } = useInlineCitation();
  if (!open) return null;
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        side="top"
        align="start"
        sideOffset={8}
        collisionPadding={8}
        onOpenAutoFocus={(event) => {
          onOpenAutoFocus?.(event);
          if (!event.defaultPrevented) {
            event.preventDefault();
          }
        }}
        onPointerEnter={(event) => {
          onPointerEnter?.(event);
          if (!event.defaultPrevented) {
            clearScheduledClose();
          }
        }}
        onPointerLeave={(event) => {
          onPointerLeave?.(event);
          if (!event.defaultPrevented) {
            scheduleClose();
          }
        }}
        onFocusCapture={(event) => {
          onFocusCapture?.(event);
          if (!event.defaultPrevented) {
            clearScheduledClose();
          }
        }}
        onBlurCapture={(event) => {
          onBlurCapture?.(event);
          if (!event.defaultPrevented) {
            scheduleClose();
          }
        }}
        className={cn(
          "z-50 max-h-64 w-72 max-w-[calc(100vw-2rem)] overflow-auto rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-md outline-none",
          className,
        )}
        data-slot="inline-citation-card-body"
        {...props}
      >
        {children}
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}

interface InlineCitationCarouselContextValue {
  index: number;
  count: number;
  setIndex: (value: number) => void;
  goNext: () => void;
  goPrev: () => void;
}

const InlineCitationCarouselContext = createContext<InlineCitationCarouselContextValue | null>(
  null,
);

const useInlineCitationCarousel = () => {
  const context = useContext(InlineCitationCarouselContext);
  if (!context) {
    throw new Error(
      "InlineCitationCarousel subcomponents must be used within InlineCitationCarousel",
    );
  }
  return context;
};

export type InlineCitationCarouselProps = {
  children: ReactNode;
  count?: number;
  defaultIndex?: number;
  className?: string;
};

export function InlineCitationCarousel({
  children,
  count: countProp,
  defaultIndex = 0,
  className,
}: InlineCitationCarouselProps) {
  const items = useMemo(() => Children.toArray(children), [children]);
  const count = countProp ?? items.length;
  const [requestedIndex, setIndex] = useState(() =>
    Math.max(0, Math.min(defaultIndex, Math.max(0, count - 1))),
  );
  const index = Math.max(0, Math.min(requestedIndex, Math.max(0, count - 1)));

  const goNext = useCallback(
    () => {
      if (count > 1) {
        setIndex((previous) => {
          const current = Math.max(0, Math.min(previous, count - 1));
          return current + 1 >= count ? 0 : current + 1;
        });
      }
    },
    [count],
  );
  const goPrev = useCallback(
    () => {
      if (count > 1) {
        setIndex((previous) => {
          const current = Math.max(0, Math.min(previous, count - 1));
          return current - 1 < 0 ? count - 1 : current - 1;
        });
      }
    },
    [count],
  );

  const value = useMemo(
    () => ({ index, count, setIndex, goNext, goPrev }),
    [index, count, goNext, goPrev],
  );

  return (
    <InlineCitationCarouselContext.Provider value={value}>
      <div
        className={cn("flex flex-col gap-2", className)}
        data-slot="inline-citation-carousel"
      >
        {children}
      </div>
    </InlineCitationCarouselContext.Provider>
  );
}

export type InlineCitationCarouselHeaderProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
};

export function InlineCitationCarouselHeader({
  children,
  className,
  ...props
}: InlineCitationCarouselHeaderProps) {
  return (
    <div
      className={cn("flex items-center justify-between", className)}
      data-slot="inline-citation-carousel-header"
      {...props}
    >
      {children}
    </div>
  );
}

export type InlineCitationCarouselContentProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
};

export function InlineCitationCarouselContent({
  children,
  className,
  ...props
}: InlineCitationCarouselContentProps) {
  const { index } = useInlineCitationCarousel();
  return (
    <div
      className={cn("overflow-hidden", className)}
      data-slot="inline-citation-carousel-content"
      {...props}
    >
      <div
        className="flex transition-transform duration-200 ease-out"
        style={{ transform: `translateX(-${index * 100}%)` }}
      >
        {children}
      </div>
    </div>
  );
}

export type InlineCitationCarouselItemProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
};

export function InlineCitationCarouselItem({
  children,
  className,
  ...props
}: InlineCitationCarouselItemProps) {
  return (
    <div
      className={cn("w-full shrink-0", className)}
      data-slot="inline-citation-carousel-item"
      {...props}
    >
      {children}
    </div>
  );
}

export type InlineCitationCarouselPrevProps = ComponentProps<"button">;

export function InlineCitationCarouselPrev({
  className,
  ...props
}: InlineCitationCarouselPrevProps) {
  const { goPrev } = useInlineCitationCarousel();
  return (
    <button
      type="button"
      aria-label="上一条来源"
      onClick={goPrev}
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      data-slot="inline-citation-carousel-prev"
      {...props}
    >
      <ChevronLeft className="h-3.5 w-3.5" />
    </button>
  );
}

export type InlineCitationCarouselNextProps = ComponentProps<"button">;

export function InlineCitationCarouselNext({
  className,
  ...props
}: InlineCitationCarouselNextProps) {
  const { goNext } = useInlineCitationCarousel();
  return (
    <button
      type="button"
      aria-label="下一条来源"
      onClick={goNext}
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      data-slot="inline-citation-carousel-next"
      {...props}
    >
      <ChevronRight className="h-3.5 w-3.5" />
    </button>
  );
}

export type InlineCitationCarouselIndexProps = HTMLAttributes<HTMLSpanElement>;

export function InlineCitationCarouselIndex({
  className,
  ...props
}: InlineCitationCarouselIndexProps) {
  const { index, count } = useInlineCitationCarousel();
  return (
    <span
      className={cn("text-xs tabular-nums text-muted-foreground", className)}
      data-slot="inline-citation-carousel-index"
      {...props}
    >
      {index + 1}/{count}
    </span>
  );
}

export type InlineCitationSourceProps = HTMLAttributes<HTMLDivElement> & {
  description?: string;
  children?: ReactNode;
  label?: ReactNode;
};

export function InlineCitationSource({
  description,
  children,
  label = "文章依据",
  className,
  ...props
}: InlineCitationSourceProps) {
  return (
    <div
      className={cn("block text-xs font-medium text-ink", className)}
      data-slot="inline-citation-source"
      {...props}
    >
      <span className="block text-[12px] font-medium text-ink">{label}</span>
      {description ? (
        <span
          className="mt-0.5 block line-clamp-2 text-xs text-muted-foreground"
          data-slot="inline-citation-source-description"
        >
          {description}
        </span>
      ) : null}
      {children}
    </div>
  );
}

export type InlineCitationQuoteProps = HTMLAttributes<HTMLQuoteElement> & {
  children: ReactNode;
};

export function InlineCitationQuote({
  children,
  className,
  ...props
}: InlineCitationQuoteProps) {
  const [expanded, setExpanded] = useState(false);
  const expandable = typeof children === "string" && children.trim().length > 120;

  return (
    <>
      <blockquote
        className={cn(
          "mt-2 border-l border-border pl-2 text-[12.5px] leading-5 text-muted-foreground",
          expandable && !expanded && "line-clamp-3",
          className,
        )}
        data-slot="inline-citation-quote"
        {...props}
      >
        {children}
      </blockquote>
      {expandable ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={expanded ? "收起完整证据片段" : "展开完整证据片段"}
          className="mt-1 cursor-pointer rounded-sm text-[12px] text-muted-foreground underline decoration-transparent underline-offset-2 transition-colors hover:text-foreground hover:decoration-current focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "收起摘录" : "展开摘录"}
        </button>
      ) : null}
    </>
  );
}
