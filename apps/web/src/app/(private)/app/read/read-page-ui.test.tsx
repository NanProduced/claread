/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  ReadPageHero,
  ReadPageUiProvider,
  useReadPageUi,
} from "./read-page-ui";

function HeroWithState({ hasContent }: { hasContent: boolean }) {
  return (
    <ReadPageUiProvider>
      <SetContent value={hasContent} />
      <ReadPageHero />
    </ReadPageUiProvider>
  );
}

function SetContent({ value }: { value: boolean }) {
  const { setHasContent } = useReadPageUi();
  useEffect(() => {
    setHasContent(value);
  }, [setHasContent, value]);
  return null;
}

describe("ReadPageHero 收起", () => {
  afterEach(cleanup);

  it("无内容时展示完整 Hero（双行标题 + 副标题）", () => {
    render(<HeroWithState hasContent={false} />);
    const hero = screen.getByTestId("read-page-hero");
    expect(hero.getAttribute("data-collapsed")).toBe("false");
    expect(hero.textContent).toContain("Read It Deeply.");
    expect(hero.textContent).toContain("从粘贴开始，进入深度阅读。");
  });

  it("有内容后收起为单行眉题，副标题退场", () => {
    render(<HeroWithState hasContent={true} />);
    const hero = screen.getByTestId("read-page-hero");
    expect(hero.getAttribute("data-collapsed")).toBe("true");
    expect(hero.textContent).toContain("Bring it to Claread.");
    expect(hero.textContent).not.toContain("Read It Deeply.");
  });

  it("reduced-motion 降级：Hero 过渡带 motion-reduce:transition-none", () => {
    render(<HeroWithState hasContent={true} />);
    const hero = screen.getByTestId("read-page-hero");
    expect(hero.className).toContain("motion-reduce:transition-none");
    expect(hero.className).toContain("duration-200");
  });

  it("useReadPageUi 无 Provider 时返回 no-op（表单可独立渲染）", () => {
    function Probe() {
      const { hasContent, setHasContent } = useReadPageUi();
      setHasContent(true); // must not throw
      return <span>{hasContent ? "yes" : "no"}</span>;
    }
    render(<Probe />);
    expect(screen.getByText("no")).toBeTruthy();
  });
});
