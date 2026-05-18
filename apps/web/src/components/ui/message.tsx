import * as React from "react";
import type { Components } from "react-markdown";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { Markdown } from "./markdown";

export interface MessageProps extends React.ComponentPropsWithoutRef<"div"> {
  children: React.ReactNode;
}

function Message({ children, className, ...props }: MessageProps) {
  return (
    <div className={cn("flex gap-3.5", className)} {...props}>
      {children}
    </div>
  );
}

export type MessageAvatarProps = {
  src: string;
  alt: string;
  fallback?: string;
  delayMs?: number;
  className?: string;
};

function MessageAvatar({
  src,
  alt,
  fallback,
  delayMs,
  className,
}: MessageAvatarProps) {
  return (
    <Avatar className={cn("h-8 w-8 shrink-0", className)}>
      <AvatarImage src={src} alt={alt} />
      {fallback ? <AvatarFallback delayMs={delayMs}>{fallback}</AvatarFallback> : null}
    </Avatar>
  );
}

export interface MessageContentProps extends React.ComponentPropsWithoutRef<"div"> {
  children: React.ReactNode;
  markdown?: boolean;
  markdownComponents?: Partial<Components>;
}

function MessageContent({
  children,
  markdown = false,
  className,
  markdownComponents,
  ...props
}: MessageContentProps) {
  const classNames = cn(
    "break-words whitespace-normal rounded-[var(--cl-radius-surface-sm)] border border-hairline/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(251,249,243,0.98))] px-4 py-3 text-ink-soft shadow-[0_12px_32px_rgba(17,17,17,0.05)]",
    className,
  );

  if (markdown) {
    return (
      <Markdown className={classNames} components={markdownComponents}>
        {String(children ?? "")}
      </Markdown>
    );
  }

  return (
    <div className={classNames} {...props}>
      {children}
    </div>
  );
}

export interface MessageActionsProps extends React.ComponentPropsWithoutRef<"div"> {
  children: React.ReactNode;
}

function MessageActions({ children, className, ...props }: MessageActionsProps) {
  return (
    <div className={cn("flex items-center gap-2 text-muted", className)} {...props}>
      {children}
    </div>
  );
}

export interface MessageActionProps extends React.ComponentProps<typeof Tooltip> {
  className?: string;
  tooltip: React.ReactNode;
  children: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right";
}

function MessageAction({
  tooltip,
  children,
  className,
  side = "top",
  ...props
}: MessageActionProps) {
  return (
    <Tooltip {...props}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} className={className}>
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

export { Message, MessageAction, MessageActions, MessageAvatar, MessageContent };
