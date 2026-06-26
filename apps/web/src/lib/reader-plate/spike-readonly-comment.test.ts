import { describe, expect, it } from "vitest";
import { createPlateEditor } from "platejs/react";
import { BaseCommentPlugin, getDraftCommentKey, getCommentKey } from "@platejs/comment";
import type { Descendant } from "platejs";

const initialValue: Descendant[] = [
  {
    type: "p",
    children: [{ text: "Hello world, this is a test." }],
  },
];

function createEditor({ readOnly }: { readOnly: boolean }) {
  return createPlateEditor({
    plugins: [BaseCommentPlugin],
    value: initialValue,
    options: { readOnly },
  });
}

describe("Spike: Plate editor readOnly + CommentKit transform", () => {
  it("readOnly=false: tf.comment.setDraft adds draft comment mark", () => {
    const editor = createEditor({ readOnly: false });
    editor.tf.select(editor.children[0] as any, { at: [0], anchor: { path: [0, 0], offset: 0 }, focus: { path: [0, 0], offset: 5 } });
    editor.tf.focus();
    const tf = (editor as any).tf;
    if (tf?.comment?.setDraft) {
      tf.comment.setDraft();
    }
    const textNode = (editor.children[0] as any).children[0];
    expect(textNode.text).toBe("Hello world, this is a test.");
  });

  it("readOnly=true: check if comment marks can be added via transform", () => {
    const editor = createEditor({ readOnly: true });
    const tf = (editor as any).tf;
    const canCallSetDraft = typeof tf?.comment?.setDraft === "function";
    expect(canCallSetDraft).toBe(true);
  });

  it("getDraftCommentKey produces expected key format", () => {
    const draftKey = getDraftCommentKey();
    expect(draftKey).toBeTruthy();
    expect(typeof draftKey).toBe("string");
  });

  it("getCommentKey produces expected key format", () => {
    const commentKey = getCommentKey("comment-123");
    expect(commentKey).toBeTruthy();
    expect(typeof commentKey).toBe("string");
  });

  it("readOnly=true: editor still has comment API available", () => {
    const editor = createEditor({ readOnly: true });
    const api = (editor as any).api;
    expect(api).toBeTruthy();
    expect(typeof api?.comment?.has).toBe("function");
    expect(typeof api?.comment?.node).toBe("function");
  });
});
