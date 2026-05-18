"use client";

import { Toaster } from "sonner";

export function ClareadToaster() {
  return (
    <Toaster
      richColors={false}
      theme="light"
      position="top-center"
      toastOptions={{
        classNames: {
          toast:
            "border border-hairline bg-surface text-ink shadow-surface-quiet",
          title: "text-sm font-semibold",
          description: "text-xs text-muted",
        },
      }}
    />
  );
}
