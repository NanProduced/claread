export type ReadingGoalDto = "exam" | "daily_reading" | "academic";

export type ReadingVariantDto =
  | "gaokao"
  | "cet"
  | "kaoyan"
  | "tem"
  | "ielts_toefl"
  | "beginner_reading"
  | "intermediate_reading"
  | "intensive_reading"
  | "academic_general";

export type TaskStatusDto =
  | "queued"
  | "running"
  | "finalizing"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired";

export interface TaskSubmitRequestDto {
  text: string;
  reading_goal: ReadingGoalDto;
  reading_variant: ReadingVariantDto;
  client_record_id?: string;
  source_type: "user_input";
  extended: boolean;
  wait_for_result: boolean;
  wait_timeout_seconds: number;
}

export interface TaskSubmitResponseDto {
  task_id: string;
  record_id: string;
  cloud_record_id: string;
  client_record_id?: string | null;
  status: TaskStatusDto;
  created: boolean;
  render_scene?: unknown;
}

export interface TaskStatusResponseDto {
  task_id: string;
  record_id: string;
  cloud_record_id: string;
  client_record_id?: string | null;
  status: TaskStatusDto;
  failure_code?: string | null;
  failure_message?: string | null;
  quota_cost_points: number;
  queued_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}
