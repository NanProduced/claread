/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
	useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
	useSearchParams: () => new URLSearchParams(),
}));

import LoginPage from "./page";

beforeEach(() => {
	vi.stubGlobal(
		"fetch",
		vi.fn(async () =>
			new Response(JSON.stringify({ ok: true, step: "idle" }), {
				status: 200,
				headers: { "content-type": "application/json" },
			}),
		),
	);
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe("Login page shell", () => {
	it("presents only the email entry state in production", async () => {
		render(<LoginPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "登录 Claread" })).toBeTruthy();
		});
		expect(screen.getByLabelText("邮箱地址")).toBeTruthy();
		expect(screen.queryByLabelText("密码")).toBeNull();
		expect(screen.queryByLabelText("第 1 位，共 6 位")).toBeNull();
	});

	it("keeps the decorative panel desktop-only and the brand mobile-visible", async () => {
		const { container } = render(<LoginPage />);
		await waitFor(() => screen.getByRole("heading", { name: "登录 Claread" }));

		const panel = container.querySelector('[data-slot="auth-brand-panel"]');
		expect(panel).not.toBeNull();
		expect(panel?.className).toContain("hidden");
		expect(panel?.className).toContain("lg:flex");

		const mobileBrand = container.querySelector('[data-slot="auth-brand-mobile"]');
		expect(mobileBrand).not.toBeNull();
		expect(mobileBrand?.className).toContain("lg:hidden");
	});

	it("links back home and only fetches flow-status", async () => {
		render(<LoginPage />);
		await waitFor(() => screen.getByRole("link", { name: "首页" }));

		const home = screen.getByRole("link", { name: "首页" });
		expect(home.getAttribute("href")).toBe("/");
		expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
		const first = vi.mocked(fetch).mock.calls[0]?.[0];
		expect(String(first)).toContain("/api/web/auth/email/flow-status");
	});

	it("keeps mobile touch targets at least 44px tall", async () => {
		render(<LoginPage />);
		await waitFor(() => screen.getByRole("button", { name: "使用邮箱继续" }));

		expect(screen.getByRole("link", { name: "首页" }).className).toContain("max-md:min-h-11");
		expect(screen.getByRole("button", { name: "使用邮箱继续" }).className).toContain(
			"max-md:min-h-11",
		);
	});
});
