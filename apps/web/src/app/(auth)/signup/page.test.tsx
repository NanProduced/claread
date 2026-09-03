/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigationMock = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
	useRouter: () => ({ push: navigationMock.push, replace: vi.fn(), refresh: vi.fn() }),
	useSearchParams: () => new URLSearchParams(),
}));

import SignupPage from "./page";

beforeEach(() => {
	navigationMock.push.mockReset();
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

describe("Signup page shell", () => {
	it("presents registration as one focused task", async () => {
		render(<SignupPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "创建 Claread 账号" })).toBeTruthy();
		});
		expect(screen.getByRole("button", { name: "发送验证码" })).toBeTruthy();
		expect(screen.queryByRole("radiogroup")).toBeNull();
		expect(screen.getByText("已有账号？")).toBeTruthy();
	});

	it("links back to the dedicated login route", async () => {
		const user = userEvent.setup();
		render(<SignupPage />);
		await waitFor(() => screen.getByRole("button", { name: "登录" }));
		await user.click(screen.getByRole("button", { name: "登录" }));
		expect(navigationMock.push).toHaveBeenCalledWith("/login");
	});
});
