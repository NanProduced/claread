"use client";

import { getDraftCommentKey } from "@platejs/comment";

export function readerCommentDraftId() {
  return getDraftCommentKey();
}
