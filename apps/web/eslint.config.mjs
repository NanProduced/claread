import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const restrictedUiImportRule = {
  files: [
    "src/app/**/*.{ts,tsx}",
    "src/components/composed/**/*.{ts,tsx}",
    "src/components/layout/**/*.{ts,tsx}",
    "src/components/primitives/**/*.{ts,tsx}",
  ],
  ignores: [
    "src/components/ui/**/*.{ts,tsx}",
    "src/components/reader/AiWorkspacePanel.tsx",
  ],
  rules: {
    "no-restricted-imports": [
      "error",
      {
        patterns: ["@/components/ui/*"],
      },
    ],
  },
};

const eslintConfig = [...nextVitals, ...nextTs, restrictedUiImportRule];

export default eslintConfig;
