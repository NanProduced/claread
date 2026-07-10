import { cn } from "@/lib/cn";

type ClareadAiMarkSize = "sm" | "md" | "lg";

const shellSizeClass: Record<ClareadAiMarkSize, string> = {
  sm: "h-8 w-8",
  md: "h-10 w-10",
  lg: "h-10 w-10",
};

const markSizeClass: Record<ClareadAiMarkSize, string> = {
  sm: "h-[18px] w-[18px]",
  md: "h-[22px] w-[22px]",
  lg: "h-[24px] w-[24px]",
};

const badgeSizeClass: Record<ClareadAiMarkSize, string> = {
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-4 w-4",
};

export interface ClareadAiMarkProps {
  size?: ClareadAiMarkSize;
  className?: string;
  markClassName?: string;
  badgeClassName?: string;
  showBadge?: boolean;
}

export function ClareadAiMark({
  size = "md",
  className,
  markClassName,
  badgeClassName,
  showBadge = true,
}: ClareadAiMarkProps) {
  return (
    <span
      className={cn(
        "claread-ai-mark brand-aperture-shell relative inline-flex shrink-0 items-center justify-center overflow-visible rounded-full border",
        shellSizeClass[size],
        className,
      )}
      data-claread-ai-mark="true"
      aria-hidden="true"
    >
      <span className="pointer-events-none absolute inset-[3px] rounded-full border border-hairline/60" />
      <img
        src="/brand/claread-icon-fullcolor.png"
        alt=""
        className={cn("brand-aperture-mark object-contain", markSizeClass[size], markClassName)}
      />
      {showBadge ? (
        <span
          className={cn(
            "absolute -bottom-0.5 -right-0.5 inline-flex items-center justify-center rounded-full border border-background/95 bg-background/95 text-vocab-amber shadow-[0_2px_6px_rgba(17,17,17,0.12)]",
            badgeSizeClass[size],
            badgeClassName,
          )}
          data-claread-ai-mark-badge="true"
        >
          <svg
            viewBox="0 0 16 16"
            className="h-[78%] w-[78%]"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M7.3 1.8 8.5 5.4l3.5 1.2-3.5 1.2-1.2 3.6-1.2-3.6-3.5-1.2 3.5-1.2 1.2-3.6Z"
              fill="currentColor"
            />
            <path
              d="M12.2 9.8 12.8 11l1.2.6-1.2.6-.6 1.3-.6-1.3-1.2-.6 1.2-.6.6-1.2Z"
              fill="currentColor"
            />
          </svg>
        </span>
      ) : null}
    </span>
  );
}
