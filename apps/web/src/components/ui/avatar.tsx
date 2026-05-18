"use client";

import * as React from "react";
import { cn } from "@/lib/cn";

type AvatarSize = "default" | "sm" | "lg";

function sizeClassName(size: AvatarSize) {
  if (size === "sm") {
    return "size-6";
  }
  if (size === "lg") {
    return "size-10";
  }
  return "size-8";
}

function Avatar({
  className,
  size = "default",
  children,
  ...props
}: React.ComponentProps<"span"> & { size?: AvatarSize }) {
  return (
    <span
      data-slot="avatar"
      data-size={size}
      className={cn(
        "group/avatar relative inline-flex shrink-0 overflow-hidden rounded-full select-none",
        sizeClassName(size),
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

function AvatarImage({
  className,
  src,
  alt,
  ...props
}: Omit<React.ComponentProps<"span">, "children"> & { src: string; alt: string }) {
  return (
    <span
      data-slot="avatar-image"
      role="img"
      aria-label={alt}
      className={cn("absolute inset-0 block bg-cover bg-center", className)}
      style={{ backgroundImage: `url("${src}")` }}
      {...props}
    />
  );
}

function AvatarFallback({
  className,
  children,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="avatar-fallback"
      className={cn(
        "absolute inset-0 flex size-full items-center justify-center rounded-full bg-muted text-sm text-muted-foreground",
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

function AvatarBadge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="avatar-badge"
      className={cn(
        "absolute bottom-0 right-0 z-10 inline-flex items-center justify-center rounded-full bg-primary text-primary-foreground ring-2 ring-background select-none",
        className,
      )}
      {...props}
    />
  );
}

function AvatarGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="avatar-group"
      className={cn("flex -space-x-2 *:ring-2 *:ring-background", className)}
      {...props}
    />
  );
}

function AvatarGroupCount({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="avatar-group-count"
      className={cn(
        "relative flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm text-muted-foreground ring-2 ring-background",
        className,
      )}
      {...props}
    />
  );
}

export {
  Avatar,
  AvatarImage,
  AvatarFallback,
  AvatarBadge,
  AvatarGroup,
  AvatarGroupCount,
};
