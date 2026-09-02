/** @vitest-environment jsdom */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApertureWatermark, BrandLockup, ClareadStamp } from "./BrandMarks";

afterEach(cleanup);

describe("Claread 品牌标识", () => {
  it("为浅色与深色主题提供对应的横版标识和光圈物料", () => {
    const { container } = render(
      <>
        <BrandLockup href={null} />
        <ApertureWatermark />
        <ClareadStamp />
      </>,
    );

    const images = Array.from(container.querySelectorAll("img"));
    const sources = images.map((image) => decodeURIComponent(image.getAttribute("src") ?? ""));

    expect(sources.some((source) => source.includes("/brand/claread-horizontal-bilingual.png"))).toBe(
      true,
    );
    expect(
      sources.some((source) =>
        source.includes("/brand/claread-horizontal-bilingual-reversed.png"),
      ),
    ).toBe(true);
    expect(sources.some((source) => source.includes("/brand/claread-icon-fullcolor.png"))).toBe(
      true,
    );
    expect(sources.some((source) => source.includes("/brand/claread-icon-reversed.png"))).toBe(
      true,
    );

    for (const image of images.filter((candidate) =>
      decodeURIComponent(candidate.getAttribute("src") ?? "").includes("reversed"),
    )) {
      expect(image.className).toContain("hidden");
      expect(image.className).toContain("dark:block");
    }
  });
});
