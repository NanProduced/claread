export type EmailStartResponseDto = {
  challenge_id: string;
  expires_in: number;
  resend_after: number;
};

export type EmailOtpVerifyResponseDto = {
  ticket: string;
  purpose: "register" | "password_reset";
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
