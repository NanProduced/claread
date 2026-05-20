"use client";

import * as React from "react";
import type { PlateContentProps } from "platejs/react";
import { PlateContent } from "platejs/react";
import { cn } from "../../lib/cn";

export function EditorContainer({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "ignore-click-outside/toolbar relative w-full cursor-text select-text overflow-y-auto rounded-xl caret-primary selection:bg-brand/25 focus-visible:outline-none [&_.slate-selection-area]:z-50 [&_.slate-selection-area]:rounded-md [&_.slate-selection-area]:border [&_.slate-selection-area]:border-brand/25 [&_.slate-selection-area]:bg-brand/15",
        className,
      )}
      {...props}
    />
  );
}

export const Editor = React.forwardRef<HTMLDivElement, PlateContentProps>(function Editor(
  { className, ...props },
  ref,
) {
  return (
    <PlateContent
      ref={ref}
      disableDefaultStyles
      className={cn(
        "group/editor relative w-full cursor-text select-text overflow-x-hidden whitespace-pre-wrap break-words rounded-xl focus-visible:outline-none [&_strong]:font-bold",
        className,
      )}
      {...props}
    />
  );
});
