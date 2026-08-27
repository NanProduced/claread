/**
 * remark-preserve-unsupported — 输入端"不静默丢失"remark 插件。
 *
 * 挂在 remarkGfm 之后，把 Plate 输入端没有完整编辑支持的 mdast 节点
 * 在 deserialize 之前降级为安全节点，保证内容可见保留：
 *
 * - footnoteReference → text：`[^1]` 保留为字面文本。
 * - footnoteDefinition → paragraph：`[^1]: ...` 保留为普通段落
 *   （前缀 `[^1]: ` + 原内容），不丢脚注正文。
 * - task list listItem（remark-gfm 的 `checked`）→ 在首个 paragraph 前
 *   prepend 字面 `[ ] ` / `[x] ` 文本并清除 checked。列表结构保持
 *   ul/li，勾选状态以 GFM 原文形态可见；序列化回 `- [ ] ...` 可往返。
 * - raw HTML（mdast `html`，如 `vector<T>` 的 `<T>`、块级 `<div>`）→
 *   字面文本节点。不被 ALLOWED 白名单静默丢弃，可见保留。
 *
 * image 不在此处降级：`![alt](url "title")` 的 mdast image 节点原样
 * 通过，由输入端 Markdown options 的 img 规则转换为 typed image element
 * （见 input-markdown-image-kit；G1′ 前曾降级为 generic link，导致
 * 图片性与 title 不可逆丢失）。
 *
 * reference-style image：`![alt][ref]` + `[ref]: url` 的
 * mdast imageReference / definition 都不在输入端 allowedNodes 内，会被
 * 静默丢弃。插件两遍解析：第一遍收集 definition（identifier 已由
 * parser 统一规范化为小写 + 空白折叠，ref/def 两侧一致，直接 Map 匹配；
 * first-wins）；第二遍把 resolved imageReference 内联为标准 image node
 * （url/title 取 definition，alt 取引用），编辑后 serialize 规范化为
 * inline image syntax（definition 行被消费）。unresolved 引用（parser
 * 正常已预解析为字面文本，此处为防御分支）降级为可见字面 Markdown。
 * linkReference 不处理（ordinary link 行为不变）。
 *
 * 只用于输入端（MarkdownTextInput 的 MarkdownPlugin 配置），
 * 不影响 reader-plate projection 路径。
 */

interface MdastNode {
  type: string;
  value?: string;
  url?: string;
  title?: string | null;
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

/** definition 收集结果（first-wins）。 */
interface CollectedDefinition {
  url?: string;
  title?: string | null;
}

/**
 * 第一遍：收集全树 definition，identifier 相同时首个生效（CommonMark
 * first-wins）。identifier 复用 parser 的规范化形式，不做二次折叠。
 */
function collectDefinitions(
  node: MdastNode,
  definitions: Map<string, CollectedDefinition>,
): void {
  visit(node, (n) => {
    if (n.type === "definition" && typeof n.identifier === "string") {
      if (!definitions.has(n.identifier)) {
        definitions.set(n.identifier, { url: n.url, title: n.title });
      }
    }
  });
}

function transform(node: MdastNode, definitions: Map<string, CollectedDefinition>): void {
  // raw inline/block HTML：由 MARKDOWN_PLUGIN_OPTIONS.rules.html 在
  // deserializer 层处理（source_callout 识别 + 非 aside 降级为 text）。
  // 此处不再降级 html 节点，避免 rules.html 永远收不到 html 节点。
  // image 也不在此处降级（G1′）：mdast image 原样通过，typed image
  // 转换由输入端 Markdown options 的 img 规则完成。
  // imageReference → 内联 image：resolved 时用 definition 的
  // url/title + 引用的 alt；unresolved 降级为可见字面 Markdown（防御
  // 分支——parser 正常已把无定义引用预解析为字面文本）。
  if (node.type === "imageReference") {
    const definition = definitions.get(node.identifier ?? "");
    if (definition) {
      node.type = "image";
      node.url = definition.url ?? "";
      if (typeof definition.title === "string") {
        node.title = definition.title;
      }
      delete node.identifier;
      delete node.label;
      return;
    }
    node.type = "text";
    node.value = `![${node.alt ?? ""}][${node.label ?? node.identifier ?? ""}]`;
    delete node.alt;
    delete node.identifier;
    delete node.label;
    delete node.children;
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
    // 第一遍：收集 definition（引用与定义的相对顺序无关）。
    const definitions = new Map<string, CollectedDefinition>();
    collectDefinitions(tree, definitions);
    // 第二遍：footnote/task-list 降级 + reference-style image 内联。
    visit(tree, (n) => transform(n, definitions));
  };
}
