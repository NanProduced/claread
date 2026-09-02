import { expect, test, type Page, type Route } from "@playwright/test";

const REGISTERED_EMAIL = "reader@example.com";
const NEW_EMAIL = "new-reader@example.com";
const RECOVERY_EMAIL = "recovery-reader@example.com";
const OTP = "246810";
const LOGIN_PASSWORD = "correct-horse-1234";
const NEW_PASSWORD = "new-correct-horse-1234";
const RESET_PASSWORD = "reset-correct-horse-1234";
const COOLDOWN_SECONDS = 1;

type OtpFlow = "register" | "password_reset";

type MockState =
  | { step: "idle" }
  | { step: "password"; email: string }
  | { step: "otp"; flow: OtpFlow; email: string; cooldownUntil: number }
  | { step: "set-password" | "reset"; flow: OtpFlow; email: string };

function readBody(route: Route, field: string): string {
  const body = route.request().postDataJSON();
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new Error(`Expected JSON object for ${route.request().method()} ${field}`);
  }
  const value = (body as Record<string, unknown>)[field];
  if (typeof value !== "string") {
    throw new Error(`Expected string field ${field}`);
  }
  return value;
}

async function installEmailAuthMock(page: Page, registeredEmail = REGISTERED_EMAIL) {
  let state: MockState = { step: "idle" };

  const cooldownRemaining = () =>
    state.step === "otp"
      ? Math.max(0, Math.ceil((state.cooldownUntil - Date.now()) / 1000))
      : 0;

  const startOtp = (flow: OtpFlow, email: string) => {
    state = {
      step: "otp",
      flow,
      email,
      cooldownUntil: Date.now() + COOLDOWN_SECONDS * 1000,
    };
  };

  const fulfill = (route: Route, body: Record<string, unknown>, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

  await page.route("**/api/web/auth/email/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (request.method() === "GET" && path === "/api/web/auth/email/flow-status") {
      if (state.step === "otp") {
        await fulfill(route, {
          ok: true,
          step: "otp",
          flow: state.flow,
          email: state.email,
          resend_after: cooldownRemaining(),
        });
        return;
      }
      if (state.step === "set-password" || state.step === "reset") {
        await fulfill(route, { ok: true, step: state.step, email: state.email });
        return;
      }
      await fulfill(route, { ok: true, step: "idle" });
      return;
    }

    if (request.method() !== "POST") {
      throw new Error(`Unexpected email auth method ${request.method()} ${path}`);
    }

    if (path === "/api/web/auth/email/start") {
      const email = readBody(route, "email");
      if (state.step === "otp" && state.flow === "register") {
        if (cooldownRemaining() > 0) {
          await fulfill(
            route,
            {
              ok: false,
              message: "发送过于频繁，请稍后再试。",
              code: "email_cooldown",
              retry_after: cooldownRemaining(),
            },
            429,
          );
          return;
        }
        startOtp("register", email);
        await fulfill(route, { ok: true, mode: "register", resend_after: COOLDOWN_SECONDS });
        return;
      }
      if (state.step !== "idle") {
        throw new Error(`Unexpected start state ${state.step}`);
      }
      if (email === registeredEmail) {
        state = { step: "password", email };
        await fulfill(route, { ok: true, mode: "password" });
        return;
      }
      startOtp("register", email);
      await fulfill(route, { ok: true, mode: "register", resend_after: COOLDOWN_SECONDS });
      return;
    }

    if (path === "/api/web/auth/email/password/login") {
      const email = readBody(route, "email");
      const password = readBody(route, "password");
      if (state.step !== "password" || state.email !== email || password !== LOGIN_PASSWORD) {
        throw new Error("Unexpected password login state");
      }
      state = { step: "idle" };
      await fulfill(route, { ok: true });
      return;
    }

    if (path === "/api/web/auth/email/password-reset/request") {
      const email = readBody(route, "email");
      if (state.step === "otp" && state.flow === "password_reset") {
        if (cooldownRemaining() > 0) {
          await fulfill(
            route,
            {
              ok: false,
              message: "发送过于频繁，请稍后再试。",
              code: "email_cooldown",
              retry_after: cooldownRemaining(),
            },
            429,
          );
          return;
        }
      } else if (state.step !== "password" || state.email !== email) {
        throw new Error("Unexpected password reset request state");
      }
      startOtp("password_reset", email);
      await fulfill(route, {
        ok: true,
        status: "accepted",
        resend_after: COOLDOWN_SECONDS,
      });
      return;
    }

    if (path === "/api/web/auth/email/otp/verify") {
      const code = readBody(route, "code");
      if (state.step !== "otp" || code !== OTP) {
        throw new Error("Unexpected OTP verification state");
      }
      const next = state.flow === "register" ? "set-password" : "reset";
      state = { step: next, flow: state.flow, email: state.email };
      await fulfill(route, { ok: true, next });
      return;
    }

    if (path === "/api/web/auth/email/register") {
      const password = readBody(route, "password");
      if (state.step !== "set-password" || state.flow !== "register" || password !== NEW_PASSWORD) {
        throw new Error("Unexpected registration state");
      }
      state = { step: "idle" };
      await fulfill(route, { ok: true });
      return;
    }

    if (path === "/api/web/auth/email/password-reset/complete") {
      const password = readBody(route, "password");
      if (state.step !== "reset" || state.flow !== "password_reset" || password !== RESET_PASSWORD) {
        throw new Error("Unexpected password reset completion state");
      }
      state = { step: "idle" };
      await fulfill(route, { ok: true });
      return;
    }

    if (path === "/api/web/auth/email/cancel") {
      state = { step: "idle" };
      await fulfill(route, { ok: true });
      return;
    }

    throw new Error(`Unexpected email auth request ${request.method()} ${path}`);
  });
}

async function fillOtp(page: Page) {
  await page.getByLabel("第 1 位，共 6 位").fill(OTP);
}

async function expectNoAuthStorage(page: Page) {
  const authEntries = await page.evaluate(() =>
    [localStorage, sessionStorage]
      .flatMap((storage) => Object.entries(storage))
      .filter(([key, value]) =>
        /(auth|session|token|credential|password|challenge|ticket)/i.test(`${key}:${value}`),
      ),
  );
  expect(authEntries).toEqual([]);
}

test.describe("offline email auth browser coverage", () => {
  test("new email completes OTP and password setup", async ({ page }) => {
    await installEmailAuthMock(page);
    await page.goto("/login?next=/daily");

    await expect(page.getByRole("heading", { name: "登录或创建账号" })).toBeVisible();
    await page.getByLabel("邮箱地址").fill(NEW_EMAIL);
    await page.getByRole("button", { name: "使用邮箱继续" }).click();

    await expect(page.getByRole("heading", { name: "查看你的邮箱" })).toBeVisible();
    await expect(page.getByText(`我们已向 ${NEW_EMAIL} 发送 6 位验证码。`)).toBeVisible();
    await fillOtp(page);
    await expect(page.getByRole("heading", { name: "设置密码" })).toBeVisible();

    await page.getByLabel("新密码").fill(NEW_PASSWORD);
    await page.getByLabel("确认密码").fill(NEW_PASSWORD);
    await page.getByRole("button", { name: "设置密码" }).click();
    await page.waitForURL((url) => url.pathname === "/daily");
  });

  test("registered email uses password login", async ({ page }) => {
    await installEmailAuthMock(page);
    await page.goto("/login?next=/daily");

    await page.getByLabel("邮箱地址").fill(REGISTERED_EMAIL);
    await page.getByRole("button", { name: "使用邮箱继续" }).click();
    await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
    await page.getByLabel("密码").fill(LOGIN_PASSWORD);
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL((url) => url.pathname === "/daily");
    await expectNoAuthStorage(page);
  });

  test("forgot password completes OTP and reset", async ({ page }) => {
    await installEmailAuthMock(page);
    await page.goto("/login?next=/daily");

    await page.getByLabel("邮箱地址").fill(REGISTERED_EMAIL);
    await page.getByRole("button", { name: "使用邮箱继续" }).click();
    await expect(page.getByRole("button", { name: "忘记密码？" })).toBeVisible();
    await page.getByRole("button", { name: "忘记密码？" }).click();
    await page.getByRole("button", { name: "发送验证码" }).click();

    await expect(page.getByRole("heading", { name: "查看你的邮箱" })).toBeVisible();
    await expect(page.getByText(`我们已向 ${REGISTERED_EMAIL} 发送 6 位验证码。`)).toBeVisible();
    await fillOtp(page);
    await expect(page.getByRole("heading", { name: "设置新密码" })).toBeVisible();

    await page.getByLabel("新密码").fill(RESET_PASSWORD);
    await page.getByLabel("确认密码").fill(RESET_PASSWORD);
    await page.getByRole("button", { name: "重置密码" }).click();
    await page.waitForURL((url) => url.pathname === "/daily");
  });

  test("refresh restores OTP and password setup with email and restarts cooldown", async ({ page }) => {
    await installEmailAuthMock(page);
    await page.goto("/login?next=/daily");

    await page.getByLabel("邮箱地址").fill(RECOVERY_EMAIL);
    await page.getByRole("button", { name: "使用邮箱继续" }).click();
    await expect(page.getByRole("heading", { name: "查看你的邮箱" })).toBeVisible();
    await expect(page.getByText(`我们已向 ${RECOVERY_EMAIL} 发送 6 位验证码。`)).toBeVisible();
    await expect(page.getByRole("button", { name: "1 秒后可重发" })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "查看你的邮箱" })).toBeVisible();
    await expect(page.getByText(`我们已向 ${RECOVERY_EMAIL} 发送 6 位验证码。`)).toBeVisible();
    const resend = page.getByRole("button", { name: "重新发送", exact: true });
    await expect(resend).toBeEnabled();
    await resend.click();
    await expect(page.getByRole("button", { name: "1 秒后可重发" })).toBeVisible();

    await fillOtp(page);
    await expect(page.getByRole("heading", { name: "设置密码" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "设置密码" })).toBeVisible();
    await expect(page.getByText(`为 ${RECOVERY_EMAIL} 设置登录密码。`)).toBeVisible();

    await page.getByLabel("新密码").fill(NEW_PASSWORD);
    await page.getByLabel("确认密码").fill(NEW_PASSWORD);
    await page.getByRole("button", { name: "设置密码" }).click();
    await page.waitForURL((url) => url.pathname === "/daily");
    await expectNoAuthStorage(page);
  });
});
