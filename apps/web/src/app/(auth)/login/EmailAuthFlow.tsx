"use client";

import { useEffect, useRef, useState } from "react";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { EmailAuthScreen } from "@/components/auth/EmailAuthScreen";
import type { EmailAuthMode } from "@/components/auth/EmailAuthCard";
import { appReadRoute, isAllowedIntent, isAllowedNextPath } from "@/lib/routes";
import {
	NETWORK_ERROR_MESSAGE,
	SERVER_UNAVAILABLE_MESSAGE,
	isNetworkError,
	looksLikeSafeUserCopy,
	userFacingErrorCopy,
} from "@/lib/user-facing-error";

type OtpFlow = "register" | "password_reset";

const FLOW_STATUS = "/api/web/auth/email/flow-status";
const START = "/api/web/auth/email/start";
const OTP_VERIFY = "/api/web/auth/email/otp/verify";
const REGISTER = "/api/web/auth/email/register";
const PASSWORD_LOGIN = "/api/web/auth/email/password/login";
const RESET_REQUEST = "/api/web/auth/email/password-reset/request";
const RESET_COMPLETE = "/api/web/auth/email/password-reset/complete";
const CANCEL = "/api/web/auth/email/cancel";
const FALLBACK_ROUTE = appReadRoute;
const SAFE_ERROR = "登录暂时不可用，请稍后重试。";

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInt(value: unknown): value is number {
	return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isNonNegativeInt(value: unknown): value is number {
	return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function requireOk(body: Record<string, unknown>): void {
	if (body.ok !== true) {
		throw new Error(SAFE_ERROR);
	}
}

function requireEmail(value: unknown): string {
	if (typeof value !== "string") {
		throw new Error(SAFE_ERROR);
	}
	return value;
}

function requireResendAfter(value: unknown): number {
	if (!isNonNegativeInt(value)) {
		throw new Error(SAFE_ERROR);
	}
	return value;
}

function parseFlowStatus(
	body: Record<string, unknown>,
):
	| { step: "idle" }
	| { step: "otp"; flow: OtpFlow; email: string; resend_after: number }
	| { step: "set-password" | "reset"; email: string } {
	requireOk(body);
	if (body.step === "idle") {
		return { step: "idle" };
	}
	if (body.step === "otp") {
		if (body.flow !== "register" && body.flow !== "password_reset") {
			throw new Error(SAFE_ERROR);
		}
		return {
			step: "otp",
			flow: body.flow === "password_reset" ? "password_reset" : "register",
			email: requireEmail(body.email),
			resend_after: requireResendAfter(body.resend_after),
		};
	}
	if (body.step === "set-password" || body.step === "reset") {
		return { step: body.step, email: requireEmail(body.email) };
	}
	throw new Error(SAFE_ERROR);
}

function parseStart(body: Record<string, unknown>) {
	requireOk(body);
	if (body.mode === "password") {
		return { mode: "password" as const };
	}
	if (body.mode === "register") {
		return { mode: "register" as const, resend_after: requireResendAfter(body.resend_after) };
	}
	throw new Error(SAFE_ERROR);
}

function parseOtpNext(body: Record<string, unknown>) {
	requireOk(body);
	if (body.next === "set-password" || body.next === "reset") {
		return body.next;
	}
	throw new Error(SAFE_ERROR);
}

function parseResetRequest(body: Record<string, unknown>) {
	requireOk(body);
	if (body.status !== "accepted") {
		throw new Error(SAFE_ERROR);
	}
	return { resend_after: requireResendAfter(body.resend_after) };
}

function safeNextRoute(value: string | null): Route {
	if (!value || value.includes("\n") || value.includes("\r") || value.startsWith("//")) {
		return FALLBACK_ROUTE;
	}
	if (!value.startsWith("/")) {
		return FALLBACK_ROUTE;
	}
	return isAllowedNextPath(value) ? (value as Route) : FALLBACK_ROUTE;
}

function safeIntent(value: string | null) {
	return isAllowedIntent(value) ? value : null;
}

function messageFromBody(body: Record<string, unknown>, fallback: string): string {
	const message = body.message;
	if (typeof message === "string" && looksLikeSafeUserCopy(message.trim())) {
		return message.trim();
	}
	return fallback;
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
	let text: string;
	try {
		text = await response.text();
	} catch (error) {
		if (isNetworkError(error)) {
			throw new Error(NETWORK_ERROR_MESSAGE);
		}
		throw new Error(SERVER_UNAVAILABLE_MESSAGE);
	}
	try {
		const parsed: unknown = JSON.parse(text);
		if (!isRecord(parsed)) {
			throw new Error(SAFE_ERROR);
		}
		return parsed;
	} catch (error) {
		if (error instanceof Error && error.message === SAFE_ERROR) {
			throw error;
		}
		throw new Error(SERVER_UNAVAILABLE_MESSAGE);
	}
}

export function EmailAuthFlow() {
	const router = useRouter();
	const searchParams = useSearchParams();
	const [mode, setMode] = useState<EmailAuthMode>("email");
	const [email, setEmail] = useState("");
	const [otpFlow, setOtpFlow] = useState<OtpFlow | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [cooldownSeconds, setCooldownSeconds] = useState(0);
	const [cooldownEpoch, setCooldownEpoch] = useState(0);
	const inFlight = useRef(false);

	useEffect(() => {
		if (cooldownSeconds <= 0) {
			return;
		}
		const timer = window.setTimeout(() => {
			setCooldownSeconds((current) => Math.max(0, current - 1));
		}, 1000);
		return () => window.clearTimeout(timer);
	}, [cooldownSeconds, cooldownEpoch]);

	function applyCooldown(seconds: unknown) {
		const value = isPositiveInt(seconds) ? seconds : 0;
		setCooldownSeconds(value);
		setCooldownEpoch((epoch) => epoch + 1);
	}

	function finishSession() {
		router.refresh();
		const nextRoute = safeNextRoute(searchParams.get("next"));
		const intent = safeIntent(searchParams.get("intent"));
		router.push((intent ? `${nextRoute}?intent=${encodeURIComponent(intent)}` : nextRoute) as Route);
	}

	async function request(
		url: string,
		init: RequestInit,
		fallback: string,
	): Promise<{ status: number; body: Record<string, unknown> }> {
		let response: Response;
		try {
			response = await fetch(url, init);
		} catch (error) {
			throw new Error(userFacingErrorCopy(error, NETWORK_ERROR_MESSAGE));
		}
		const body = await readJson(response);
		if (response.status === 429 && isPositiveInt(body.retry_after)) {
			applyCooldown(body.retry_after);
		}
		if (response.ok && body.ok === true) {
			return { status: response.status, body };
		}
		if (!response.ok || body.ok === false) {
			throw new Error(messageFromBody(body, fallback));
		}
		throw new Error(SAFE_ERROR);
	}

	async function run(task: () => Promise<void>, fallback: string) {
		if (inFlight.current) {
			return;
		}
		inFlight.current = true;
		setLoading(true);
		setError(null);
		try {
			await task();
		} catch (error) {
			const message = userFacingErrorCopy(error, fallback);
			if (message) {
				setError(message);
			}
		} finally {
			inFlight.current = false;
			setLoading(false);
		}
	}

	useEffect(() => {
		let cancelled = false;
		void (async () => {
			inFlight.current = true;
			try {
				const { body } = await request(FLOW_STATUS, { method: "GET" }, SAFE_ERROR);
				if (cancelled) {
					return;
				}
				const status = parseFlowStatus(body);
				if (status.step === "otp") {
					setMode("otp");
					setOtpFlow(status.flow);
					setEmail(status.email);
					applyCooldown(status.resend_after);
					return;
				}
				if (status.step === "set-password" || status.step === "reset") {
					setMode(status.step);
					setOtpFlow(status.step === "reset" ? "password_reset" : "register");
					setEmail(status.email);
					applyCooldown(0);
					return;
				}
				setMode("email");
				setOtpFlow(null);
				applyCooldown(0);
			} catch (error) {
				if (!cancelled) {
					const message = userFacingErrorCopy(error, SAFE_ERROR);
					if (message) {
						setError(message);
					}
				}
			} finally {
				inFlight.current = false;
				if (!cancelled) {
					setLoading(false);
				}
			}
		})();
		return () => {
			cancelled = true;
		};
		// Mount-only restore from HttpOnly flow cookies.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

	function handleSubmitEmail(nextEmail: string) {
		void run(async () => {
			const { body } = await request(
				START,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ email: nextEmail }),
				},
				SAFE_ERROR,
			);
			const started = parseStart(body);
			setEmail(nextEmail);
			if (started.mode === "password") {
				setMode("password");
				setOtpFlow(null);
				applyCooldown(0);
				return;
			}
			setMode("otp");
			setOtpFlow("register");
			applyCooldown(started.resend_after);
		}, SAFE_ERROR);
	}

	function handleSubmitPassword(password: string) {
		void run(async () => {
			await request(
				PASSWORD_LOGIN,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ email, password }),
				},
				SAFE_ERROR,
			);
			finishSession();
		}, SAFE_ERROR);
	}

	function handleSubmitOtp(code: string) {
		void run(async () => {
			const { body } = await request(
				OTP_VERIFY,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ code }),
				},
				SAFE_ERROR,
			);
			setMode(parseOtpNext(body));
			applyCooldown(0);
		}, SAFE_ERROR);
	}

	function handleSubmitSetPassword(password: string) {
		void run(async () => {
			await request(
				REGISTER,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ password }),
				},
				SAFE_ERROR,
			);
			finishSession();
		}, SAFE_ERROR);
	}

	function handleForgotPassword() {
		setError(null);
		setMode("forgot");
	}

	function handleSubmitForgot(nextEmail: string) {
		void run(async () => {
			const { body } = await request(
				RESET_REQUEST,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ email: nextEmail }),
				},
				SAFE_ERROR,
			);
			const accepted = parseResetRequest(body);
			setEmail(nextEmail);
			setOtpFlow("password_reset");
			setMode("otp");
			applyCooldown(accepted.resend_after);
		}, SAFE_ERROR);
	}

	function handleSubmitReset(password: string) {
		void run(async () => {
			await request(
				RESET_COMPLETE,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify({ password }),
				},
				SAFE_ERROR,
			);
			finishSession();
		}, SAFE_ERROR);
	}

	function handleResendOtp() {
		if (otpFlow === "password_reset") {
			handleSubmitForgot(email);
			return;
		}
		handleSubmitEmail(email);
	}

	function handleBackToEmail() {
		void run(async () => {
			await request(CANCEL, { method: "POST" }, SAFE_ERROR);
			setMode("email");
			setOtpFlow(null);
			applyCooldown(0);
		}, SAFE_ERROR);
	}

	return (
		<EmailAuthScreen
			mode={mode}
			email={email}
			loading={loading}
			error={error}
			cooldownSeconds={cooldownSeconds}
			onSubmitEmail={handleSubmitEmail}
			onSubmitPassword={handleSubmitPassword}
			onSubmitOtp={handleSubmitOtp}
			onSubmitSetPassword={handleSubmitSetPassword}
			onSubmitForgot={handleSubmitForgot}
			onSubmitReset={handleSubmitReset}
			onResendOtp={handleResendOtp}
			onBackToEmail={handleBackToEmail}
			onForgotPassword={handleForgotPassword}
		/>
	);
}