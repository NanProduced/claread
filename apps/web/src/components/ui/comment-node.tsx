"use client";

import * as React from "react";

import type { TCommentText } from "platejs";
import type { PlateLeafProps } from "platejs/react";

import { getCommentCount } from "@platejs/comment";
import { PlateLeaf, useEditorPlugin, usePluginOption } from "platejs/react";

import { cn } from "@/lib/cn";
import { commentPlugin } from "@/components/editor/plugins/comment-kit";

/**
 * CommentLeaf — 渲染 comment mark 的 leaf 组件
 *
 * 适配自 platejs registry 的 comment-node.json：
 * - `@/lib/utils` → `@/lib/cn`（项目 components.json 配置）
 * - `@/registry/components/editor/plugins/comment-kit` → `@/components/editor/plugins/comment-kit`
 * - `bg-highlight` / `border-b-highlight` → 项目兼容颜色（项目无 `--highlight` CSS 变量，
 *   改用 blue 色系与现有 user_note mark 视觉一致）
 *
 * Comment 是 text mark（leaf 级标记），通过 `getCommentKey(id)` 生成 mark key。
 * `api.comment.nodeId(leaf)` 从 leaf 读取 comment id。
 * 点击 mark 切换 activeId，悬停切换 hoverId，驱动面板显示。
 */
export function CommentLeaf(props: PlateLeafProps<TCommentText>) {
  const { children, leaf } = props;

  const { api, setOption } = useEditorPlugin(commentPlugin);
  const hoverId = usePluginOption(commentPlugin, "hoverId");
  const activeId = usePluginOption(commentPlugin, "activeId");

  const isOverlapping = getCommentCount(leaf) > 1;
  const currentId = api.comment.nodeId(leaf);
  const isActive = activeId === currentId;
  const isHover = hoverId === currentId;

  return (
    <PlateLeaf
      {...props}
      className={cn(
        "border-b-2 border-b-blue-300/50 bg-blue-50/40 transition-colors duration-200",
        (isHover || isActive) && "border-b-blue-400 bg-blue-100/70",
        isOverlapping && "border-b-2 border-b-blue-400/70 bg-blue-100/60",
        (isHover || isActive) && isOverlapping && "border-b-blue-500 bg-blue-200/80",
      )}
      attributes={{
        ...props.attributes,
        onClick: () => setOption("activeId", currentId ?? null),
        onMouseEnter: () => setOption("hoverId", currentId ?? null),
        onMouseLeave: () => setOption("hoverId", null),
      }}
    >
      {children}
    </PlateLeaf>
  );
}
