import { emailAuthResponse, startEmailAuth } from "@/services/bff/email-auth";

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  return emailAuthResponse(await startEmailAuth(body));
}
