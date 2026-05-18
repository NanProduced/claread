"use client"

import { Toaster, toast } from "sonner"

function ClareadToaster() {
  return (
    <Toaster
      position="bottom-right"
      richColors={false}
      closeButton
      toastOptions={{
        className:
          "!rounded-[var(--cl-radius-surface-sm)] !border !border-hairline !bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(251,250,246,0.98))] !text-ink !shadow-[var(--cl-shadow-2)]",
        descriptionClassName: "!text-muted",
        actionButtonStyle: {
          background: "var(--ink)",
          color: "var(--surface)",
        },
      }}
    />
  )
}

export { ClareadToaster, toast }
