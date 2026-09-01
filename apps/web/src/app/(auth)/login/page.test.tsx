/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./page";

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe("Login page shell", () => {
	it("presents only the email entry state in production", () => {
		render(<LoginPage />);

		expect(screen.getByRole("heading", { name: "登录或创建账号" })).toBeTruthy();
		expect(screen.getByLabelText("邮箱地址")).toBeTruthy();
		expect(screen.queryByLabelText("密码")).toBeNull();
		expect(screen.queryByLabelText("第 1 位，共 6 位")).toBeNull();
	});

	it("keeps the decorative panel desktop-only and the brand mobile-visible", () => {
		const { container } = render(<LoginPage />);

		const panel = container.querySelector('[data-slot="auth-brand-panel"]');
		expect(panel).not.toBeNull();
		expect(panel?.className).toContain("hidden");
		expect(panel?.className).toContain("lg:flex");

		const mobileBrand = container.querySelector('[data-slot="auth-brand-mobile"]');
		expect(mobileBrand).not.toBeNull();
		expect(mobileBrand?.className).toContain("lg:hidden");
	});

	it("links back home and never fetches", () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal("fetch", fetchSpy);

		render(<LoginPage />);

		const home = screen.getByRole("link", { name: "首页" });
		expect(home.getAttribute("href")).toBe("/");
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it("keeps mobile touch targets at least 44px tall", () => {
		render(<LoginPage />);

		expect(screen.getByRole("link", { name: "首页" }).className).toContain("max-md:min-h-11");
		expect(screen.getByRole("button", { name: "使用邮箱继续" }).className).toContain(
			"max-md:min-h-11",
		);
	});
});
