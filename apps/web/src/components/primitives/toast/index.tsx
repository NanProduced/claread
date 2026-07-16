"use client"

import { Toaster, toast } from "sonner"

function ClareadToaster() {
  return (
    <Toaster
      position="bottom-right"
      richColors={false}
      closeButton={false}
      toastOptions={{
        className:
          "app-toast-surface !rounded-[var(--cl-radius-surface-sm)] !border !border-hairline !text-ink !shadow-[var(--cl-shadow-2)]",
        descriptionClassName: "!text-muted-foreground",
        actionButtonStyle: {
          background: "var(--ink)",
          color: "var(--surface)",
        },
        cancelButtonStyle: {
          background: "var(--app-control-quiet)",
          border: "1px solid var(--hairline)",
          color: "var(--ink)",
        },
      }}
    />
  )
}

export { ClareadToaster, toast }
