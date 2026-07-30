"use client";

/**
 * AI Elements — Chain of Thought disclosure family.
 *
 * Generic presentation primitives for an agent "process" disclosure:
 * a collapsible turn-scoped surface with a low-weight shimmer header,
 * typed status steps, and compact non-interactive search-result chips.
 *
 * Boundary rules (see ai-elements/README.md):
 * - Generic only: no business copy, no markdown rendering, no navigation.
 *   Ask Claread semantics (labels, reasoning text, domain chips) are
 *   composed above this family in `reader/ask-chat/turn-process.tsx`.
 * - Collapsed by default, always. There is deliberately NO auto-open —
 *   the disclosure stays collapsed while streaming until the user opens
 *   it. There IS a one-shot auto-close: if the user expanded during the
 *   stream, the disclosure re-collapses once ~1s after the turn settles;
 *   the user may expand again afterwards and it will not re-force-close.
 * - `ChainOfThoughtContent` is plain document flow. It never sets
 *   `overflow-y` — the surrounding conversation owns scrolling, and this
 *   component must not create a second scroll owner.
 * - `SearchResult` chips are non-interactive spans (no href, no button):
 *   they surface compact domain hints only. Interactive citation
 *   navigation belongs to a dedicated sources component above this layer.
 * - Warning/error/reason/CTA copy is never owned here; the host app's
 *   notice system (Prompt Kit SystemMessage) is the sole owner.
 */

import { useControllableState } from "@radix-ui/react-use-controllable-state";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/cn";
import {
  AlertTriangleIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleSlashIcon,
  LoaderCircleIcon,
  XIcon,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import {
  createContext,
  memo,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * Public status of one process step.
 * - `active`: in flight right now (renders a spinner glyph).
 * - `complete`: finished successfully.
 * - `degraded`: finished, partially unavailable (quiet alert glyph; the
 *   explanatory copy is owned by the host app's notice system, never here).
 * - `failed`: finished with an error.
 * - `interrupted`: the turn ended before this step finished. Must never be
 *   rendered with a success glyph.
 */
export type ChainOfThoughtStepStatus =
  | "active"
  | "complete"
  | "degraded"
  | "failed"
  | "interrupted";

interface ChainOfThoughtContextValue {
  isStreaming: boolean;
  isOpen: boolean;
}

const ChainOfThoughtContext = createContext<ChainOfThoughtContextValue | null>(
  null,
);

const AUTO_CLOSE_DELAY = 1000;

export const useChainOfThought = () => {
  const context = useContext(ChainOfThoughtContext);
  if (!context) {
    throw new Error(
      "ChainOfThought components must be used within ChainOfThought",
    );
  }
  return context;
};

export type ChainOfThoughtProps = Omit<
  ComponentProps<typeof Collapsible>,
  "children" | "className" | "open" | "defaultOpen" | "onOpenChange"
> & {
  /** Low-weight header shimmer hint; purely presentational. */
  isStreaming?: boolean;
  open?: boolean;
  /** Defaults to `false` — the disclosure never auto-opens. */
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  children?: ReactNode;
};

export const ChainOfThought = memo(
  ({
    className,
    isStreaming = false,
    open,
    defaultOpen,
    onOpenChange,
    children,
    ...props
  }: ChainOfThoughtProps) => {
    const [isOpen, setIsOpen] = useControllableState<boolean>({
      defaultProp: defaultOpen ?? false,
      onChange: onOpenChange,
      prop: open,
    });

    const hasEverStreamedRef = useRef(isStreaming);
    const [hasAutoClosed, setHasAutoClosed] = useState(false);

    useEffect(() => {
      if (isStreaming) {
        hasEverStreamedRef.current = true;
      }
    }, [isStreaming]);

    // One-shot auto-close when the stream settles — the same contract as
    // ai-elements/reasoning.tsx and the frozen r2-18 acceptance: a
    // disclosure opened during the turn re-collapses once when the turn
    // completes/interrupts, and stays re-expandable for review. There is
    // deliberately NO auto-open: collapsed-by-default is a hard product
    // requirement even while streaming.
    useEffect(() => {
      if (hasEverStreamedRef.current && !isStreaming && isOpen && !hasAutoClosed) {
        const timer = setTimeout(() => {
          setIsOpen(false);
          setHasAutoClosed(true);
        }, AUTO_CLOSE_DELAY);
        return () => clearTimeout(timer);
      }
    }, [isStreaming, isOpen, hasAutoClosed, setIsOpen]);

    const contextValue = useMemo(
      () => ({ isStreaming, isOpen }),
      [isStreaming, isOpen],
    );

    return (
      <ChainOfThoughtContext.Provider value={contextValue}>
        <Collapsible
          className={cn("not-prose", className)}
          onOpenChange={setIsOpen}
          open={isOpen}
          data-slot="chain-of-thought"
          {...props}
        >
          {children}
        </Collapsible>
      </ChainOfThoughtContext.Provider>
    );
  },
);

export type ChainOfThoughtHeaderProps = Omit<
  ComponentProps<typeof CollapsibleTrigger>,
  "children" | "className"
> & {
  /** Leading status glyph (host-provided, e.g. pulse dot or check). */
  glyph?: ReactNode;
  /** Title node — may be plain text or a shimmer while running. */
  children?: ReactNode;
  className?: string;
};

export const ChainOfThoughtHeader = memo(
  ({ className, glyph, children, ...props }: ChainOfThoughtHeaderProps) => {
    const { isOpen } = useChainOfThought();

    return (
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground",
          className,
        )}
        data-slot="chain-of-thought-trigger"
        {...props}
      >
        {glyph}
        <span className="min-w-0 flex-1 truncate text-left">{children}</span>
        <ChevronDownIcon
          className={cn(
            "size-4 shrink-0 transition-transform",
            isOpen ? "rotate-180" : "rotate-0",
          )}
          aria-hidden="true"
        />
      </CollapsibleTrigger>
    );
  },
);

export type ChainOfThoughtContentProps = Omit<
  ComponentProps<typeof CollapsibleContent>,
  "className"
> & {
  className?: string;
};

export const ChainOfThoughtContent = memo(
  ({ className, children, ...props }: ChainOfThoughtContentProps) => {
    return (
      <CollapsibleContent
        className={cn(
          "mt-2 text-sm",
          "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 text-muted-foreground outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
          // Plain flow only — never overflow-y: the conversation scroll
          // owner must remain the single scrollable ancestor.
          className,
        )}
        data-slot="chain-of-thought-content"
        {...props}
      >
        {children}
      </CollapsibleContent>
    );
  },
);

const STEP_STATUS_GLYPH: Record<ChainOfThoughtStepStatus, ReactNode> = {
  active: (
    <LoaderCircleIcon
      className="size-3.5 shrink-0 animate-spin motion-reduce:animate-none"
      aria-hidden="true"
    />
  ),
  complete: <CheckIcon className="size-3.5 shrink-0" aria-hidden="true" />,
  degraded: (
    <AlertTriangleIcon className="size-3.5 shrink-0" aria-hidden="true" />
  ),
  failed: <XIcon className="size-3.5 shrink-0" aria-hidden="true" />,
  interrupted: (
    <CircleSlashIcon className="size-3.5 shrink-0" aria-hidden="true" />
  ),
};

/**
 * Format a wire `duration_ms` for display without overstating precision.
 * - `null` → `null` (render nothing; the wire has no result duration yet)
 * - `< 1000` → `<1s`
 * - otherwise whole seconds
 */
export function formatChainOfThoughtDuration(
  durationMs: number | null | undefined,
): string | null {
  if (durationMs == null || !Number.isFinite(durationMs) || durationMs < 0) {
    return null;
  }
  if (durationMs < 1000) {
    return "<1s";
  }
  return `${Math.round(durationMs / 1000)}s`;
}

export type ChainOfThoughtStepProps = Omit<
  ComponentProps<"div">,
  "children" | "className"
> & {
  status: ChainOfThoughtStepStatus;
  label: ReactNode;
  description?: ReactNode;
  /** Overrides the default status glyph (e.g. a phase-specific icon). */
  icon?: ReactNode;
  durationMs?: number | null;
  /** In-step extra content (e.g. non-interactive search result chips). */
  children?: ReactNode;
  className?: string;
};

export const ChainOfThoughtStep = memo(
  ({
    status,
    label,
    description,
    icon,
    durationMs,
    children,
    className,
    ...props
  }: ChainOfThoughtStepProps) => {
    const duration = formatChainOfThoughtDuration(durationMs);

    return (
      <div
        className={cn(
          "flex items-start gap-2 py-1 text-[12px] leading-5",
          status === "active" ? "text-ink-soft" : "text-muted-foreground",
          className,
        )}
        data-slot="chain-of-thought-step"
        data-step-status={status}
        {...props}
      >
        <span className="mt-0.5 flex shrink-0 items-center justify-center">
          {icon ?? STEP_STATUS_GLYPH[status]}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-baseline gap-1.5">
            <span
              className={cn(
                "truncate font-medium",
                status === "failed" || status === "interrupted"
                  ? "text-ink-soft"
                  : undefined,
              )}
            >
              {label}
            </span>
            {duration ? (
              <span className="shrink-0 text-[11px] text-muted-foreground/80">
                {duration}
              </span>
            ) : null}
          </span>
          {description ? (
            <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground/90">
              {description}
            </span>
          ) : null}
          {children}
        </span>
      </div>
    );
  },
);

export type ChainOfThoughtSearchResultsProps = ComponentProps<"div">;

/** Compact, non-interactive container for search-result chips. */
export const ChainOfThoughtSearchResults = memo(
  ({ className, children, ...props }: ChainOfThoughtSearchResultsProps) => {
    return (
      <div
        className={cn("mt-1 flex flex-wrap items-center gap-1", className)}
        data-slot="chain-of-thought-search-results"
        {...props}
      >
        {children}
      </div>
    );
  },
);

export type SearchResultProps = Omit<
  ComponentProps<"span">,
  "children" | "className"
> & {
  /** Display hostname only — never a full URL. */
  domain: string;
  icon?: ReactNode;
  className?: string;
};

/**
 * Non-interactive domain chip. Deliberately NOT a link or button: the
 * chain-of-thought surface only hints at searched domains; citation
 * navigation is owned by a dedicated sources component.
 */
export const SearchResult = memo(
  ({ domain, icon, className, ...props }: SearchResultProps) => {
    return (
      <span
        className={cn(
          "inline-flex h-5 max-w-40 items-center gap-1 rounded-full border border-hairline/70 bg-surface/40 px-1.5 text-[11px] leading-none text-muted-foreground",
          className,
        )}
        data-slot="chain-of-thought-search-result"
        {...props}
      >
        {icon}
        <span className="truncate">{domain}</span>
      </span>
    );
  },
);

ChainOfThought.displayName = "ChainOfThought";
ChainOfThoughtHeader.displayName = "ChainOfThoughtHeader";
ChainOfThoughtContent.displayName = "ChainOfThoughtContent";
ChainOfThoughtStep.displayName = "ChainOfThoughtStep";
ChainOfThoughtSearchResults.displayName = "ChainOfThoughtSearchResults";
SearchResult.displayName = "SearchResult";
