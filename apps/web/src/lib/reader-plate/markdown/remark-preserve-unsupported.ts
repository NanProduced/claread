/**
 * remark-preserve-unsupported — 输入端"不静默丢失"remark 插件。
 *
 * 挂在 remarkGfm 之后，把 Plate 输入端没有完整编辑支持的 mdast 节点
 * 在 deserialize 之前降级为安全节点，保证内容可见保留：
 *
 * - image → link：`![alt](src)` 变为普通链接节点 `[alt](src)`
 *   （无 alt 时用 src 作为可见文本）。输入端可见、可点击、可序列化往返。
 * - footnoteReference → text：`[^1]` 保留为字面文本。
 * - footnoteDefinition → paragraph：`[^1]: ...` 保留为普通段落
 *   （前缀 `[^1]: ` + 原内容），不丢脚注正文。
 * - task list listItem（remark-gfm 的 `checked`）→ 在首个 paragraph 前
 *   prepend 字面 `[ ] ` / `[x] ` 文本并清除 checked。列表结构保持
 *   ul/li，勾选状态以 GFM 原文形态可见；序列化回 `- [ ] ...` 可往返。
 * - raw HTML（mdast `html`，如 `vector<T>` 的 `<T>`、块级 `<div>`）→
 *   字面文本节点。不被 ALLOWED 白名单静默丢弃，可见保留。
 *
 * 只用于输入端（MarkdownTextInput 的 MarkdownPlugin 配置），
 * 不影响 reader-plate projection 路径。
 */

interface MdastNode {
  type: string;
  value?: string;
  url?: string;
  alt?: string | null;
  identifier?: string;
  label?: string;
  checked?: boolean | null;
  children?: MdastNode[];
}

function visit(node: MdastNode, fn: (n: MdastNode) => void): void {
  fn(node);
  if (node.children) {
    for (const child of node.children) {
      visit(child, fn);
    }
  }
}

function footnoteId(node: MdastNode): string {
  return node.identifier ?? node.label ?? "?";
}

function transform(node: MdastNode): void {
  // raw inline/block HTML（如 vector<T> 的 <T>）→ 字面文本，不静默丢弃
  if (node.type === "html") {
    node.type = "text";
    // value 保留原始字面量
    return;
  }
  // image → link（alt 作为可见文本，无 alt 用 url）
  if (node.type === "image") {
    const url = node.url ?? "";
    const alt = node.alt ?? "";
    node.type = "link";
    node.children = [{ type: "text", value: alt || url }];
    delete node.alt;
    return;
  }
  // footnoteReference → 字面文本 [^id]
  if (node.type === "footnoteReference") {
    node.type = "text";
    node.value = `[^${footnoteId(node)}]`;
    delete node.children;
    return;
  }
  // footnoteDefinition → 普通段落，前缀 [^id]:
  if (node.type === "footnoteDefinition") {
    const id = footnoteId(node);
    const originalChildren = node.children ?? [];
    node.type = "paragraph";
    node.children = [
      { type: "text", value: `[^${id}]: ` },
      // 原定义通常是一个 paragraph；展平一层避免段落套段落
      ...(originalChildren.length === 1 && originalChildren[0].type === "paragraph"
        ? (originalChildren[0].children ?? [])
        : originalChildren),
    ];
    return;
  }
  // task list listItem：checked → 前缀字面 [ ] / [x] 文本
  if (node.type === "listItem" && typeof node.checked === "boolean") {
    const marker = node.checked ? "[x] " : "[ ] ";
    const first = node.children?.[0];
    if (first && first.type === "paragraph") {
      first.children = [{ type: "text", value: marker }, ...(first.children ?? [])];
    } else {
      node.children = [
        { type: "paragraph", children: [{ type: "text", value: marker }] },
        ...(node.children ?? []),
      ];
    }
    node.checked = null;
  }
}

/**
 * unified/remark 插件约定：返回 transformer(tree)。
 * 放在 remarkGfm 之后，确保 GFM 扩展节点已经生成。
 */
export function remarkPreserveUnsupported() {
  return (tree: MdastNode) => {
    visit(tree, transform);
  };
}
