"use client";

import { useEffect, useRef, useState } from "react";
import { AtSign, CircleAlert, Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";

import { AuthDivider } from "./AuthDivider";
import { GoogleIcon } from "./GoogleIcon";
import { OtpInput } from "./OtpInput";

export type EmailAuthMode = "email" | "password" | "otp" | "set-password" | "forgot" | "reset";

export type EmailAuthCardProps = {
	mode: EmailAuthMode;
	email?: string;
	loading?: boolean;
	error?: string | null;
	cooldownSeconds?: number;
	onSubmitEmail?: (email: string) => void;
	onSubmitPassword?: (password: string) => void;
	onSubmitOtp?: (code: string) => void;
	onSubmitSetPassword?: (password: string) => void;
	onSubmitForgot?: (email: string) => void;
	onSubmitReset?: (password: string) => void;
	onResendOtp?: () => void;
	onBackToEmail?: () => void;
	onForgotPassword?: () => void;
	onSignUp?: () => void;
	onLegalLink?: (doc: "terms" | "privacy") => void;
};

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const STATUS_ID = "email-auth-status";
const PASSWORD_MIN_CODE_POINTS = 12;
const PASSWORD_MAX_CODE_POINTS = 128;

const FIELD_LABEL = "text-xs font-semibold text-muted-foreground";
const LINK_BUTTON =
	"focus-ring rounded-sm text-muted-foreground underline underline-offset-4 transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-muted-foreground/70 disabled:no-underline";

const MODE_COPY: Record<EmailAuthMode, (email: string) => { title: string; description: string }> = {
	email: () => ({
		title: "登录或创建账号",
		description: "输入邮箱地址，继续使用 Claread。",
	}),
	password: () => ({
		title: "欢迎回来",
		description: "输入密码以继续。",
	}),
	otp: (email) => ({
		title: "查看你的邮箱",
		description: email ? `我们已向 ${email} 发送 6 位验证码。` : "我们已向你的邮箱发送 6 位验证码。",
	}),
	"set-password": (email) => ({
		title: "设置密码",
		description: email ? `为 ${email} 设置登录密码。` : "为你的账号设置登录密码。",
	}),
	forgot: () => ({
		title: "重置密码",
		description: "输入邮箱地址，我们将发送 6 位验证码。",
	}),
	reset: () => ({
		title: "设置新密码",
		description: "为你的账号设置新密码。",
	}),
};

/** 密码强度门槛：NFC 归一化后按 code points 计 12–128。 */
function passwordIssue(raw: string): string | null {
	if (!raw) {
		return "请输入新密码。";
	}
	const codePoints = [...raw.normalize("NFC")].length;
	if (codePoints < PASSWORD_MIN_CODE_POINTS || codePoints > PASSWORD_MAX_CODE_POINTS) {
		return "密码需为 12–128 个字符。";
	}
	return null;
}

type FieldProps = {
	id: string;
	label: string;
	type: string;
	autoComplete: string;
	value: string;
	onChange: (value: string) => void;
	disabled?: boolean;
	invalid?: boolean;
	describedBy?: string;
	placeholder?: string;
	trailing?: React.ReactNode;
};

function PlainField({
	id,
	label,
	type,
	autoComplete,
	value,
	onChange,
	disabled,
	invalid,
	describedBy,
	placeholder,
	trailing,
}: FieldProps) {
	return (
		<div className="space-y-2">
			<div className="flex items-center justify-between gap-3">
				<label htmlFor={id} className={FIELD_LABEL}>
					{label}
				</label>
				{trailing}
			</div>
			<Input
				id={id}
				type={type}
				autoComplete={autoComplete}
				value={value}
				onChange={(event) => onChange(event.target.value)}
				disabled={disabled}
				placeholder={placeholder}
				aria-invalid={invalid || undefined}
				aria-describedby={describedBy}
			/>
		</div>
	);
}

function EmailField(props: Omit<FieldProps, "label" | "type" | "autoComplete" | "placeholder">) {
	return (
		<div className="space-y-2">
			<label htmlFor={props.id} className={FIELD_LABEL}>
				邮箱地址
			</label>
			<InputGroup>
				<InputGroupInput
					id={props.id}
					type="email"
					inputMode="email"
					autoComplete="email"
					placeholder="you@example.com"
					value={props.value}
					onChange={(event) => props.onChange(event.target.value)}
					disabled={props.disabled}
					aria-invalid={props.invalid || undefined}
					aria-describedby={props.describedBy}
				/>
				<InputGroupAddon align="inline-start">
					<AtSign aria-hidden="true" />
				</InputGroupAddon>
			</InputGroup>
		</div>
	);
}

function SubmitButton({ label, loading }: { label: string; loading?: boolean }) {
	return (
		<Button
			type="submit"
			size="lg"
			className="w-full max-md:min-h-11"
			disabled={loading}
			aria-busy={loading || undefined}
		>
			{loading ? (
				<Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
			) : null}
			{label}
		</Button>
	);
}

/**
 * Presentational email-auth card for the Auth 5 shell. Every state is driven
 * by explicit props; the card only emits UI actions and never fetches,
 * navigates, or stores anything itself.
 */
export function EmailAuthCard({
	mode,
	email = "",
	loading = false,
	error = null,
	cooldownSeconds = 0,
	onSubmitEmail,
	onSubmitPassword,
	onSubmitOtp,
	onSubmitSetPassword,
	onSubmitForgot,
	onSubmitReset,
	onResendOtp,
	onBackToEmail,
	onForgotPassword,
	onSignUp,
	onLegalLink,
}: EmailAuthCardProps) {
	const [emailDraft, setEmailDraft] = useState(email);
	const [password, setPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const [otpCode, setOtpCode] = useState("");
	const [localError, setLocalError] = useState<string | null>(null);
	const [cooldown, setCooldown] = useState(cooldownSeconds);
	const lastSubmittedOtpRef = useRef<string | null>(null);

	useEffect(() => {
		setEmailDraft(email);
	}, [email]);

	useEffect(() => {
		setLocalError(null);
		setPassword("");
		setConfirmPassword("");
		setOtpCode("");
		lastSubmittedOtpRef.current = null;
	}, [mode]);

	useEffect(() => {
		setCooldown(cooldownSeconds);
	}, [cooldownSeconds]);

	// 外部错误到达后，允许用户用同一验证码重试。
	useEffect(() => {
		lastSubmittedOtpRef.current = null;
	}, [error]);

	useEffect(() => {
		if (cooldown <= 0) {
			return;
		}
		const timer = setTimeout(() => setCooldown((current) => Math.max(0, current - 1)), 1000);
		return () => clearTimeout(timer);
	}, [cooldown]);

	const activeError = localError ?? error;
	const describedBy = activeError ? STATUS_ID : undefined;
	const copy = MODE_COPY[mode](email);

	function submitValidatedEmail(
		event: React.FormEvent<HTMLFormElement>,
		emit?: (email: string) => void,
	) {
		event.preventDefault();
		const candidate = emailDraft.trim();
		if (!EMAIL_PATTERN.test(candidate)) {
			setLocalError("请输入有效的邮箱地址。");
			return;
		}
		setLocalError(null);
		emit?.(candidate);
	}

	function submitPasswordLogin(event: React.FormEvent<HTMLFormElement>) {
		event.preventDefault();
		if (!password) {
			setLocalError("请输入密码。");
			return;
		}
		setLocalError(null);
		onSubmitPassword?.(password);
	}

	function handleOtpChange(value: string) {
		setOtpCode(value);
		setLocalError(null);
		// 修改验证码后允许再次提交。
		lastSubmittedOtpRef.current = null;
	}

	function trySubmitOtp(code: string) {
		if (code.length !== 6) {
			setLocalError("请输入 6 位验证码。");
			return;
		}
		// 同一验证码在自动完成与手动验证之间最多提交一次。
		if (lastSubmittedOtpRef.current === code) {
			return;
		}
		lastSubmittedOtpRef.current = code;
		setLocalError(null);
		onSubmitOtp?.(code);
	}

	function submitNewPassword(
		event: React.FormEvent<HTMLFormElement>,
		emit?: (password: string) => void,
	) {
		event.preventDefault();
		const issue = passwordIssue(password);
		if (issue) {
			setLocalError(issue);
			return;
		}
		if (password !== confirmPassword) {
			setLocalError("两次输入的密码不一致。");
			return;
		}
		setLocalError(null);
		emit?.(password.normalize("NFC"));
	}

	const passwordRequirementHint = <span className="text-xs text-subtle">12–128 个字符</span>;

	return (
		<div className="w-full space-y-6">
			<header className="space-y-1.5">
				<h1 className="text-2xl font-semibold tracking-tight text-ink">{copy.title}</h1>
				<p className="text-sm leading-6 text-muted-foreground">{copy.description}</p>
			</header>

			{mode === "email" ? (
				<>
					<div className="space-y-1.5">
						<Button
							type="button"
							variant="outline"
							size="lg"
							className="w-full max-md:min-h-11"
							disabled
							aria-describedby="google-auth-note"
						>
							<GoogleIcon aria-hidden="true" />
							使用 Google 登录
						</Button>
						<p id="google-auth-note" className="text-xs text-muted-foreground">
							Google 登录暂不可用。
						</p>
					</div>

					<AuthDivider>或</AuthDivider>

					<form
						className="space-y-4"
						noValidate
						onSubmit={(event) => submitValidatedEmail(event, onSubmitEmail)}
					>
						<EmailField
							id="email-auth-email"
							value={emailDraft}
							onChange={(value) => {
								setEmailDraft(value);
								setLocalError(null);
							}}
							disabled={loading}
							invalid={Boolean(activeError)}
							describedBy={describedBy}
						/>
						<SubmitButton label="使用邮箱继续" loading={loading} />
					</form>
				</>
			) : null}

			{mode === "password" ? (
				<form className="space-y-4" noValidate onSubmit={submitPasswordLogin}>
					<div className="flex items-center justify-between gap-3 rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm">
						<span className="truncate text-ink">{email}</span>
						<button
							type="button"
							aria-label="使用其他邮箱"
							className={cn(LINK_BUTTON, "shrink-0 text-xs")}
							disabled={loading}
							onClick={onBackToEmail}
						>
							更换
						</button>
					</div>
					<PlainField
						id="email-auth-password"
						label="密码"
						type="password"
						autoComplete="current-password"
						value={password}
						onChange={(value) => {
							setPassword(value);
							setLocalError(null);
						}}
						disabled={loading}
						invalid={Boolean(activeError)}
						describedBy={describedBy}
						trailing={
							<button
								type="button"
								className={cn(LINK_BUTTON, "text-xs")}
								disabled={loading}
								onClick={onForgotPassword}
							>
								忘记密码？
							</button>
						}
					/>
					<SubmitButton label="登录" loading={loading} />
				</form>
			) : null}

			{mode === "otp" ? (
				<form
					className="space-y-4"
					noValidate
					onSubmit={(event) => {
						event.preventDefault();
						trySubmitOtp(otpCode);
					}}
				>
					<OtpInput
						value={otpCode}
						onChange={handleOtpChange}
						onComplete={trySubmitOtp}
						disabled={loading}
						invalid={Boolean(activeError)}
						describedBy={describedBy}
					/>
					<SubmitButton label="验证" loading={loading} />
					<div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
						<span>没有收到验证码？</span>
						<button
							type="button"
							className={LINK_BUTTON}
							disabled={cooldown > 0 || loading}
							onClick={onResendOtp}
						>
							{cooldown > 0 ? `${cooldown} 秒后可重发` : "重新发送"}
						</button>
					</div>
					<button
						type="button"
						className={LINK_BUTTON}
						disabled={loading}
						onClick={onBackToEmail}
					>
						返回登录
					</button>
				</form>
			) : null}

			{mode === "set-password" || mode === "reset" ? (
				<form
					className="space-y-4"
					noValidate
					onSubmit={(event) =>
						submitNewPassword(event, mode === "set-password" ? onSubmitSetPassword : onSubmitReset)
					}
				>
					<PlainField
						id="email-auth-new-password"
						label="新密码"
						type="password"
						autoComplete="new-password"
						value={password}
						onChange={(value) => {
							setPassword(value);
							setLocalError(null);
						}}
						disabled={loading}
						invalid={Boolean(activeError)}
						describedBy={describedBy}
						trailing={passwordRequirementHint}
					/>
					<PlainField
						id="email-auth-confirm-password"
						label="确认密码"
						type="password"
						autoComplete="new-password"
						value={confirmPassword}
						onChange={(value) => {
							setConfirmPassword(value);
							setLocalError(null);
						}}
						disabled={loading}
						invalid={Boolean(activeError)}
						describedBy={describedBy}
					/>
					<SubmitButton label={mode === "set-password" ? "设置密码" : "重置密码"} loading={loading} />
				</form>
			) : null}

			{mode === "forgot" ? (
				<form
					className="space-y-4"
					noValidate
					onSubmit={(event) => submitValidatedEmail(event, onSubmitForgot)}
				>
					<EmailField
						id="email-auth-email"
						value={emailDraft}
						onChange={(value) => {
							setEmailDraft(value);
							setLocalError(null);
						}}
						disabled={loading}
						invalid={Boolean(activeError)}
						describedBy={describedBy}
					/>
					<SubmitButton label="发送验证码" loading={loading} />
					<button
						type="button"
						className={LINK_BUTTON}
						disabled={loading}
						onClick={onBackToEmail}
					>
						返回登录
					</button>
				</form>
			) : null}

			{activeError ? (
				<p
					id={STATUS_ID}
					role="alert"
					className="flex items-start gap-2 rounded-md border border-feedback-error/20 bg-feedback-error/5 px-3 py-2 text-sm leading-5 text-feedback-error"
				>
					<CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
					{activeError}
				</p>
			) : null}

			{mode === "email" ? (
				<div className="space-y-4">
					<p className="text-center text-sm text-muted-foreground">
						还没有账号？{" "}
						<button
							type="button"
							className={cn(LINK_BUTTON, "font-medium text-ink hover:text-ink/70")}
							disabled={loading}
							onClick={onSignUp}
						>
							注册
						</button>
					</p>
					<p className="text-xs leading-5 text-muted-foreground">
						继续即表示你同意我们的{" "}
						<button type="button" className={LINK_BUTTON} onClick={() => onLegalLink?.("terms")}>
							服务条款
						</button>{" "}
						和{" "}
						<button type="button" className={LINK_BUTTON} onClick={() => onLegalLink?.("privacy")}>
							隐私政策
						</button>
						。
					</p>
				</div>
			) : null}
		</div>
	);
}
