import { describe, expect, it } from "vitest";

import {
  AUTH_EXPIRED_MESSAGE,
  CONTENT_NOT_FOUND_MESSAGE,
  GENERIC_ERROR_MESSAGE,
  NETWORK_ERROR_MESSAGE,
  SERVER_UNAVAILABLE_MESSAGE,
  looksLikeSafeUserCopy,
  userFacingErrorCopy,
  userFacingErrorMessage,
  userFacingPayloadMessage,
} from "./user-facing-error";

describe("user-facing error gateway", () => {
  it("never passes raw technical error text through", () => {
    // 截图同款：response.json() 打到 HTML 错误页的 SyntaxError。
    expect(
      userFacingErrorMessage(
        new SyntaxError(`Unexpected token '<', "<!DOCTYPE "... is not valid JSON`),
      ),
    ).toBe(SERVER_UNAVAILABLE_MESSAGE);
    expect(userFacingErrorMessage(new TypeError("Failed to fetch"))).toBe(
      NETWORK_ERROR_MESSAGE,
    );
    expect(userFacingErrorMessage(new Error("boom"), "自定义兜底。")).toBe(
      "自定义兜底。",
    );
    expect(userFacingErrorMessage("weird")).toBe(GENERIC_ERROR_MESSAGE);
    expect(
      userFacingErrorMessage(
        Object.assign(new Error("x"), { name: "AbortError" }),
      ),
    ).toBe("");
  });

  it("keeps clean Chinese BFF copy, folds English/technical strings", () => {
    expect(looksLikeSafeUserCopy("验证码错误，请重新输入。")).toBe(true);
    expect(looksLikeSafeUserCopy("Entry query mismatch")).toBe(false);
    expect(
      looksLikeSafeUserCopy(`Unexpected token '<' 不是合法 JSON`),
    ).toBe(false);
  });

  it("maps BFF payloads by message → code → status → fallback", () => {
    expect(
      userFacingPayloadMessage({ status: 409, message: "存在待确认的候选文档。" }),
    ).toBe("存在待确认的候选文档。");
    expect(userFacingPayloadMessage({ status: 401 })).toBe(AUTH_EXPIRED_MESSAGE);
    expect(userFacingPayloadMessage({ status: 404, message: "Not Found" })).toBe(
      CONTENT_NOT_FOUND_MESSAGE,
    );
    expect(userFacingPayloadMessage({ status: 503, message: "fetch failed" })).toBe(
      SERVER_UNAVAILABLE_MESSAGE,
    );
    expect(
      userFacingPayloadMessage({ code: "insufficient_credits" }, "兜底。"),
    ).toBe("当前积分不足，请稍后再试。");
    expect(
      userFacingPayloadMessage({ status: 400, message: "bad request body" }, "兜底。"),
    ).toBe("兜底。");
  });

  it("userFacingErrorCopy keeps authored Chinese errors, folds the rest", () => {
    expect(userFacingErrorCopy(new Error("确认失败：候选已过期。"))).toBe(
      "确认失败：候选已过期。",
    );
    expect(userFacingErrorCopy(new TypeError("Failed to fetch"), "提交失败。")).toBe(
      NETWORK_ERROR_MESSAGE,
    );
    expect(userFacingErrorCopy(new Error("upstream detail dump"), "兜底。")).toBe(
      "兜底。",
    );
  });
});
