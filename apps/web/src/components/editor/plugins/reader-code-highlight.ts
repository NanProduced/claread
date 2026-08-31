/**
 * Reader 代码块语法高亮 — 纯呈现层。
 *
 * 约束：
 * - shiki core + JavaScript regex engine，文法按语言懒加载；shiki 本体与
 *   文法都走 dynamic import，不进 Reader 首屏 bundle，也不阻塞首屏渲染。
 * - codeToTokens 产出 token 数组，由组件渲染 React spans；禁止任何直接
 *   注入 HTML 的渲染路径（守卫测试锁定）。
 * - fail-closed：语言未知、加载或高亮失败、文本无法逐字还原时一律返回
 *   null，组件回退现有纯文本渲染。
 * - 主题为低对比暖系：token 的 hex 只是「角色判别键」，真实渲染色由
 *   globals.css reader-record-plate-markdown-code-block 区段的 CSS 变量
 *   给出（深浅主题自适应），不引入深色 IDE 主题。
 */
import { useEffect, useState } from "react";
import type {
  HighlighterCore,
  LanguageRegistration,
  ThemedToken,
  ThemeRegistrationRaw,
} from "shiki/core";

export const READER_CODE_TOKEN_BASE_CLASS = "reader-record-plate-code-token";

const READER_CODE_THEME_NAME = "claread-reader-warm";

/**
 * 角色判别 hex（取 DESIGN.md 标注色的浅色值，仅作 token.color → class
 * 映射键；真实颜色在 globals.css 用 CSS 变量渲染）。
 */
const READER_CODE_ROLE_HEX = {
  keyword: "#5b4e7f",
  string: "#8a6500",
  function: "#2c6e94",
  type: "#7157a6",
  constant: "#237651",
  comment: "#6b6b6b",
} as const;

const READER_CODE_TOKEN_CLASS_BY_HEX: Record<string, string> = {
  [READER_CODE_ROLE_HEX.keyword]: `${READER_CODE_TOKEN_BASE_CLASS}--keyword`,
  [READER_CODE_ROLE_HEX.string]: `${READER_CODE_TOKEN_BASE_CLASS}--string`,
  [READER_CODE_ROLE_HEX.function]: `${READER_CODE_TOKEN_BASE_CLASS}--function`,
  [READER_CODE_ROLE_HEX.type]: `${READER_CODE_TOKEN_BASE_CLASS}--type`,
  [READER_CODE_ROLE_HEX.constant]: `${READER_CODE_TOKEN_BASE_CLASS}--constant`,
  [READER_CODE_ROLE_HEX.comment]: `${READER_CODE_TOKEN_BASE_CLASS}--comment`,
};

/** token 颜色 → class；未命中角色的 token 只带基础 class，继承正文墨色。 */
export function readerCodeTokenClassName(color: string | undefined): string {
  const roleClass = color ? READER_CODE_TOKEN_CLASS_BY_HEX[color.toLowerCase()] : undefined;
  return roleClass
    ? `${READER_CODE_TOKEN_BASE_CLASS} ${roleClass}`
    : READER_CODE_TOKEN_BASE_CLASS;
}

/** 低对比暖系主题：scope → 角色 hex；`editor.foreground` 为正文墨色（不映射角色）。 */
const readerWarmCodeTheme: ThemeRegistrationRaw = {
  name: READER_CODE_THEME_NAME,
  type: "light",
  colors: {
    "editor.foreground": "#151515",
    "editor.background": "#ffffff",
  },
  settings: [
    {
      scope: ["keyword", "storage"],
      settings: { foreground: READER_CODE_ROLE_HEX.keyword },
    },
    {
      scope: ["string", "punctuation.definition.string"],
      settings: { foreground: READER_CODE_ROLE_HEX.string },
    },
    {
      scope: ["entity.name.function", "support.function"],
      settings: { foreground: READER_CODE_ROLE_HEX.function },
    },
    {
      scope: [
        "entity.name.type",
        "support.type",
        "support.class",
        "entity.name.tag",
        "entity.name.namespace",
      ],
      settings: { foreground: READER_CODE_ROLE_HEX.type },
    },
    {
      scope: [
        "constant.numeric",
        "constant.language",
        "constant.character.escape",
      ],
      settings: { foreground: READER_CODE_ROLE_HEX.constant },
    },
    {
      // 运算符保持正文墨色，避免整行紫噪。
      scope: ["keyword.operator"],
      settings: { foreground: "#151515" },
    },
    {
      scope: ["comment", "punctuation.definition.comment"],
      settings: { foreground: READER_CODE_ROLE_HEX.comment },
    },
  ],
};

type ReaderCodeGrammarModule = {
  default: LanguageRegistration | LanguageRegistration[];
};

/**
 * curated 文法注册表：key 为文法自身的 `name`（codeToTokens 的 lang 入口），
 * value 为懒加载 import，保证 bundle 按需加载。
 */
const READER_CODE_GRAMMARS: Record<string, () => Promise<ReaderCodeGrammarModule>> = {
  c: () => import("shiki/langs/c.mjs"),
  cpp: () => import("shiki/langs/cpp.mjs"),
  csharp: () => import("shiki/langs/csharp.mjs"),
  css: () => import("shiki/langs/css.mjs"),
  diff: () => import("shiki/langs/diff.mjs"),
  docker: () => import("shiki/langs/docker.mjs"),
  go: () => import("shiki/langs/go.mjs"),
  html: () => import("shiki/langs/html.mjs"),
  ini: () => import("shiki/langs/ini.mjs"),
  java: () => import("shiki/langs/java.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  kotlin: () => import("shiki/langs/kotlin.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  php: () => import("shiki/langs/php.mjs"),
  python: () => import("shiki/langs/python.mjs"),
  ruby: () => import("shiki/langs/ruby.mjs"),
  rust: () => import("shiki/langs/rust.mjs"),
  scss: () => import("shiki/langs/scss.mjs"),
  shellscript: () => import("shiki/langs/shellscript.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  swift: () => import("shiki/langs/swift.mjs"),
  toml: () => import("shiki/langs/toml.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"),
  xml: () => import("shiki/langs/xml.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
};

/** 常见 fence 别名 → curated 文法名。 */
const READER_CODE_LANGUAGE_ALIASES: Record<string, string> = {
  "c#": "csharp",
  "c++": "cpp",
  cjs: "javascript",
  cs: "csharp",
  cxx: "cpp",
  dockerfile: "docker",
  golang: "go",
  js: "javascript",
  kt: "kotlin",
  md: "markdown",
  mjs: "javascript",
  py: "python",
  python3: "python",
  rb: "ruby",
  rs: "rust",
  sh: "shellscript",
  bash: "shellscript",
  shell: "shellscript",
  ts: "typescript",
  yml: "yaml",
  zsh: "shellscript",
};

/** 归一化 fence 语言串：未知语言返回 null（fail-closed 回退纯文本）。 */
export function resolveReaderCodeLanguage(
  language: string | null | undefined,
): string | null {
  if (typeof language !== "string") {
    return null;
  }
  const normalized = language.trim().toLowerCase();
  if (!normalized) {
    return null;
  }
  if (normalized in READER_CODE_GRAMMARS) {
    return normalized;
  }
  const aliased = READER_CODE_LANGUAGE_ALIASES[normalized];
  return aliased !== undefined && aliased in READER_CODE_GRAMMARS ? aliased : null;
}

/**
 * canonical 文法名 → 人类可读语言标签（与规格 §10 对齐：Python /
 * TypeScript / C++ / C# 等；未知语言保留原字符串）。
 */
const READER_CODE_LANGUAGE_LABELS: Record<string, string> = {
  c: "C",
  cpp: "C++",
  csharp: "C#",
  css: "CSS",
  diff: "Diff",
  docker: "Docker",
  go: "Go",
  html: "HTML",
  ini: "INI",
  java: "Java",
  javascript: "JavaScript",
  json: "JSON",
  kotlin: "Kotlin",
  markdown: "Markdown",
  php: "PHP",
  python: "Python",
  ruby: "Ruby",
  rust: "Rust",
  scss: "SCSS",
  shellscript: "Shell",
  sql: "SQL",
  swift: "Swift",
  toml: "TOML",
  typescript: "TypeScript",
  xml: "XML",
  yaml: "YAML",
};

/**
 * 语言归一化（badge / mermaid 特判等统一使用）：trim + lowercase；
 * 空白或缺失返回 null。
 */
export function readerCodeNormalizedLanguage(
  language: string | null | undefined,
): string | null {
  if (typeof language !== "string") {
    return null;
  }
  const trimmed = language.trim();
  return trimmed ? trimmed.toLowerCase() : null;
}

/**
 * 语言标签显示函数（Reader 与输入端共用）。
 *
 * 已知语言（含别名）返回人类可读形式；未知语言保留安全规范化名称
 * （trim 后的原字符串）；无语言返回 null，调用方不得虚构 badge。
 */
export function readerCodeLanguageLabel(
  language: string | null | undefined,
): string | null {
  if (typeof language !== "string") {
    return null;
  }
  const trimmed = language.trim();
  if (!trimmed) {
    return null;
  }
  const canonical = resolveReaderCodeLanguage(trimmed);
  if (!canonical) {
    return trimmed;
  }
  return READER_CODE_LANGUAGE_LABELS[canonical] ?? canonical;
}

let readerCodeHighlighterPromise: Promise<HighlighterCore> | null = null;

function getReaderCodeHighlighter(): Promise<HighlighterCore> {
  if (!readerCodeHighlighterPromise) {
    readerCodeHighlighterPromise = (async () => {
      const [coreModule, engineModule] = await Promise.all([
        import("shiki/core"),
        import("shiki/engine/javascript"),
      ]);
      return coreModule.createHighlighterCore({
        themes: [readerWarmCodeTheme],
        langs: [],
        engine: engineModule.createJavaScriptRegexEngine(),
      });
    })();
    // 引擎初始化失败时清空缓存，允许下一个代码块重试（仍 fail-closed）。
    readerCodeHighlighterPromise.catch(() => {
      readerCodeHighlighterPromise = null;
    });
  }
  return readerCodeHighlighterPromise;
}

const readerCodeLoadedLanguages = new Map<string, Promise<void>>();

function loadReaderCodeLanguage(canonical: string): Promise<void> {
  const cached = readerCodeLoadedLanguages.get(canonical);
  if (cached) {
    return cached;
  }
  const load = (async () => {
    const highlighter = await getReaderCodeHighlighter();
    const grammarModule = await READER_CODE_GRAMMARS[canonical]();
    await highlighter.loadLanguage(grammarModule.default);
  })();
  readerCodeLoadedLanguages.set(canonical, load);
  // 文法加载失败（如网络 chunk 失败）时清除缓存，允许重试。
  load.catch(() => {
    readerCodeLoadedLanguages.delete(canonical);
  });
  return load;
}

/** 把 tokens 逐字还原为纯文本（行内拼接、行间 \n）。 */
export function readerCodeTokensToPlainText(tokens: ThemedToken[][]): string {
  return tokens
    .map((line) => line.map((token) => token.content).join(""))
    .join("\n");
}

/**
 * codeToTokens 包装：成功且文本逐字还原时返回 tokens；
 * 未知语言、加载/高亮失败或文本漂移时返回 null（fail-closed）。
 */
export async function readerCodeToTokens(
  code: string,
  language: string | null | undefined,
): Promise<ThemedToken[][] | null> {
  const canonical = resolveReaderCodeLanguage(language);
  if (!canonical) {
    return null;
  }
  try {
    await loadReaderCodeLanguage(canonical);
    const highlighter = await getReaderCodeHighlighter();
    const result = highlighter.codeToTokens(code, {
      lang: canonical,
      theme: READER_CODE_THEME_NAME,
    });
    const tokens = result.tokens;
    return readerCodeTokensToPlainText(tokens) === code ? tokens : null;
  } catch {
    return null;
  }
}

/**
 * 渲染层高亮 hook：加载中 / 失败 / 未知语言时返回 null（组件回退纯文本），
 * 不阻塞首屏。tokens 以 {code, language} 为键存取：输入变化时旧结果在渲染期
 * 即视为过期（回退纯文本），不依赖 effect 里的同步 setState。
 */
export function useReaderCodeHighlight(
  code: string,
  language: string | null | undefined,
): ThemedToken[][] | null {
  const [entry, setEntry] = useState<{
    code: string;
    language: string | null | undefined;
    tokens: ThemedToken[][] | null;
  } | null>(null);

  const tokens =
    entry && entry.code === code && entry.language === language
      ? entry.tokens
      : null;

  useEffect(() => {
    let cancelled = false;
    if (resolveReaderCodeLanguage(language) === null) {
      return;
    }
    void readerCodeToTokens(code, language).then((next) => {
      if (!cancelled) {
        setEntry({ code, language, tokens: next });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [code, language]);

  return tokens;
}

// ---------------------------------------------------------------------------
// 输入端 transient decoration（规格 §10：复用同一 Shiki tokenizer，
// 以 Plate decorate 机制实现只读语法高亮）。
//
// - decoration 只在渲染层把 token class 合入 leaf，不改变 editor.children，
//   不进入 Markdown serialize，不污染 Slate value。
// - shiki tokenize 是异步的，而 Plate decorate 必须同步返回：首次命中
//   缓存未暖时调度一次 tokenize（去重），完成后通过 editor.api.redecorate()
//   触发重渲染，下次 decorate 命中缓存直接返回 ranges。
// ---------------------------------------------------------------------------

/** decoration 属性名：渲染层 renderLeaf 读 leaf[class] 得到 token class。 */
export const INPUT_CODE_TOKEN_DECORATION_PROP = "inputCodeTokenClass";

/**
 * decoration 的 leaf key：ranges 同步携带 `[INPUT_CODE_SYNTAX_DECORATION_KEY]:
 * true`，由输入端注册的同名 leaf 插件组件渲染（与官方 code-block
 * codeSyntax 同模式；不注册 leaf 插件时 decoration 不会落到 DOM）。
 */
export const INPUT_CODE_SYNTAX_DECORATION_KEY = "input_code_syntax";

type InputCodeDecorateEditor = {
  api: {
    redecorate?: () => void;
  };
};

/** decorate 内部用到的 editor api 形状（调用点做窄化 cast）。 */
type InputCodeDecorateApi = InputCodeDecorateEditor["api"] & {
  node(at: unknown[]): [unknown, unknown[]] | undefined;
};

type InputCodeDecorateEntry = [unknown, unknown[]];

export type InputCodeTokenRange = {
  anchor: { path: number[]; offset: number };
  focus: { path: number[]; offset: number };
  [key: string]: unknown;
};

const inputCodeTokenCache = new Map<string, ThemedToken[][] | null>();
const INPUT_CODE_TOKEN_CACHE_LIMIT = 512;

/**
 * 调度 slot = editor identity + code block path（真实 Slate 每次编辑都会
 * 替换节点对象，不得使用 codeBlock 对象 identity）。waiter 记录 editor 与
 * block path；timer fire 与 tokenize completion 都从 editor 当前树重新读取
 * 该 path 判定 stale。
 */
type InputCodeWaiter = {
  editor: InputCodeDecorateEditor;
  blockPath: number[];
};

type PendingInputCodeEntry = {
  waiters: Map<string, InputCodeWaiter>;
  timerId: ReturnType<typeof setTimeout>;
};

// pending 按 code 文本 key 去重；entry 记录等待该 key 的 waiter 集合与定时器。
const pendingInputCodeTokens = new Map<string, PendingInputCodeEntry>();
// waiter 当前挂在于哪个 key 的 entry 下（latest-wins 取消用）。
const inputCodeSlotPendingKey = new Map<string, string>();

const inputCodeEditorIds = new WeakMap<InputCodeDecorateEditor, number>();
let inputCodeNextEditorId = 1;

function inputCodeWaiterId(
  editor: InputCodeDecorateEditor,
  blockPath: number[],
): string {
  let id = inputCodeEditorIds.get(editor);
  if (id === undefined) {
    id = inputCodeNextEditorId;
    inputCodeNextEditorId += 1;
    inputCodeEditorIds.set(editor, id);
  }
  return `${id}:${blockPath.join(",")}`;
}

function inputCodeTokenCacheSet(key: string, tokens: ThemedToken[][] | null): void {
  if (!inputCodeTokenCache.has(key) && inputCodeTokenCache.size >= INPUT_CODE_TOKEN_CACHE_LIMIT) {
    const oldest = inputCodeTokenCache.keys().next().value;
    if (oldest !== undefined) {
      inputCodeTokenCache.delete(oldest);
    }
  }
  inputCodeTokenCache.set(key, tokens);
}

function inputCodeTokenCacheKey(language: string | null | undefined, code: string): string {
  return `${resolveReaderCodeLanguage(language) ?? "\u0000"}\u0000${code}`;
}

function inputCodeLineText(line: unknown): string {
  const children = (line as { children?: unknown[] } | null)?.children;
  if (!Array.isArray(children)) {
    return "";
  }
  return children
    .map((child) =>
      typeof (child as { text?: unknown })?.text === "string"
        ? (child as { text: string }).text
        : "",
    )
    .join("")
    .replace(/\r$/, "");
}

function inputCodeBlockText(codeBlock: unknown): string {
  const lines = (codeBlock as { children?: unknown[] } | null)?.children;
  if (!Array.isArray(lines)) {
    return "";
  }
  return lines.map(inputCodeLineText).join("\n");
}

/**
 * 从 editor 当前树重新读取 block path 上的 code block 状态。
 * 三个时点（schedule / timer fire / tokenize completion）统一用它判定：
 * 节点仍存在、仍是 code_block、language+code 的 cache key 是否仍等于任务 key。
 */
function readInputCodeBlockState(
  editor: InputCodeDecorateEditor,
  blockPath: number[],
): { language: string | null; code: string; key: string } | null {
  const api = editor.api as unknown as InputCodeDecorateApi;
  const entry = api.node(blockPath);
  const node = entry?.[0];
  if (!node || (node as { type?: string }).type !== "code_block") {
    return null;
  }
  const language = (node as { lang?: string | null }).lang ?? null;
  const code = inputCodeBlockText(node);
  if (!code) {
    return null;
  }
  return { language, code, key: inputCodeTokenCacheKey(language, code) };
}

function inputCodeWaiterWants(waiter: InputCodeWaiter, key: string): boolean {
  const state = readInputCodeBlockState(waiter.editor, waiter.blockPath);
  return state !== null && state.key === key;
}

/**
 * latest-wins：该 slot（editor+path）在其他 key 下尚有挂起任务时先撤销——
 * 摘除 waiter，entry 空了就取消定时器并删除 entry。burst 编辑只留最新
 * 版本的挂起任务；切换到 cache hit key 前同样先撤销。
 */
function cancelStaleInputCodePending(waiterId: string, newKey: string): void {
  const prevKey = inputCodeSlotPendingKey.get(waiterId);
  if (prevKey === undefined || prevKey === newKey) {
    return;
  }
  const prevEntry = pendingInputCodeTokens.get(prevKey);
  if (prevEntry) {
    prevEntry.waiters.delete(waiterId);
    if (prevEntry.waiters.size === 0) {
      clearTimeout(prevEntry.timerId);
      pendingInputCodeTokens.delete(prevKey);
    }
  }
  if (inputCodeSlotPendingKey.get(waiterId) === prevKey) {
    inputCodeSlotPendingKey.delete(waiterId);
  }
}

async function runInputCodeTokenize(
  key: string,
  code: string,
  language: string | null,
): Promise<void> {
  try {
    // fire 时校验：entry 可能已被 latest-wins 取消，或 waiter 已失效。
    const entry = pendingInputCodeTokens.get(key);
    if (!entry) {
      return;
    }
    const live = [...entry.waiters.values()].filter((waiter) =>
      inputCodeWaiterWants(waiter, key),
    );
    if (live.length === 0) {
      return;
    }
    const tokens = await readerCodeToTokens(code, language);
    // 完成时再校验：从当前树重读，全部过期则不缓存旧版本。
    const stillWanted = [
      ...(pendingInputCodeTokens.get(key)?.waiters.values() ?? []),
    ].some((waiter) => inputCodeWaiterWants(waiter, key));
    if (stillWanted) {
      inputCodeTokenCacheSet(key, tokens);
    }
  } finally {
    const waiting = pendingInputCodeTokens.get(key);
    if (waiting) {
      pendingInputCodeTokens.delete(key);
      // 仅通知仍期望该 key 的 waiter 的 editor；过期完成不触发刷新。
      const editors = new Set<InputCodeDecorateEditor>();
      waiting.waiters.forEach((waiter, waiterId) => {
        if (inputCodeWaiterWants(waiter, key)) {
          editors.add(waiter.editor);
        }
        if (inputCodeSlotPendingKey.get(waiterId) === key) {
          inputCodeSlotPendingKey.delete(waiterId);
        }
      });
      editors.forEach((editor) => {
        editor.api.redecorate?.();
      });
    }
  }
}

function scheduleInputCodeTokens(
  editor: InputCodeDecorateEditor,
  blockPath: number[],
): void {
  // schedule 时点：从当前树重读（节点存在 / code_block / 当前 key）。
  const state = readInputCodeBlockState(editor, blockPath);
  if (!state) {
    return;
  }
  const { key } = state;
  const waiterId = inputCodeWaiterId(editor, blockPath);
  cancelStaleInputCodePending(waiterId, key);
  if (inputCodeTokenCache.has(key)) {
    return;
  }
  const entry = pendingInputCodeTokens.get(key);
  if (entry) {
    entry.waiters.set(waiterId, { editor, blockPath });
    inputCodeSlotPendingKey.set(waiterId, key);
    return;
  }
  if (resolveReaderCodeLanguage(state.language) === null) {
    // 未知语言 fail-closed：负缓存，避免每次渲染重复解析。
    inputCodeTokenCacheSet(key, null);
    return;
  }
  const waiters = new Map([[waiterId, { editor, blockPath } as InputCodeWaiter]]);
  // setTimeout：tokenize 与 redecorate 都不能发生在 render 期间。
  const timerId = setTimeout(() => {
    void runInputCodeTokenize(key, state.code, state.language);
  }, 0);
  pendingInputCodeTokens.set(key, { waiters, timerId });
  inputCodeSlotPendingKey.set(waiterId, key);
}

/**
 * 输入端代码块高亮 decorate：仅对 code_block 内的 text 节点返回 ranges，
 * 把 token 的角色 class 作为 decoration 属性携带（渲染层由 renderLeaf 消费）。
 */
export function inputCodeBlockDecorate({
  editor,
  entry,
}: {
  editor: InputCodeDecorateEditor;
  entry: InputCodeDecorateEntry;
}): InputCodeTokenRange[] {
  const [node, entryPath] = entry;
  if (typeof (node as { text?: unknown })?.text !== "string") {
    return [];
  }
  // decorate 只处理 code_block 内的 text 节点，路径元素均为 number。
  const path = entryPath as number[];
  const api = editor.api as unknown as InputCodeDecorateApi;
  const lineEntry = api.node(path.slice(0, -1));
  const line = lineEntry?.[0];
  if (!line || (line as { type?: string }).type !== "code_line") {
    return [];
  }
  const blockEntry = api.node(path.slice(0, -2));
  const codeBlock = blockEntry?.[0];
  if (
    !codeBlock ||
    (codeBlock as { type?: string }).type !== "code_block"
  ) {
    return [];
  }

  scheduleInputCodeTokens(editor, path.slice(0, -2));

  const language = (codeBlock as { lang?: string | null }).lang ?? null;
  const code = inputCodeBlockText(codeBlock);
  if (!code) {
    return [];
  }
  const tokens = inputCodeTokenCache.get(inputCodeTokenCacheKey(language, code));
  if (!tokens) {
    return [];
  }

  const lines = (codeBlock as { children: unknown[] }).children;
  const lineIndex = path[path.length - 2] as number;
  const childIndex = path[path.length - 1] as number;
  const lineTokens = tokens[lineIndex] ?? [];
  if (lineTokens.length === 0) {
    return [];
  }

  // 该 text 节点在整段 code（含行间 \n）里的起始 offset。
  let lineStart = 0;
  for (let i = 0; i < lineIndex; i += 1) {
    lineStart += inputCodeLineText(lines[i]).length + 1;
  }
  const lineChildren = Array.isArray((line as { children?: unknown[] }).children)
    ? ((line as { children: unknown[] }).children)
    : [];
  let childStart = lineStart;
  for (let i = 0; i < childIndex; i += 1) {
    const sibling = lineChildren[i] as { text?: unknown } | undefined;
    if (typeof sibling?.text === "string") {
      childStart += sibling.text.length;
    }
  }
  const childEnd = childStart + (node as { text: string }).text.length;

  const ranges: InputCodeTokenRange[] = [];
  let tokenOffset = lineStart;
  for (const token of lineTokens) {
    const tokenStart = tokenOffset;
    tokenOffset += token.content.length;
    const tokenEnd = tokenOffset;
    const from = Math.max(tokenStart, childStart);
    const to = Math.min(tokenEnd, childEnd);
    if (to <= from) {
      continue;
    }
    ranges.push({
      anchor: { path, offset: from - childStart },
      focus: { path, offset: to - childStart },
      [INPUT_CODE_SYNTAX_DECORATION_KEY]: true,
      [INPUT_CODE_TOKEN_DECORATION_PROP]: readerCodeTokenClassName(token.color),
    });
  }
  return ranges;
}

/**
 * 仅供单元测试观察调度内部状态（只读快照）；生产代码不得依赖。
 */
export function __inputCodeHighlightSchedulerState(): {
  pendingKeys: string[];
  cacheKeys: string[];
  pendingSlotCount: number;
} {
  return {
    pendingKeys: [...pendingInputCodeTokens.keys()],
    cacheKeys: [...inputCodeTokenCache.keys()],
    pendingSlotCount: inputCodeSlotPendingKey.size,
  };
}
