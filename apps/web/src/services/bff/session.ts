import "server-only";

import { cookies } from "next/headers";
import type { Route } from "next";
import { appReadRoute, loginRoute } from "@/lib/routes";

import { logoutUpstreamSession } from "@/services/api/auth";

export const WEB_SESSION_COOKIE = "claread_web_session";
export const WEB_EMAIL_CHALLENGE_COOKIE = "claread_email_challenge";
export const WEB_EMAIL_TICKET_COOKIE = "claread_email_ticket";
export const WEB_EMAIL_FLOW_COOKIE_PATH = "/api/web/auth/email";

export type WebSession =
  | {
      kind: "authenticated";
      sessionToken: string;
      source: "cookie";
    }
  | {
      kind: "debug";
      sessionToken: string;
      source: "env";
    }
  | {
      kind: "anonymous";
      source: "none";
    };

export type ProjectedWebSessionState = "signed_in" | "signed_out" | "limited_debug";

export interface ProjectedWebSession {
  state: ProjectedWebSessionState;
  source: WebSession["source"];
  hasAppAccess: boolean;
}

export async function getWebSession(): Promise<WebSession> {
  const cookieStore = await cookies();
  const cookieToken = cookieStore.get(WEB_SESSION_COOKIE)?.value;

  if (cookieToken) {
    return {
      kind: "authenticated",
      sessionToken: cookieToken,
      source: "cookie",
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

  return {
    kind: "anonymous",
    source: "none",
  };
}

export function projectSession(session: WebSession) {
  return {
    state:
      session.kind === "authenticated"
        ? "signed_in"
        : session.kind === "anonymous"
          ? "signed_out"
          : "limited_debug",
    source: session.source,
    hasAppAccess: session.kind !== "anonymous",
  } satisfies ProjectedWebSession;
}

export async function getProjectedWebSession(): Promise<ProjectedWebSession> {
  return projectSession(await getWebSession());
}

export function appCtaForSession(session: ProjectedWebSession): {
  href: Route;
  label: string;
} {
  if (session.state === "signed_in") {
    return {
      href: appReadRoute,
      label: "打开 Claread",
    };
  }

  if (session.state === "limited_debug") {
    return {
      href: appReadRoute,
      label: "打开调试工作区",
    };
  }

  return {
    href: loginRoute(appReadRoute),
    label: "登录",
  };
}

export async function clearWebAuthCookies(): Promise<void> {
  const cookieStore = await cookies();
  const sessionToken = cookieStore.get(WEB_SESSION_COOKIE)?.value;

  if (sessionToken) {
    await logoutUpstreamSession(sessionToken);
  }

  cookieStore.delete(WEB_SESSION_COOKIE);
}