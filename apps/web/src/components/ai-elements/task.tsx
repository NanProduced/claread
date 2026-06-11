"use client";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/cn";
import { ChevronDownIcon, SearchIcon } from "lucide-react";
import type { ComponentProps } from "react";

export type TaskItemFileProps = ComponentProps<"div">;

export const TaskItemFile = ({
  children,
  className,
  ...props
}: TaskItemFileProps) => (
  <div
    className={cn(
      "inline-flex items-center gap-1 rounded-md border bg-secondary px-1.5 py-0.5 text-foreground text-xs",
      className
    )}
    {...props}
  >
    {children}
  </div>
);

export type TaskItemProps = ComponentProps<"div">;

export const TaskItem = ({ children, className, ...props }: TaskItemProps) => (
  <div className={cn("text-[13px] leading-6 text-muted-foreground", className)} {...props}>
    {children}
  </div>
);

export type TaskProps = Omit<ComponentProps<typeof Collapsible>, "children" | "className" | "defaultOpen"> & {
  className?: string;
  defaultOpen?: boolean;
  children?: React.ReactNode;
};

export const Task = ({
  defaultOpen = true,
  className,
  ...props
}: TaskProps) => (
  <Collapsible className={cn(className)} defaultOpen={defaultOpen} {...(props as any)} />
);

export type TaskTriggerProps = Omit<ComponentProps<typeof CollapsibleTrigger>, "children" | "className"> & {
  title: string;
  className?: string;
  children?: React.ReactNode;
};

export const TaskTrigger = ({
  children,
  className,
  title,
  ...props
}: TaskTriggerProps) => (
  <CollapsibleTrigger asChild className={cn("group", className)} {...(props as any)}>
    {children ?? (
      <div className="flex w-full cursor-pointer items-center gap-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground">
        <SearchIcon className="size-3.5" />
        <p className="text-[12px]">{title}</p>
        <ChevronDownIcon className="size-3.5 transition-transform group-data-[state=open]:rotate-180" />
      </div>
    )}
  </CollapsibleTrigger>
);

export type TaskContentProps = Omit<ComponentProps<typeof CollapsibleContent>, "children" | "className"> & {
  className?: string;
  children?: React.ReactNode;
};

export const TaskContent = ({
  children,
  className,
  ...props
}: TaskContentProps) => (
  <CollapsibleContent
    className={cn(
      "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 text-popover-foreground outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
      className
    )}
    {...(props as any)}
  >
    <div className="mt-2.5 space-y-1.5 pl-5">
      {children}
    </div>
  </CollapsibleContent>
);
