export interface LogoutResponseDto {
  ok: boolean;
}

export interface SessionInfoResponseDto {
  user_id: string;
  session_id: string;
  nickname: string;
  avatar_url: string;
  cumulative_article_count: number;
  settings: Record<string, unknown>;
}

export interface ProfileUpdateRequestDto {
  nickname?: string;
  avatar_url?: string;
  settings?: Record<string, unknown>;
}

export interface ProfileUpdateResponseDto {
  ok: boolean;
  updated: string[];
}
