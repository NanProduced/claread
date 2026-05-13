export interface PhoneCodeRequestDto {
  phone: string;
}

export interface PhoneCodeResponseDto {
  ok: boolean;
  message: string;
  normalized_phone?: string | null;
}

export interface PhoneVerifyRequestDto {
  phone: string;
  code: string;
}

export interface PhoneVerifyResponseDto {
  user_id: string;
  session_token: string;
  expires_at: string;
}

export interface LogoutResponseDto {
  ok: boolean;
}
