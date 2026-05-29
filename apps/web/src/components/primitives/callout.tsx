import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Info, AlertTriangle, AlertCircle, CheckCircle2 } from "lucide-react"

import { cn } from "@/lib/cn"

const calloutVariants = cva(
  "relative w-full rounded-lg border p-4 [&>svg]:absolute [&>svg]:text-foreground [&>svg]:left-4 [&>svg]:top-4 [&>svg+div]:translate-y-[-3px] [&:has(svg)]:pl-11",
  {
    variants: {
      variant: {
        default: "bg-background text-foreground",
        info: "border-lens-blue/20 bg-lens-blue-soft/50 text-lens-blue [&>svg]:text-lens-blue",
        warning: "border-amber-500/20 bg-amber-500/10 text-amber-700 [&>svg]:text-amber-600",
        error: "border-red-500/20 bg-red-500/10 text-red-700 [&>svg]:text-red-600",
        success: "border-structure-green/20 bg-structure-green/10 text-structure-green [&>svg]:text-structure-green",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

const iconMap = {
  default: Info,
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
  success: CheckCircle2,
}

export interface CalloutProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof calloutVariants> {
  icon?: React.ReactNode;
  title?: string;
}

export function Callout({ className, variant = "default", icon, title, children, ...props }: CalloutProps) {
  const Icon = iconMap[variant || "default"]

  return (
    <div className={cn(calloutVariants({ variant }), className)} {...props}>
      {icon !== null ? (icon || <Icon className="h-5 w-5" />) : null}
      <div className="flex flex-col gap-1">
        {title && <h5 className="font-medium leading-none tracking-tight">{title}</h5>}
        <div className="text-sm [&_p]:leading-relaxed opacity-90">{children}</div>
      </div>
    </div>
  )
}
