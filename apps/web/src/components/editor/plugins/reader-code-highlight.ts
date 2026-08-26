/**
 * Reader 代码块语法高亮 — 纯呈现层（obs-01b-e F1）。
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
