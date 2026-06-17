"use client";

import React from "react";
import { Task, TaskContent, TaskItem, TaskTrigger } from "@/components/ai-elements/task";
import { cn } from "@/lib/cn";

type TaskProcessCardProps = {
  title: string;
  detail: string;
  className?: string;
  children?: React.ReactNode;
};

export function TaskProcessCard({
  title,
  detail,
  className,
  children,
}: TaskProcessCardProps) {
  return (
    <Task className={cn("space-y-0.5", className)}>
      <TaskTrigger title={title} />
      <TaskContent>
        <TaskItem>{detail}</TaskItem>
        {children ? <div>{children}</div> : null}
      </TaskContent>
    </Task>
  );
}
