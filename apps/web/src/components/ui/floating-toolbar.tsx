"use client";

import * as React from "react";

import {
  type FloatingToolbarState,
  flip,
  offset,
  useFloatingToolbar,
  useFloatingToolbarState,
} from "@platejs/floating";
import { KEYS } from "platejs";
import {
  useEditorId,
  useEventEditorValue,
  usePluginOption,
} from "platejs/react";

import { Toolbar } from "./toolbar";
import { cn } from "@/lib/cn";

function composeRefs<T>(...refs: Array<React.Ref<T> | undefined>) {
  return (node: T) => {
    for (const ref of refs) {
      if (typeof ref === "function") {
        ref(node);
      } else if (ref && typeof ref === "object" && "current" in ref) {
        (ref as React.MutableRefObject<T | null>).current = node;
      }
    }
  };
}

export function FloatingToolbar({
  children,
  className,
  state,
  ...props
}: React.ComponentProps<typeof Toolbar> & {
  state?: FloatingToolbarState;
}) {
  const editorId = useEditorId();
  const focusedEditorId = useEventEditorValue("focus");
  const isFloatingLinkOpen = !!usePluginOption({ key: KEYS.link }, "mode");
  const isAIChatOpen = usePluginOption({ key: KEYS.aiChat }, "open");

  const floatingToolbarState = useFloatingToolbarState({
    editorId,
    focusedEditorId,
    hideToolbar: isFloatingLinkOpen || isAIChatOpen,
    ...state,
    floatingOptions: {
      middleware: [
        offset(12),
        flip({
          fallbackPlacements: [
            "top-start",
            "top-end",
            "bottom-start",
            "bottom-end",
          ],
          padding: 12,
        }),
      ],
      placement: "top",
      ...state?.floatingOptions,
    },
  });

  const {
    clickOutsideRef,
    hidden,
    props: rootProps,
    ref: floatingRef,
  } = useFloatingToolbar(floatingToolbarState);

  if (hidden) return null;

  return (
    <div ref={clickOutsideRef}>
      <Toolbar
        {...props}
        {...rootProps}
        ref={composeRefs<HTMLDivElement>(props.ref, floatingRef)}
        className={cn(
          "absolute z-50 max-w-[80vw] animate-in fade-in-0 zoom-in-95 duration-150 overflow-x-auto whitespace-nowrap rounded-md border bg-popover p-1 opacity-100 shadow-md print:hidden",
          className,
        )}
      >
        {children}
      </Toolbar>
    </div>
  );
}
