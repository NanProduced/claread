import { cancelEmailAuthFlow, emailAuthResponse } from "@/services/bff/email-auth";

export async function POST() {
  return emailAuthResponse(await cancelEmailAuthFlow());
}
