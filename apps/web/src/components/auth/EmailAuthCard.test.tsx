/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmailAuthCard } from "./EmailAuthCard";

afterEach(cleanup);

describe("EmailAuthCard — 邮箱入口", () => {
	it("renders login as one focused task without an auth-mode selector", () => {
		const { container } = render(<EmailAuthCard mode="email" />);

		expect(screen.getByRole("heading", { name: "登录 Claread" })).toBeTruthy();
		expect(screen.getByLabelText("邮箱地址")).toBeTruthy();
		expect(screen.getByPlaceholderText("you@example.com")).toBeTruthy();
		expect(container.querySelector('[data-slot="input-group-addon"]')).toBeNull();
		expect(screen.queryByRole("radiogroup")).toBeNull();
		expect(screen.queryByRole("radio")).toBeNull();

		const google = screen.getByRole("button", { name: "使用 Google 登录" });
		expect(google.hasAttribute("disabled")).toBe(true);
		expect(screen.getByText("Google 登录暂不可用。")).toBeTruthy();

		expect(screen.getByRole("button", { name: "注册" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "服务条款" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "隐私政策" })).toBeTruthy();
	});

	it("emits onSubmitEmail with a valid email and the login intent by default", async () => {
		const user = userEvent.setup();
		const onSubmitEmail = vi.fn();
		render(<EmailAuthCard mode="email" onSubmitEmail={onSubmitEmail} />);

		await user.type(screen.getByLabelText("邮箱地址"), "reader@example.com");
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));

		expect(onSubmitEmail).toHaveBeenCalledTimes(1);
		expect(onSubmitEmail).toHaveBeenCalledWith("reader@example.com", "login");
	});

	it("blocks an invalid email with a local announcement", async () => {
		const user = userEvent.setup();
		const onSubmitEmail = vi.fn();
		render(<EmailAuthCard mode="email" onSubmitEmail={onSubmitEmail} />);

		await user.type(screen.getByLabelText("邮箱地址"), "not-an-email");
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));

		expect(onSubmitEmail).not.toHaveBeenCalled();
		expect(screen.getByRole("alert").textContent).toContain("有效的邮箱地址");
	});

	it("renders signup as one focused task and emits route-switch and legal actions", async () => {
		const user = userEvent.setup();
		const onSubmitEmail = vi.fn();
		const onSwitchIntent = vi.fn();
		const onLegalLink = vi.fn();
		render(
			<EmailAuthCard
				mode="email"
				intent="register"
				onSubmitEmail={onSubmitEmail}
				onSwitchIntent={onSwitchIntent}
				onLegalLink={onLegalLink}
			/>,
		);

		expect(screen.getByRole("heading", { name: "创建 Claread 账号" })).toBeTruthy();
		expect(screen.getByText("输入邮箱地址，我们将发送 6 位验证码。")).toBeTruthy();
		expect(screen.queryByRole("radiogroup")).toBeNull();
		expect(screen.getByText("已有账号？")).toBeTruthy();

		await user.type(screen.getByLabelText("邮箱地址"), "reader@example.com");
		await user.click(screen.getByRole("button", { name: "发送验证码" }));
		expect(onSubmitEmail).toHaveBeenCalledWith("reader@example.com", "register");

		await user.click(screen.getByRole("button", { name: "登录" }));
		expect(onSwitchIntent).toHaveBeenCalledWith("login");

		await user.click(screen.getByRole("button", { name: "服务条款" }));
		expect(onLegalLink).toHaveBeenCalledWith("terms");
		await user.click(screen.getByRole("button", { name: "隐私政策" }));
		expect(onLegalLink).toHaveBeenCalledWith("privacy");
	});

	it("loading keeps the action label and disables form and state switches", () => {
		render(<EmailAuthCard mode="email" loading />);

		const submit = screen.getByRole("button", { name: "使用邮箱继续" });
		expect(submit.hasAttribute("disabled")).toBe(true);
		expect(submit.getAttribute("aria-busy")).toBe("true");
		expect((screen.getByLabelText("邮箱地址") as HTMLInputElement).disabled).toBe(true);
		expect(screen.getByRole("button", { name: "注册" }).hasAttribute("disabled")).toBe(true);
		// 法律链接不是认证状态切换操作，loading 时保持可用。
		expect(screen.getByRole("button", { name: "服务条款" }).hasAttribute("disabled")).toBe(false);
	});

	it("announces an external error with the feedback-error token", () => {
		render(<EmailAuthCard mode="email" error="尝试次数过多，请稍后再试。" />);
		const alert = screen.getByRole("alert");
		expect(alert.textContent).toContain("尝试次数过多");
		expect(alert.className).toContain("text-feedback-error");
	});

	it("syncs a later email prop into the draft without remounting", () => {
		const { rerender } = render(<EmailAuthCard mode="forgot" email="" />);
		expect((screen.getByLabelText("邮箱地址") as HTMLInputElement).value).toBe("");
		rerender(<EmailAuthCard mode="forgot" email="reader@example.com" />);
		expect((screen.getByLabelText("邮箱地址") as HTMLInputElement).value).toBe(
			"reader@example.com",
		);
	});
});

describe("EmailAuthCard — 密码登录", () => {
	it("reveals and hides the password without submitting", async () => {
		const user = userEvent.setup();
		const onSubmitPassword = vi.fn();
		render(
			<EmailAuthCard mode="password" email="reader@example.com" onSubmitPassword={onSubmitPassword} />,
		);

		const password = screen.getByLabelText("密码") as HTMLInputElement;
		const reveal = screen.getByRole("button", { name: "显示密码" });
		expect(password.type).toBe("password");
		expect(reveal.getAttribute("aria-pressed")).toBe("false");

		await user.click(reveal);
		expect(password.type).toBe("text");
		expect(screen.getByRole("button", { name: "隐藏密码" }).getAttribute("aria-pressed")).toBe(
			"true",
		);
		expect(onSubmitPassword).not.toHaveBeenCalled();

		await user.click(screen.getByRole("button", { name: "隐藏密码" }));
		expect(password.type).toBe("password");
	});

	it("shows the account email and emits the password", async () => {
		const user = userEvent.setup();
		const onSubmitPassword = vi.fn();
		render(
			<EmailAuthCard mode="password" email="reader@example.com" onSubmitPassword={onSubmitPassword} />,
		);

		expect(screen.getByRole("heading", { name: "欢迎回来" })).toBeTruthy();
		expect(screen.getByText("reader@example.com")).toBeTruthy();

		await user.type(screen.getByLabelText("密码"), "correct-horse-88");
		await user.click(screen.getByRole("button", { name: "登录" }));

		expect(onSubmitPassword).toHaveBeenCalledTimes(1);
		expect(onSubmitPassword).toHaveBeenCalledWith("correct-horse-88");
	});

	it("requires a password and routes forgot/back actions", async () => {
		const user = userEvent.setup();
		const onSubmitPassword = vi.fn();
		const onForgotPassword = vi.fn();
		const onBackToEmail = vi.fn();
		render(
			<EmailAuthCard
				mode="password"
				email="reader@example.com"
				onSubmitPassword={onSubmitPassword}
				onForgotPassword={onForgotPassword}
				onBackToEmail={onBackToEmail}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "登录" }));
		expect(onSubmitPassword).not.toHaveBeenCalled();
		expect(screen.getByRole("alert").textContent).toContain("请输入密码");

		await user.click(screen.getByRole("button", { name: "忘记密码？" }));
		expect(onForgotPassword).toHaveBeenCalledTimes(1);

		await user.click(screen.getByRole("button", { name: "使用其他邮箱" }));
		expect(onBackToEmail).toHaveBeenCalledTimes(1);
	});

	it("disables state-switching actions while loading", () => {
		render(<EmailAuthCard mode="password" email="reader@example.com" loading />);

		expect(screen.getByRole("button", { name: "忘记密码？" }).hasAttribute("disabled")).toBe(true);
		expect(screen.getByRole("button", { name: "使用其他邮箱" }).hasAttribute("disabled")).toBe(true);
	});
});

describe("EmailAuthCard — 邮箱验证码", () => {
	it("keeps register OTP copy specific to the entered email", () => {
		render(
			<EmailAuthCard mode="otp" email="reader@example.com" otpFlow="register" />,
		);

		expect(screen.getByText("我们已向 reader@example.com 发送 6 位验证码。"))
			.toBeTruthy();
	});

	it("uses an account-enumeration-safe copy for password reset OTP", () => {
		render(
			<EmailAuthCard mode="otp" email="reader@example.com" otpFlow="password_reset" />,
		);

		expect(
			screen.getByText("如果该邮箱已注册且邮件服务可用，你将收到 6 位验证码。"),
		).toBeTruthy();
		expect(screen.queryByText(/reader@example\.com/)).toBeNull();
	});

	it("auto-submits a completed 6-digit code", async () => {
		const user = userEvent.setup();
		const onSubmitOtp = vi.fn();
		render(<EmailAuthCard mode="otp" email="reader@example.com" onSubmitOtp={onSubmitOtp} />);

		expect(screen.getByRole("heading", { name: "查看你的邮箱" })).toBeTruthy();
		expect(screen.getByText(/reader@example\.com/)).toBeTruthy();

		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("483920");

		expect(onSubmitOtp).toHaveBeenCalledTimes(1);
		expect(onSubmitOtp).toHaveBeenCalledWith("483920");
	});

	it("submits the same code at most once until it changes or an error arrives", async () => {
		const user = userEvent.setup();
		const onSubmitOtp = vi.fn();
		const { rerender } = render(<EmailAuthCard mode="otp" onSubmitOtp={onSubmitOtp} />);

		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("483920");
		expect(onSubmitOtp).toHaveBeenCalledTimes(1);

		// 手动验证同一验证码：不再重复提交。
		await user.click(screen.getByRole("button", { name: "验证" }));
		expect(onSubmitOtp).toHaveBeenCalledTimes(1);

		// 修改验证码后允许重新提交。
		await user.click(screen.getByLabelText("第 6 位，共 6 位"));
		await user.keyboard("{Backspace}");
		await user.keyboard("1");
		expect(onSubmitOtp).toHaveBeenCalledTimes(2);
		expect(onSubmitOtp).toHaveBeenLastCalledWith("483921");

		// 外部错误到达后允许重试同一验证码。
		rerender(<EmailAuthCard mode="otp" error="验证码已过期。" onSubmitOtp={onSubmitOtp} />);
		await user.click(screen.getByRole("button", { name: "验证" }));
		expect(onSubmitOtp).toHaveBeenCalledTimes(3);
		expect(onSubmitOtp).toHaveBeenLastCalledWith("483921");
	});

	it("requires six digits before a manual verify", async () => {
		const user = userEvent.setup();
		const onSubmitOtp = vi.fn();
		render(<EmailAuthCard mode="otp" onSubmitOtp={onSubmitOtp} />);

		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("483");
		await user.click(screen.getByRole("button", { name: "验证" }));

		expect(onSubmitOtp).not.toHaveBeenCalled();
		expect(screen.getByRole("alert").textContent).toContain("6 位验证码");
	});

	it("links the Chinese error to every cell via aria-describedby", () => {
		render(<EmailAuthCard mode="otp" error="验证码不正确，请重新输入。" />);

		expect(screen.getByRole("alert").id).toBe("email-auth-status");
		for (let index = 1; index <= 6; index += 1) {
			expect(screen.getByLabelText(`第 ${index} 位，共 6 位`).getAttribute("aria-describedby")).toBe(
				"email-auth-status",
			);
		}
	});

	it("shows the resend cooldown and blocks resend while it runs", () => {
		render(<EmailAuthCard mode="otp" cooldownSeconds={45} />);

		const resend = screen.getByRole("button", { name: "45 秒后可重发" });
		expect(resend.hasAttribute("disabled")).toBe(true);
	});

	it("displays cooldown from props without an internal timer", () => {
		vi.useFakeTimers();
		const { rerender } = render(<EmailAuthCard mode="otp" cooldownSeconds={45} />);
		expect(screen.getByRole("button", { name: "45 秒后可重发" })).toBeTruthy();
		vi.advanceTimersByTime(2000);
		expect(screen.getByRole("button", { name: "45 秒后可重发" })).toBeTruthy();
		rerender(<EmailAuthCard mode="otp" cooldownSeconds={73} />);
		expect(screen.getByRole("button", { name: "73 秒后可重发" })).toBeTruthy();
		vi.useRealTimers();
	});

	it("enables resend once no cooldown remains", async () => {
		const user = userEvent.setup();
		const onResendOtp = vi.fn();
		render(<EmailAuthCard mode="otp" cooldownSeconds={0} onResendOtp={onResendOtp} />);

		await user.click(screen.getByRole("button", { name: "重新发送" }));
		expect(onResendOtp).toHaveBeenCalledTimes(1);
	});

	it("labels the register OTP back action as changing email", async () => {
		const user = userEvent.setup();
		const onBackToEmail = vi.fn();
		render(<EmailAuthCard mode="otp" otpFlow="register" onBackToEmail={onBackToEmail} />);

		await user.click(screen.getByRole("button", { name: "更换邮箱" }));
		expect(onBackToEmail).toHaveBeenCalledTimes(1);
		expect(screen.queryByRole("button", { name: "返回登录" })).toBeNull();
	});

	it("disables resend and back while loading", () => {
		render(<EmailAuthCard mode="otp" cooldownSeconds={0} loading />);

		expect(screen.getByRole("button", { name: "重新发送" }).hasAttribute("disabled")).toBe(true);
		expect(screen.getByRole("button", { name: "返回登录" }).hasAttribute("disabled")).toBe(true);
	});

	it("clears password, confirm, otp, local error and submit guard when mode changes", async () => {
		const user = userEvent.setup();
		const onSubmitOtp = vi.fn();
		const { rerender } = render(<EmailAuthCard mode="email" />);

		await user.type(screen.getByLabelText("邮箱地址"), "not-an-email");
		await user.click(screen.getByRole("button", { name: "使用邮箱继续" }));
		expect(screen.getByRole("alert").textContent).toContain("有效的邮箱地址");

		rerender(<EmailAuthCard mode="password" email="reader@example.com" />);
		expect(screen.queryByRole("alert")).toBeNull();
		await user.type(screen.getByLabelText("密码"), "stale-password");

		rerender(
			<EmailAuthCard
				mode="set-password"
				email="reader@example.com"
				onSubmitSetPassword={vi.fn()}
			/>,
		);
		expect((screen.getByLabelText("新密码") as HTMLInputElement).value).toBe("");
		expect((screen.getByLabelText("确认密码") as HTMLInputElement).value).toBe("");

		rerender(<EmailAuthCard mode="otp" email="reader@example.com" onSubmitOtp={onSubmitOtp} />);
		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("483920");
		expect(onSubmitOtp).toHaveBeenCalledTimes(1);

		rerender(<EmailAuthCard mode="password" email="reader@example.com" />);
		expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");

		rerender(<EmailAuthCard mode="otp" email="reader@example.com" onSubmitOtp={onSubmitOtp} />);
		expect((screen.getByLabelText("第 1 位，共 6 位") as HTMLInputElement).value).toBe("");
		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("483920");
		expect(onSubmitOtp).toHaveBeenCalledTimes(2);
	});
});

describe("EmailAuthCard — 设置密码", () => {
	it("reveals the new and confirmation passwords independently", async () => {
		const user = userEvent.setup();
		render(<EmailAuthCard mode="set-password" />);

		const newPassword = screen.getByLabelText("新密码") as HTMLInputElement;
		const confirmation = screen.getByLabelText("确认密码") as HTMLInputElement;
		await user.click(screen.getByRole("button", { name: "显示新密码" }));

		expect(newPassword.type).toBe("text");
		expect(confirmation.type).toBe("password");

		await user.click(screen.getByRole("button", { name: "显示确认密码" }));
		expect(newPassword.type).toBe("text");
		expect(confirmation.type).toBe("text");
	});

	it("shows the password requirement next to the field", () => {
		render(<EmailAuthCard mode="set-password" />);
		expect(screen.getByText("12–128 个字符")).toBeTruthy();
	});

	it("rejects passwords outside 12–128 code points", async () => {
		const user = userEvent.setup();
		const onSubmitSetPassword = vi.fn();
		render(<EmailAuthCard mode="set-password" onSubmitSetPassword={onSubmitSetPassword} />);

		await user.type(screen.getByLabelText("新密码"), "a".repeat(11));
		await user.type(screen.getByLabelText("确认密码"), "a".repeat(11));
		await user.click(screen.getByRole("button", { name: "设置密码" }));
		expect(onSubmitSetPassword).not.toHaveBeenCalled();
		expect(screen.getByRole("alert").textContent).toContain("12–128 个字符");
	});

	it("rejects passwords beyond 128 code points", async () => {
		const user = userEvent.setup();
		const onSubmitSetPassword = vi.fn();
		render(<EmailAuthCard mode="set-password" onSubmitSetPassword={onSubmitSetPassword} />);

		const tooLong = "a".repeat(129);
		await user.type(screen.getByLabelText("新密码"), tooLong);
		await user.type(screen.getByLabelText("确认密码"), tooLong);
		await user.click(screen.getByRole("button", { name: "设置密码" }));
		expect(onSubmitSetPassword).not.toHaveBeenCalled();
		expect(screen.getByRole("alert").textContent).toContain("12–128 个字符");
	});

	it("counts NFC code points and emits the normalized password", async () => {
		const user = userEvent.setup();
		const onSubmitSetPassword = vi.fn();
		render(<EmailAuthCard mode="set-password" onSubmitSetPassword={onSubmitSetPassword} />);

		// 11 个 ASCII + 分解形式 e+U+0301：原始 13 个 code points，NFC 后 12 个。
		const decomposed = `${"x".repeat(11)}é`;
		const normalized = `${"x".repeat(11)}é`;
		await user.type(screen.getByLabelText("新密码"), decomposed);
		await user.type(screen.getByLabelText("确认密码"), decomposed);
		await user.click(screen.getByRole("button", { name: "设置密码" }));

		expect(onSubmitSetPassword).toHaveBeenCalledTimes(1);
		expect(onSubmitSetPassword).toHaveBeenCalledWith(normalized);
		expect([...decomposed].length).toBe(13);
		expect([...normalized].length).toBe(12);
	});

	it("requires matching passwords", async () => {
		const user = userEvent.setup();
		const onSubmitSetPassword = vi.fn();
		render(<EmailAuthCard mode="set-password" onSubmitSetPassword={onSubmitSetPassword} />);

		await user.type(screen.getByLabelText("新密码"), "pineapple-7777");
		await user.type(screen.getByLabelText("确认密码"), "pineapple-8888");
		await user.click(screen.getByRole("button", { name: "设置密码" }));

		expect(onSubmitSetPassword).not.toHaveBeenCalled();
		expect(screen.getByRole("alert").textContent).toContain("两次输入的密码不一致");
	});
});

describe("EmailAuthCard — 忘记密码", () => {
	it("offers a 6-digit code, never a reset link", async () => {
		const user = userEvent.setup();
		const onSubmitForgot = vi.fn();
		render(<EmailAuthCard mode="forgot" email="reader@example.com" onSubmitForgot={onSubmitForgot} />);

		expect(screen.getByRole("heading", { name: "重置密码" })).toBeTruthy();
		expect(screen.getByText(/6 位验证码/)).toBeTruthy();
		expect(screen.queryByText(/reset link/i)).toBeNull();
		expect((screen.getByLabelText("邮箱地址") as HTMLInputElement).value).toBe(
			"reader@example.com",
		);

		await user.click(screen.getByRole("button", { name: "发送验证码" }));
		expect(onSubmitForgot).toHaveBeenCalledTimes(1);
		expect(onSubmitForgot).toHaveBeenCalledWith("reader@example.com");
	});

	it("offers a way back to sign in", async () => {
		const user = userEvent.setup();
		const onBackToEmail = vi.fn();
		render(<EmailAuthCard mode="forgot" onBackToEmail={onBackToEmail} />);

		await user.click(screen.getByRole("button", { name: "返回登录" }));
		expect(onBackToEmail).toHaveBeenCalledTimes(1);
	});
});

describe("EmailAuthCard — 重置密码", () => {
	it("emits a matching new password", async () => {
		const user = userEvent.setup();
		const onSubmitReset = vi.fn();
		render(<EmailAuthCard mode="reset" onSubmitReset={onSubmitReset} />);

		expect(screen.getByRole("heading", { name: "设置新密码" })).toBeTruthy();

		await user.type(screen.getByLabelText("新密码"), "pineapple-7777");
		await user.type(screen.getByLabelText("确认密码"), "pineapple-7777");
		await user.click(screen.getByRole("button", { name: "重置密码" }));

		expect(onSubmitReset).toHaveBeenCalledTimes(1);
		expect(onSubmitReset).toHaveBeenCalledWith("pineapple-7777");
	});

	it("keeps external errors announced in reset mode", () => {
		render(<EmailAuthCard mode="reset" error="验证码已过期，请重新获取。" />);
		expect(screen.getByRole("alert").textContent).toContain("验证码已过期");
	});
});

describe("EmailAuthCard — 重置成功", () => {
	it("announces the reset success state and emits immediate entry", async () => {
		const user = userEvent.setup();
		const onResetSuccessContinue = vi.fn();
		render(
			<EmailAuthCard
				mode="reset-success"
				onResetSuccessContinue={onResetSuccessContinue}
			/>,
		);

		expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
		expect(screen.getByRole("heading", { name: "密码已重置" })).toBeTruthy();
		expect(
			screen.getByText("新密码已生效，你已安全登录。"),
		).toBeTruthy();
		expect(screen.queryByText(/正在进入/)).toBeNull();

		await user.click(screen.getByRole("button", { name: "立即进入" }));
		expect(onResetSuccessContinue).toHaveBeenCalledTimes(1);
	});
});
