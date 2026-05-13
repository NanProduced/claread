import "server-only";

import { cookies } from "next/headers";

export const WEB_SESSION_COOKIE = "claread_web_session";
export const WEB_PHONE_COOKIE = "claread_web_phone";
export const WEB_PHONE_CHALLENGE_COOKIE = "claread_phone_login_challenge";

export type WebSession =
  | {
      kind: "authenticated";
      sessionToken: string;
      source: "cookie";
      phone?: string;
    }
  | {
      kind: "debug";
      sessionToken: string;
      source: "env";
    }
  | {
      kind: "mock_phone";
      source: "mock";
      phone: string;
    }
  | {
      kind: "anonymous";
      source: "none";
    };

export async function getWebSession(): Promise<WebSession> {
  const cookieStore = await cookies();
  const cookieToken = cookieStore.get(WEB_SESSION_COOKIE)?.value;
  const phone = cookieStore.get(WEB_PHONE_COOKIE)?.value;

  if (cookieToken) {
    return {
      kind: "authenticated",
      sessionToken: cookieToken,
      source: "cookie",
      phone,
    };
  }

  const debugToken = process.env.CLAREAD_WEB_DEBUG_SESSION_TOKEN;

  if (debugToken && process.env.NODE_ENV !== "production") {
    return {
      kind: "debug",
      sessionToken: debugToken,
      source: "env",
    };
  }

  if (phone) {
    return {
      kind: "mock_phone",
      source: "mock",
      phone,
    };
  }

  return {
    kind: "anonymous",
    source: "none",
  };
}

export function projectSession(session: WebSession) {
  return {
    authenticated:
      session.kind === "authenticated" ||
      session.kind === "debug" ||
      session.kind === "mock_phone",
    mode: session.kind,
    source: session.source,
    phone: "phone" in session ? session.phone : undefined,
    upstreamReady: session.kind === "authenticated" || session.kind === "debug",
  };
}
