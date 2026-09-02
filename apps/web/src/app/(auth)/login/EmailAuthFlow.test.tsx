/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigationMock = vi.hoisted(() => ({
	push: vi.fn(),
	replace: vi.fn(),
	refresh: vi.fn(),
	search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
	useRouter: () => ({
		push: navigationMock.push,
		replace: navigationMock.replace,
		refresh: navigationMock.refresh,
	}),
	useSearchParams: () => navigationMock.search,
}));

import { EmailAuthFlow } from "./EmailAuthFlow";

const EMAIL = "reader@example.com";
const PASSWORD = "correct horse battery staple";
const CODE = "123456";

function jsonResponse(body: unknown, init: { status?: number; headers?: HeadersInit } = {}) {
	return new Response(JSON.stringify(body), {
		status: init.status ?? 200,
		headers: { "content-type": "application/json", ...init.headers },
	});
}

function mockFetchByUrl(
	handlers: Record<string, (request: Request) => Promise<Response> | Response>,
) {
	return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
		const pathname = new URL(raw, "http://localhost").pathname;
		const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
		const request = input instanceof Request ? input : new Request(new URL(raw, "http://localhost"), init);
		const handler = handlers[`${method} ${pathname}`];
		if (!handler) {
			throw new Error(`unexpected fetch ${method} ${pathname}`);
		}
		return handler(request);
	});
}

function pasteOtp(code: string) {
	fireEvent.paste(screen.getByRole("group", { name: "一次性验证码" }), {
		clipboardData: { getData: () => code },
	} as unknown as ClipboardEvent);
}

function assertNoCredentials(value: unknown) {
	const dumped = JSON.stringify(value);
	expect(dumped).not.toContain(PASSWORD);
	expect(dumped).not.toContain(CODE);
	expect(dumped).not.toMatch(/challenge_id|session_token|"ticket"/);
}

describe("EmailAuthFlow", () => {
	const logs: unknown[] = [];
	let storageSet: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		logs.length = 0;
		navigationMock.push.mockReset();
		navigationMock.replace.mockReset();
		navigationMock.refresh.mockReset();
		navigationMock.search = new URLSearchParams();
		storageSet = vi.fn();
		vi.spyOn(console, "info").mockImplementation((...args) => {
			logs.push(args);
		});
		vi.spyOn(console, "error").mockImplementation((...args) => {
			logs.push(args);
		});
		vi.spyOn(console, "warn").mockImplementation((...args) => {
			logs.push(args);
		});
		const storage = {
			getItem: vi.fn(),
			setItem: storageSet,
			removeItem: vi.fn(),
			clear: vi.fn(),
			key: vi.fn(),
			length: 0,
		};
		vi.stubGlobal("localStorage", storage);
		vi.stubGlobal("sessionStorage", { ...storage, setItem: storageSet });
	});

	afterEach(() => {
		cleanup();
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
		vi.useRealTimers();
	});

	it("restores idle from flow-status", async () => {
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "登录或创建账号" })).toBeTruthy();
		});
	});

	it("restores otp from flow-status", async () => {
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () =>
					jsonResponse({
						ok: true,
						step: "otp",
						flow: "register",
						email: EMAIL,
						resend_after: 41,
					}),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "查看你的邮箱" })).toBeTruthy();
		});
		expect(screen.getByText(new RegExp(EMAIL))).toBeTruthy();
		expect(screen.getByRole("button", { name: "41 秒后可重发" })).toBeTruthy();
	});

	it("restores set-password from flow-status", async () => {
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () =>
					jsonResponse({ ok: true, step: "set-password", email: EMAIL }),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "设置密码" })).toBeTruthy();
		});
	});

	it("restores reset from flow-status", async () => {
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () =>
					jsonResponse({ ok: true, step: "reset", email: EMAIL }),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "设置新密码" })).toBeTruthy();
		});
	});

	it("logs in with password and refreshes before a safe redirect", async () => {
		const user = userEvent.setup();
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () => jsonResponse({ ok: true, mode: "password" }),
			"POST /api/web/auth/email/password/login": async () => jsonResponse({ ok: true }),
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByLabelText("密码"));
		await user.type(screen.getByLabelText("密码"), PASSWORD);
		await user.click(screen.getByRole("button", { name: "登录" }));
		await waitFor(() => {
			expect(navigationMock.refresh).toHaveBeenCalled();
			expect(navigationMock.push).toHaveBeenCalledWith("/app/read");
		});
		expect(navigationMock.refresh.mock.invocationCallOrder[0]).toBeLessThan(
			navigationMock.push.mock.invocationCallOrder[0],
		);
		assertNoCredentials(logs);
		expect(storageSet).not.toHaveBeenCalled();
	});

	it("walks register → OTP → set-password → redirect", async () => {
		const user = userEvent.setup();
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () =>
				jsonResponse({ ok: true, mode: "register", resend_after: 73 }),
			"POST /api/web/auth/email/otp/verify": async () =>
				jsonResponse({ ok: true, next: "set-password" }),
			"POST /api/web/auth/email/register": async () => jsonResponse({ ok: true }),
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("button", { name: "73 秒后可重发" }));
		pasteOtp(CODE);
		await waitFor(() => screen.getByLabelText("新密码"));
		await user.type(screen.getByLabelText("新密码"), PASSWORD);
		await user.type(screen.getByLabelText("确认密码"), PASSWORD);
		await user.click(screen.getByRole("button", { name: "设置密码" }));
		await waitFor(() => {
			expect(navigationMock.refresh).toHaveBeenCalled();
			expect(navigationMock.push).toHaveBeenCalledWith("/app/read");
		});
	});

	it("walks forgot → OTP → reset → redirect", async () => {
		const user = userEvent.setup();
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () => jsonResponse({ ok: true, mode: "password" }),
			"POST /api/web/auth/email/password-reset/request": async () =>
				jsonResponse({ ok: true, status: "accepted", resend_after: 19 }),
			"POST /api/web/auth/email/otp/verify": async () => jsonResponse({ ok: true, next: "reset" }),
			"POST /api/web/auth/email/password-reset/complete": async () => jsonResponse({ ok: true }),
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("button", { name: "忘记密码？" }));
		await user.click(screen.getByRole("button", { name: "忘记密码？" }));
		await user.click(screen.getByRole("button", { name: "发送验证码" }));
		await waitFor(() => screen.getByRole("button", { name: "19 秒后可重发" }));
		pasteOtp(CODE);
		await waitFor(() => screen.getByRole("button", { name: "重置密码" }));
		await user.type(screen.getByLabelText("新密码"), PASSWORD);
		await user.type(screen.getByLabelText("确认密码"), PASSWORD);
		await user.click(screen.getByRole("button", { name: "重置密码" }));
		await waitFor(() => {
			expect(navigationMock.refresh).toHaveBeenCalled();
			expect(navigationMock.push).toHaveBeenCalledWith("/app/read");
		});
	});

	it("applies 429 retry_after and resets cooldown when resend returns the same duration", async () => {
		const user = userEvent.setup();
		let startCount = 0;
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () => {
				startCount += 1;
				if (startCount === 2) {
					return jsonResponse(
						{
							ok: false,
							message: "发送过于频繁，请稍后再试。",
							code: "email_cooldown",
							retry_after: 1,
						},
						{ status: 429, headers: { "Retry-After": "1" } },
					);
				}
				return jsonResponse({ ok: true, mode: "register", resend_after: 1 });
			},
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("button", { name: "1 秒后可重发" }));
		await waitFor(() => screen.getByRole("button", { name: "重新发送" }), { timeout: 2500 });
		await user.click(screen.getByRole("button", { name: "重新发送" }));
		await waitFor(() => screen.getByRole("button", { name: "1 秒后可重发" }));
		expect(screen.getByRole("alert").textContent).toContain("过于频繁");
		await waitFor(() => screen.getByRole("button", { name: "重新发送" }), { timeout: 2500 });
		await user.click(screen.getByRole("button", { name: "重新发送" }));
		await waitFor(() => screen.getByRole("button", { name: "1 秒后可重发" }));
	}, 10_000);

	it("cancels back to email and ignores duplicate submits", async () => {
		const user = userEvent.setup();
		let startCalls = 0;
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () => {
				startCalls += 1;
				await new Promise((resolve) => setTimeout(resolve, 50));
				return jsonResponse({ ok: true, mode: "password" });
			},
			"POST /api/web/auth/email/cancel": async () => jsonResponse({ ok: true }),
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("button", { name: "忘记密码？" }));
		expect(startCalls).toBe(1);
		await user.click(screen.getByRole("button", { name: "使用其他邮箱" }));
		await waitFor(() => screen.getByRole("heading", { name: "登录或创建账号" }));
	});

	it("shows a safe error for non-JSON and malformed responses", async () => {
		const user = userEvent.setup();
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () =>
				new Response("<html>nope</html>", { status: 502, headers: { "content-type": "text/html" } }),
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("alert"));
		expect(screen.getByRole("alert").textContent).toMatch(/请稍后重试/);
		assertNoCredentials(logs);
		expect(JSON.stringify(logs)).not.toContain("<html>");
	});

	it("falls back to /app/read when next is hostile", async () => {
		const user = userEvent.setup();
		navigationMock.search = new URLSearchParams("next=https://evil.example/phish");
		const fetchMock = mockFetchByUrl({
			"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
			"POST /api/web/auth/email/start": async () => jsonResponse({ ok: true, mode: "password" }),
			"POST /api/web/auth/email/password/login": async () => jsonResponse({ ok: true }),
		});
		vi.stubGlobal("fetch", fetchMock);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByLabelText("密码"));
		await user.type(screen.getByLabelText("密码"), PASSWORD);
		await user.click(screen.getByRole("button", { name: "登录" }));
		await waitFor(() => {
			expect(navigationMock.push).toHaveBeenCalledWith("/app/read");
		});
		expect(navigationMock.push.mock.calls.flat().join(" ")).not.toContain("evil");
	});

	it("keeps the brand panel instance across email → password and resets card fields", async () => {
		const user = userEvent.setup();
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
				"POST /api/web/auth/email/start": async () => jsonResponse({ ok: true, mode: "password" }),
			}),
		);
		const { container } = render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		const panel = container.querySelector('[data-slot="auth-brand-panel"]');
		expect(panel).not.toBeNull();
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByLabelText("密码"));
		expect(container.querySelector('[data-slot="auth-brand-panel"]')).toBe(panel);
		expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
		expect(screen.queryByLabelText("邮箱地址")).toBeNull();
		expect(screen.queryByRole("alert")).toBeNull();
	});

	it.each([
		["missing ok", { step: "idle" }],
		["ok string", { ok: "true", step: "idle" }],
		["unknown step", { ok: true, step: "mystery" }],
		["otp missing flow", { ok: true, step: "otp", email: EMAIL, resend_after: 9 }],
		["otp unknown flow", { ok: true, step: "otp", flow: "login", email: EMAIL, resend_after: 9 }],
		["otp missing email", { ok: true, step: "otp", flow: "register", resend_after: 9 }],
		["otp email number", { ok: true, step: "otp", flow: "register", email: 1, resend_after: 9 }],
		["otp missing resend_after", { ok: true, step: "otp", flow: "register", email: EMAIL }],
		["otp resend_after string", { ok: true, step: "otp", flow: "register", email: EMAIL, resend_after: "73" }],
		["set-password missing email", { ok: true, step: "set-password" }],
		["reset email array", { ok: true, step: "reset", email: [EMAIL] }],
	] as const)("rejects malformed flow-status (%s) without leaving idle", async (_name, body) => {
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse(body),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByRole("alert"));
		expect(screen.getByRole("heading", { name: "登录或创建账号" })).toBeTruthy();
		expect(screen.getByRole("alert").textContent).toBe("登录暂时不可用，请稍后重试。");
		expect(screen.queryByRole("heading", { name: "查看你的邮箱" })).toBeNull();
		expect(screen.queryByRole("heading", { name: "设置密码" })).toBeNull();
		expect(navigationMock.refresh).not.toHaveBeenCalled();
		expect(navigationMock.push).not.toHaveBeenCalled();
	});

	it.each([
		["missing ok", { mode: "password" }],
		["missing mode", { ok: true }],
		["unknown mode", { ok: true, mode: "magic" }],
		["register missing resend_after", { ok: true, mode: "register" }],
		["register resend_after string", { ok: true, mode: "register", resend_after: "73" }],
	] as const)("rejects malformed start (%s) without switching mode", async (_name, body) => {
		const user = userEvent.setup();
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
				"POST /api/web/auth/email/start": async () => jsonResponse(body),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("alert"));
		expect(screen.getByRole("heading", { name: "登录或创建账号" })).toBeTruthy();
		expect(screen.getByRole("alert").textContent).toBe("登录暂时不可用，请稍后重试。");
		expect(screen.queryByLabelText("密码")).toBeNull();
		expect(screen.queryByRole("group", { name: "一次性验证码" })).toBeNull();
		expect(navigationMock.refresh).not.toHaveBeenCalled();
		expect(navigationMock.push).not.toHaveBeenCalled();
	});

	it.each([
		["missing next", { ok: true }],
		["unknown next", { ok: true, next: "home" }],
		["ok number", { ok: 1, next: "set-password" }],
	] as const)("rejects malformed otp verify (%s) without leaving otp", async (_name, body) => {
		const user = userEvent.setup();
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
				"POST /api/web/auth/email/start": async () =>
					jsonResponse({ ok: true, mode: "register", resend_after: 73 }),
				"POST /api/web/auth/email/otp/verify": async () => jsonResponse(body),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("heading", { name: "查看你的邮箱" }));
		pasteOtp(CODE);
		await waitFor(() => screen.getByRole("alert"));
		expect(screen.getByRole("heading", { name: "查看你的邮箱" })).toBeTruthy();
		expect(screen.getByRole("alert").textContent).toBe("登录暂时不可用，请稍后重试。");
		expect(screen.queryByLabelText("新密码")).toBeNull();
		expect(navigationMock.refresh).not.toHaveBeenCalled();
		expect(navigationMock.push).not.toHaveBeenCalled();
	});

	it.each([
		["missing status", { ok: true, resend_after: 19 }],
		["unknown status", { ok: true, status: "ok", resend_after: 19 }],
		["missing resend_after", { ok: true, status: "accepted" }],
	] as const)("rejects malformed reset request (%s) without leaving forgot", async (_name, body) => {
		const user = userEvent.setup();
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
				"POST /api/web/auth/email/start": async () => jsonResponse({ ok: true, mode: "password" }),
				"POST /api/web/auth/email/password-reset/request": async () => jsonResponse(body),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByRole("button", { name: "忘记密码？" }));
		await user.click(screen.getByRole("button", { name: "忘记密码？" }));
		await user.click(screen.getByRole("button", { name: "发送验证码" }));
		await waitFor(() => screen.getByRole("alert"));
		expect(screen.getByRole("heading", { name: "重置密码" })).toBeTruthy();
		expect(screen.getByRole("alert").textContent).toBe("登录暂时不可用，请稍后重试。");
		expect(screen.queryByRole("heading", { name: "查看你的邮箱" })).toBeNull();
		expect(navigationMock.refresh).not.toHaveBeenCalled();
		expect(navigationMock.push).not.toHaveBeenCalled();
	});

	it.each([
		["missing ok", {}],
		["ok number", { ok: 1 }],
	] as const)("rejects malformed session login (%s) without refresh or redirect", async (_name, body) => {
		const user = userEvent.setup();
		vi.stubGlobal(
			"fetch",
			mockFetchByUrl({
				"GET /api/web/auth/email/flow-status": () => jsonResponse({ ok: true, step: "idle" }),
				"POST /api/web/auth/email/start": async () => jsonResponse({ ok: true, mode: "password" }),
				"POST /api/web/auth/email/password/login": async () => jsonResponse(body),
			}),
		);
		render(<EmailAuthFlow />);
		await waitFor(() => screen.getByLabelText("邮箱地址"));
		await user.type(screen.getByLabelText("邮箱地址"), EMAIL);
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		await waitFor(() => screen.getByLabelText("密码"));
		await user.type(screen.getByLabelText("密码"), PASSWORD);
		await user.click(screen.getByRole("button", { name: "登录" }));
		await waitFor(() => screen.getByRole("alert"));
		expect(screen.getByRole("heading", { name: "欢迎回来" })).toBeTruthy();
		expect(screen.getByRole("alert").textContent).toBe("登录暂时不可用，请稍后重试。");
		expect(navigationMock.refresh).not.toHaveBeenCalled();
		expect(navigationMock.push).not.toHaveBeenCalled();
	});
});
