import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { WEB_PHONE_COOKIE, WEB_SESSION_COOKIE } from "@/services/bff/session";
import {
  intentAllowlist,
  isAllowedNextPath,
  loginPath,
  matchesRoutePrefix,
  protectedRoutePrefixes,
} from "@/lib/routes";

function hasWebSession(request: NextRequest) {
  if (request.cookies.has(WEB_SESSION_COOKIE) || request.cookies.has(WEB_PHONE_COOKIE)) {
    return true;
  }

  return process.env.NODE_ENV !== "production" && Boolean(process.env.CLAREAD_WEB_DEBUG_SESSION_TOKEN);
}

function safeIntent(value: string | null) {
  return value && intentAllowlist.includes(value as (typeof intentAllowlist)[number]) ? value : null;
}

export function proxy(request: NextRequest) {
  const { pathname, searchParams } = request.nextUrl;

  if (!matchesRoutePrefix(pathname, protectedRoutePrefixes) || hasWebSession(request)) {
    return NextResponse.next();
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = loginPath;
  loginUrl.search = "";

  if (isAllowedNextPath(pathname)) {
    loginUrl.searchParams.set("next", pathname);
  }

  const intent = safeIntent(searchParams.get("intent"));
  if (intent) {
    loginUrl.searchParams.set("intent", intent);
  }

  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/app/:path*"],
};
