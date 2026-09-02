export type EmailStartResponseDto = {
  mode: "password" | "register";
  challenge_id?: string | null;
  expires_in?: number | null;
  resend_after?: number | null;
};

export type EmailOtpVerifyResponseDto = {
  ticket: string;
  expires_in: number;
};

export type EmailSessionResponseDto = {
  session_token: string;
  expires_at: string;
};

export type EmailPasswordResetResponseDto = {
  status: "accepted";
  challenge_id: string;
  expires_in: number;
  resend_after: number;
};
