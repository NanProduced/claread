"use client";

/**
 * Comment Kit — 注册 Plate CommentPlugin + CommentLeaf
 *
 * 适配自 platejs registry 的 comment-kit.json：
 * - 移除 discussion-kit 依赖（V1 不接入 DiscussionKit，见 plan 决策 5）
 * - 简化 onClick handler：用 `api.comment.node()` 检查当前选区是否在 comment mark 上
 * - 保留 setDraft transform：创建 draft comment mark
 * - 保留 CommentLeaf render 配置
 * - `@/registry/ui/comment-node` → `@/components/ui/comment-node`
 *
 * Comment 是 text mark（leaf 级标记），不是 element。
 * 通过 `getCommentKey(id)` 生成 mark key，`getDraftCommentKey()` 返回 draft key。
 * 状态通过 options.activeId / options.hoverId 暴露，由 CommentLeaf 和面板读取。
 *
 * 集成方调用 `editor.tf.comment.setDraft()` 创建 draft comment，然后打开 inline comment 面板。
 */
import type { ExtendConfig, Path } from "platejs";

import {
  type BaseCommentConfig,
  BaseCommentPlugin,
  getDraftCommentKey,
} from "@platejs/comment";
import { toTPlatePlugin } from "platejs/react";

import { CommentLeaf } from "@/components/ui/comment-node";

type CommentConfig = ExtendConfig<
  BaseCommentConfig,
  {
    activeId: string | null;
    commentingBlock: Path | null;
    hoverId: string | null;
  }
>;

export const commentPlugin = toTPlatePlugin<CommentConfig>(BaseCommentPlugin, {
  handlers: {
    onClick: ({ api, setOption }) => {
      const commentEntry = api.comment?.node();
      setOption(
        "activeId",
        commentEntry ? api.comment?.nodeId(commentEntry[0]) ?? null : null,
      );
    },
  },
  options: { activeId: null, commentingBlock: null, hoverId: null },
})
  .extendTransforms(({ editor, setOption, tf: { comment: { setDraft } } }) => ({
    setDraft: () => {
      if (editor.selection && editor.api.isCollapsed()) {
        editor.tf.select(editor.api.block()![1]);
      }
      setDraft();
      editor.tf.collapse();
      setOption("activeId", getDraftCommentKey());
      setOption(
        "commentingBlock",
        editor.selection?.focus?.path?.slice(0, 1) ?? null,
      );
    },
  }))
  .configure({
    node: { component: CommentLeaf },
    shortcuts: { setDraft: { keys: "mod+shift+m" } },
  });

export const CommentKit = [commentPlugin];
