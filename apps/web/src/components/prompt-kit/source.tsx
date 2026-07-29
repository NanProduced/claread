"use client";

import { ExternalLink, Globe2 } from "lucide-react";
import { createContext, useContext, type ReactNode } from "react";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { cn } from "@/lib/cn";

interface SourceContextValue {
  href: string;
  domain: string;
}

const SourceContext = createContext<SourceContextValue | null>(null);

function useSourceContext() {
  const context = useContext(SourceContext);
  if (context == null) {
    throw new Error("Source.* must be used inside <Source>");
  }
  return context;
}

function sourceDomain(href: string) {
  try {
    return new URL(href).hostname;
  } catch {
    return href.split("/").filter(Boolean).at(-1) ?? href;
  }
}

/**
 * Prompt Kit's compact web-source disclosure, adapted to Claread's existing
 * HoverCard primitive and semantic tokens. It intentionally accepts only a
 * typed URL/title/description tuple; v1's generic web snapshot is not used.
 */
export function Source({ href, children }: { href: string; children: ReactNode }) {
  const domain = sourceDomain(href);

  return (
    <SourceContext.Provider value={{ href, domain }}>
      <HoverCard closeDelay={100} openDelay={150}>
        {children}
      </HoverCard>
    </SourceContext.Provider>
  );
}

export function SourceTrigger({
  label,
  showFavicon = false,
  className,
}: {
  label?: string | number;
  showFavicon?: boolean;
  className?: string;
}) {
  const { href, domain } = useSourceContext();
  const labelToShow = label ?? domain.replace(/^www\./, "");

  return (
    <HoverCardTrigger asChild>
      <a
        aria-label={`查看网页来源 ${labelToShow}`}
        className={cn(
          "inline-flex h-5 max-w-40 items-center gap-1 overflow-hidden rounded-full bg-muted px-1.5 text-xs font-medium text-muted-foreground no-underline transition-colors duration-150 hover:bg-muted/80 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
        data-slot="prompt-kit-source-trigger"
        href={href}
        rel="noopener noreferrer"
        target="_blank"
      >
        {showFavicon ? <Globe2 aria-hidden="true" className="size-3 shrink-0" /> : null}
        <span className="truncate tabular-nums">{labelToShow}</span>
      </a>
    </HoverCardTrigger>
  );
}

export function SourceContent({
  title,
  description,
  className,
}: {
  title: string;
  description?: string;
  className?: string;
}) {
  const { href, domain } = useSourceContext();

  return (
    <HoverCardContent
      className={cn("w-72 max-w-[calc(100vw-2rem)] p-0 shadow-md", className)}
      data-slot="prompt-kit-source-content"
    >
      <a
        className="flex flex-col gap-2 p-3 no-underline"
        href={href}
        rel="noopener noreferrer"
        target="_blank"
      >
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Globe2 aria-hidden="true" className="size-3.5 shrink-0" />
          <span className="truncate">{domain.replace(/^www\./, "")}</span>
          <ExternalLink aria-hidden="true" className="ml-auto size-3 shrink-0" />
        </span>
        <span className="line-clamp-2 text-sm font-medium text-foreground">{title}</span>
        {description ? (
          <span className="line-clamp-2 text-xs leading-5 text-muted-foreground">
            {description}
          </span>
        ) : null}
      </a>
    </HoverCardContent>
  );
}

/**
 * The answer-level form used once the server exposes typed web citations.
 * Source pills retain a compact reading flow while their hover cards carry
 * the title and excerpt. This deliberately has no dependency on article
 * evidence handles or v1's generic `web_snapshot` field.
 */
export interface WebSourceItem {
  citationId: string;
  href: string;
  title: string;
  description?: string;
}

export function WebSources({ sources }: { sources: readonly WebSourceItem[] }) {
  if (sources.length === 0) {
    return null;
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5" data-slot="prompt-kit-sources">
      <span className="text-xs font-medium text-muted-foreground">
        网页来源 · {sources.length}
      </span>
      {sources.map((source) => (
        <Source href={source.href} key={source.citationId}>
          <SourceTrigger showFavicon />
          <SourceContent description={source.description} title={source.title} />
        </Source>
      ))}
    </div>
  );
}
