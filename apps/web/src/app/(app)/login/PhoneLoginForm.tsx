"use client";

import { useMemo, useState } from "react";
import type { Route } from "next";
import { useRouter } from "next/navigation";

type AuthStatus =
  | { tone: "idle"; message: "" }
  | { tone: "info" | "success" | "error"; message: string };

type ApiResponse = {
  ok?: boolean;
  message?: unknown;
  error?: unknown;
  detail?: unknown;
};

const REQUEST_CODE_ENDPOINT = "/api/web/auth/phone/request-code";
const VERIFY_CODE_ENDPOINT = "/api/web/auth/phone/verify-code";
const settingsRoute = "/settings" as Route;

function normalizeDigits(value: string, maxLength: number) {
  return value.replace(/\D/g, "").slice(0, maxLength);
}

function isValidMainlandPhone(phone: string) {
  return /^1[3-9]\d{9}$/.test(phone);
}

function pickMessage(body: ApiResponse) {
  if (typeof body.message === "string" && body.message.trim()) {
    return body.message;
  }

  if (typeof body.error === "string" && body.error.trim()) {
    return body.error;
  }

  if (typeof body.detail === "string" && body.detail.trim()) {
    return body.detail;
  }

  return null;
}

async function readErrorMessage(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as ApiResponse;
    return pickMessage(body) ?? fallback;
  } catch {
    // Early BFF implementations may return an empty body on failure.
  }

  return fallback;
}

function getRequestErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message !== "Failed to fetch") {
    return error.message;
  }

  return fallback;
}

async function postJson(url: string, body: Record<string, string>, fallback: string) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response, fallback));
  }

  const payload = (await response
    .clone()
    .json()
    .catch(() => null)) as ApiResponse | null;

  if (payload?.ok === false) {
    throw new Error(pickMessage(payload) ?? fallback);
  }

  return payload ?? {};
}

export function PhoneLoginForm() {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<AuthStatus>({ tone: "idle", message: "" });
  const [isRequestingCode, setIsRequestingCode] = useState(false);
  const [isVerifyingCode, setIsVerifyingCode] = useState(false);
  const [hasRequestedCode, setHasRequestedCode] = useState(false);

  const canRequestCode = useMemo(
    () => isValidMainlandPhone(phone) && !isRequestingCode && !isVerifyingCode,
    [phone, isRequestingCode, isVerifyingCode],
  );
  const canVerifyCode = useMemo(
    () =>
      isValidMainlandPhone(phone) &&
      code.length >= 4 &&
      !isRequestingCode &&
      !isVerifyingCode,
    [phone, code, isRequestingCode, isVerifyingCode],
  );

  async function handleRequestCode() {
    if (!isValidMainlandPhone(phone)) {
      setStatus({ tone: "error", message: "请输入 11 位中国大陆手机号。" });
      return;
    }

    setIsRequestingCode(true);
    setStatus({ tone: "info", message: "正在发送验证码..." });

    try {
      const payload = await postJson(
        REQUEST_CODE_ENDPOINT,
        { phone },
        "验证码发送失败，请稍后再试。",
      );

      setHasRequestedCode(true);
      setStatus({
        tone: "success",
        message: pickMessage(payload) ?? "验证码已发送，本地调试请输入 888888。",
      });
    } catch (error) {
      setStatus({
        tone: "error",
        message: getRequestErrorMessage(error, "验证码发送失败，请稍后再试。"),
      });
    } finally {
      setIsRequestingCode(false);
    }
  }

  async function handleVerifyCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!isValidMainlandPhone(phone)) {
      setStatus({ tone: "error", message: "请输入 11 位中国大陆手机号。" });
      return;
    }

    if (code.length < 4) {
      setStatus({ tone: "error", message: "请输入短信验证码。" });
      return;
    }

    setIsVerifyingCode(true);
    setStatus({ tone: "info", message: "正在验证..." });

    try {
      const payload = await postJson(
        VERIFY_CODE_ENDPOINT,
        { phone, code },
        "验证码校验失败，请重新输入。",
      );

      setStatus({
        tone: "success",
        message: pickMessage(payload) ?? "登录成功，Web 会话已建立。",
      });
      router.refresh();
      router.push(settingsRoute);
    } catch (error) {
      setStatus({
        tone: "error",
        message: getRequestErrorMessage(error, "验证码校验失败，请重新输入。"),
      });
    } finally {
      setIsVerifyingCode(false);
    }
  }

  const statusClassName =
    status.tone === "error"
      ? "border-error-red/20 bg-error-red/5 text-error-red"
      : status.tone === "success"
        ? "border-structure-green/20 bg-structure-green/10 text-structure-green"
        : "border-lens-blue/20 bg-lens-blue-soft text-lens-blue";

  return (
    <form className="mt-6 space-y-4" onSubmit={handleVerifyCode}>
      <div className="space-y-2">
        <label className="text-xs font-semibold text-muted" htmlFor="login-phone">
          手机号
        </label>
        <input
          id="login-phone"
          className="w-full rounded-md border border-hairline bg-surface-warm px-4 py-3 text-sm text-ink outline-none transition-colors placeholder:text-subtle focus:border-muted disabled:cursor-not-allowed disabled:opacity-60"
          inputMode="tel"
          autoComplete="tel"
          placeholder="输入手机号"
          value={phone}
          onChange={(event) => {
            setPhone(normalizeDigits(event.target.value, 11));
            setStatus({ tone: "idle", message: "" });
          }}
          disabled={isRequestingCode || isVerifyingCode}
        />
      </div>

      <button
        className="w-full rounded-pill bg-ink px-4 py-3 text-sm font-semibold text-surface transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={!canRequestCode}
        onClick={handleRequestCode}
        type="button"
      >
        {isRequestingCode ? "发送中..." : hasRequestedCode ? "重新发送验证码" : "发送验证码"}
      </button>

      <div className="space-y-2">
        <label className="text-xs font-semibold text-muted" htmlFor="login-code">
          验证码
        </label>
        <input
          id="login-code"
          className="w-full rounded-md border border-hairline bg-surface-warm px-4 py-3 text-sm text-ink outline-none transition-colors placeholder:text-subtle focus:border-muted disabled:cursor-not-allowed disabled:opacity-60"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="输入短信验证码"
          value={code}
          onChange={(event) => {
            setCode(normalizeDigits(event.target.value, 8));
            setStatus({ tone: "idle", message: "" });
          }}
          disabled={isRequestingCode || isVerifyingCode}
        />
      </div>

      <button
        className="w-full rounded-pill border border-ink bg-surface px-4 py-3 text-sm font-semibold text-ink transition-colors hover:bg-surface-warm disabled:cursor-not-allowed disabled:border-hairline disabled:text-subtle"
        disabled={!canVerifyCode}
        type="submit"
      >
        {isVerifyingCode ? "验证中..." : "登录"}
      </button>

      {status.message ? (
        <p className={`rounded-md border px-3 py-2 text-sm leading-5 ${statusClassName}`}>
          {status.message}
        </p>
      ) : null}
    </form>
  );
}
