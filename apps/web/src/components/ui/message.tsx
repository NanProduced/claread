import * as React from "react";
import type { Components } from "react-markdown";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { Markdown } from "./markdown";

export interface MessageProps extends React.ComponentPropsWithoutRef<"div"> {
  children: React.ReactNode;
}

function Message({ children, className, ...props }: MessageProps) {
  return (
    <div className={cn("flex gap-3", className)} {...props}>
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
    "rounded-lg bg-secondary p-2 text-foreground break-words whitespace-normal",
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
    <div className={cn("text-muted-foreground flex items-center gap-2", className)} {...props}>
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
    <TooltipProvider>
      <Tooltip {...props}>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent side={side} className={className}>
          {tooltip}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { Message, MessageAction, MessageActions, MessageAvatar, MessageContent };
