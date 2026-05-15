import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const WEB_SESSION_COOKIE = "claread_web_session";

const PROTECTED_PREFIXES = [
  "/read",
  "/library",
  "/vocabulary",
  "/review",
  "/settings",
  "/reader",
] as const;

const NEXT_ALLOWLIST_PREFIXES = [
  ...PROTECTED_PREFIXES,
  "/daily",
  "/examples",
  "/share",
] as const;

const INTENT_ALLOWLIST = new Set(["save"]);

function hasWebSession(request: NextRequest) {
  if (request.cookies.has(WEB_SESSION_COOKIE)) {
    return true;
  }

  return process.env.NODE_ENV !== "production" && Boolean(process.env.CLAREAD_WEB_DEBUG_SESSION_TOKEN);
}

function matchesPrefix(pathname: string, prefixes: readonly string[]) {
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function safeNextPath(pathname: string) {
  if (!matchesPrefix(pathname, NEXT_ALLOWLIST_PREFIXES)) {
    return null;
  }

  return pathname;
}

function safeIntent(value: string | null) {
  return value && INTENT_ALLOWLIST.has(value) ? value : null;
}

export function proxy(request: NextRequest) {
  const { pathname, searchParams } = request.nextUrl;

  if (!matchesPrefix(pathname, PROTECTED_PREFIXES) || hasWebSession(request)) {
    return NextResponse.next();
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  loginUrl.search = "";

  // Route boundary:
  // - /reader/r/:id and the current /reader/:id are private user readings.
  //   They require a Claread Web session even when an ID is shared manually.
  // - Public reading lives at /daily/:date and /examples/:slug and should be
  //   SSG/ISR-friendly without session checks.
  // - User-generated anonymous sharing must use /share/:token, authenticated by
  //   the share token and rendered as read-only content.
  // - Protected pages should never render anonymous empty states; proxy redirects
  //   them to /login before the route component runs.
  const nextPath = safeNextPath(pathname);
  if (nextPath) {
    loginUrl.searchParams.set("next", nextPath);
  }

  const intent = safeIntent(searchParams.get("intent"));
  if (intent) {
    loginUrl.searchParams.set("intent", intent);
  }

  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: [
    "/read/:path*",
    "/library/:path*",
    "/vocabulary/:path*",
    "/review/:path*",
    "/settings/:path*",
    "/reader/:path*",
  ],
};
