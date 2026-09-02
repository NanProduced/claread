/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/brand/BrandMarks", () => ({
  BrandLockup: ({ href }: { href: string | null }) =>
    href ? <a href={href}>Claread</a> : <span>Claread</span>,
}));
vi.mock("./ProductStickerWall", () => ({
  ProductStickerWall: () => <div data-testid="sticker-wall" />,
}));

import { privacyRoute, termsRoute } from "@/lib/routes";
import { ProductFooter } from "./ProductFooter";

afterEach(cleanup);

describe("公开站 Footer", () => {
  it("把法律链接指向真实页面路由，而不是 hash", () => {
    render(<ProductFooter />);

    expect(screen.getByRole("link", { name: "隐私政策" }).getAttribute("href")).toBe(
      privacyRoute,
    );
    expect(screen.getByRole("link", { name: "服务条款" }).getAttribute("href")).toBe(
      termsRoute,
    );
    expect(screen.getByRole("link", { name: "隐私政策" }).getAttribute("href")).not.toContain("#");
    expect(screen.getByRole("link", { name: "服务条款" }).getAttribute("href")).not.toContain("#");
  });
});
