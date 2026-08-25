import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(__dirname, "../..");
const read = (path: string) => readFileSync(resolve(ROOT, path), "utf8");
const legacyCitationName = ["Citation", "List"].join("");
const legacyProvenanceName = ["Ask", "Provenance", "Line"].join("");
const legacyProvenanceRef = ["provenance", "Signature", "Ref"].join("");
const legacyContextPreview = ["context", "Preview"].join("");

describe("Ask Claread frozen UI cleanup", () => {
  it("removes the duplicate provenance and legacy citation surfaces", () => {
    const panel = read("src/components/reader/AiWorkspacePanel.tsx");
    const suggestions = read("src/components/reader/ask-chat/PromptSuggestions.tsx");
    const stories = read("src/components/reader/ask-chat/index.stories.tsx");

    expect(panel).not.toContain(legacyProvenanceName);
    expect(panel).not.toContain(legacyProvenanceRef);
    expect(suggestions).not.toContain(legacyContextPreview);
    expect(suggestions).not.toContain("suggestion.badgeClassName");
    expect(suggestions).not.toContain("suggestion.icon");
    expect(stories).not.toContain(legacyCitationName);
    expect(
      existsSync(resolve(ROOT, `src/components/reader/ask-chat/${legacyCitationName}.tsx`)),
    ).toBe(false);
    expect(
      existsSync(resolve(ROOT, `src/components/reader/ask-chat/${legacyCitationName}.test.tsx`)),
    ).toBe(false);
  });

  it("reuses the PromptInput model select and one answer typography recipe", () => {
    const composer = read("src/components/reader/ask-chat/AskComposer.tsx");
    const panel = read("src/components/reader/AiWorkspacePanel.tsx");

    expect(composer).toContain("PromptInputSelectTrigger");
    expect(composer).toContain('status={sending ? "streaming" : "ready"}');
    expect(panel).toContain("ASK_ANSWER_MARKDOWN_CLASSNAME");
    expect(panel.match(/ASK_ANSWER_MARKDOWN_CLASSNAME/g)).toHaveLength(3);
  });

  it("keeps the docked Ask column narrower than the reading stage", () => {
    const styles = read("src/app/globals.css");

    expect(styles).toMatch(
      /var\(--reader-ask-column-width,\s*clamp\(24rem,\s*25\.5vw,\s*32rem\)\)/,
    );
  });

  it("keeps the Ask desktop density and compact composer typography explicit", () => {
    const panel = read("src/components/reader/AiWorkspacePanel.tsx");
    const composer = read("src/components/reader/ask-chat/AskComposer.tsx");

    expect(panel).toContain("sm:gap-4 sm:px-5 sm:pb-6 sm:pt-3");
    expect(composer).toContain("!text-sm");
    expect(composer).toContain("!h-7");
    expect(composer).toContain("!text-xs !font-normal");
  });
});
