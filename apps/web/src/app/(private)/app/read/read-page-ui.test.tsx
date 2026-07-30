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

describe("ReadPageHero 同一身份收缩", () => {
  afterEach(cleanup);

  it("无内容时展示完整 Hero（双行标题 + 副标题）", () => {
    render(<HeroWithState hasContent={false} />);
    const hero = screen.getByTestId("read-page-hero");
    const title = screen.getByTestId("read-page-hero-title");
    const detail = screen.getByTestId("read-page-hero-detail");
    expect(hero.getAttribute("data-collapsed")).toBe("false");
    expect(hero.textContent).toContain("Paste to Begin");
    expect(title.textContent).toBe("Bring it to Claread.");
    expect(title.className).toContain("text-5xl");
    expect(detail.getAttribute("aria-hidden")).toBe("false");
    expect(detail.textContent).toContain("Read It Deeply.");
    expect(detail.textContent).toContain("从粘贴开始，进入深度阅读。");
  });

  it("有内容后收缩为同一品牌身份，不再切换成另一个标题", () => {
    render(<HeroWithState hasContent={true} />);
    const hero = screen.getByTestId("read-page-hero");
    const title = screen.getByTestId("read-page-hero-title");
    const detail = screen.getByTestId("read-page-hero-detail");
    expect(hero.getAttribute("data-collapsed")).toBe("true");
    // 同一身份：眉题与主标题保留，只是缩小。
    expect(hero.textContent).toContain("Paste to Begin");
    expect(title.textContent).toBe("Bring it to Claread.");
    expect(title.className).toContain("text-xl");
    expect(title.className).not.toContain("text-5xl");
    // 旧的身份突变标题不得回归。
    expect(hero.textContent).not.toContain("准备阅读材料");
    // 副标题与辅助文案收起并对辅助技术隐藏。
    expect(detail.getAttribute("aria-hidden")).toBe("true");
    expect(detail.className).toContain("opacity-0");
  });

  it("联合过渡合同：250ms 窗口 + reduced-motion 全部关闭", () => {
    render(<HeroWithState hasContent={false} />);
    const hero = screen.getByTestId("read-page-hero");
    const title = screen.getByTestId("read-page-hero-title");
    const detail = screen.getByTestId("read-page-hero-detail");
    // font-size / grid-rows / opacity / translate 联合过渡，时长落在
    // 220-280ms 设计窗口（250ms）。
    expect(title.className).toContain("duration-[250ms]");
    expect(title.className).toContain("motion-reduce:transition-none");
    expect(hero.querySelector(".grid")?.className).toContain("duration-[250ms]");
    expect(hero.querySelector(".grid")?.className).toContain(
      "motion-reduce:transition-none",
    );
    expect(detail.className).toContain("motion-reduce:transform-none");
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
