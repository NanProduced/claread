import { emailAuthResponse, getEmailAuthFlowStatus } from "@/services/bff/email-auth";

export async function GET() {
  return emailAuthResponse(await getEmailAuthFlowStatus());
}
