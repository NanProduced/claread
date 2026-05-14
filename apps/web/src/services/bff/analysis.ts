import "server-only";

import { randomUUID } from "node:crypto";

import {
  getUpstreamAnalysisTaskStatus,
  submitUpstreamAnalysisTask,
} from "@/services/api/tasks";
import { getWebSession } from "@/services/bff/session";
import type {
  ReadingGoalDto,
  ReadingVariantDto,
  TaskSubmitRequestDto,
  TaskStatusDto,
} from "@/types/api/tasks";

const DEFAULT_WAIT_TIMEOUT_SECONDS = 60;

const GOAL_DEFAULT_VARIANT: Record<ReadingGoalDto, ReadingVariantDto> = {
  exam: "cet",
  daily_reading: "intermediate_reading",
  academic: "academic_general",
};

export type WebAnalysisSubmitResult =
  | {
      ok: true;
      taskId: string;
      recordId: string;
      status: TaskStatusDto;
      readerUrl: string;
      message: string;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
      taskId?: string;
      recordId?: string;
    };

export type WebAnalysisTaskStatusResult =
  | {
      ok: true;
      taskId: string;
      recordId: string;
      status: TaskStatusDto;
      readerUrl: string;
      failureCode?: string | null;
      failureMessage?: string | null;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
    };

function normalizeText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeReadingGoal(value: unknown): ReadingGoalDto {
  if (value === "exam" || value === "academic" || value === "daily_reading") {
    return value;
  }

  return "daily_reading";
}

function readStringField(payload: unknown, field: string): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[field];
  return typeof value === "string" ? value : undefined;
}

function readCloudRecordId(payload: unknown): string | undefined {
  return readStringField(payload, "cloud_record_id");
}

function upstreamErrorCode(status: number): string {
  if (status === 401) {
    return "upstream_auth_failed";
  }
  if (status === 402) {
    return "insufficient_credits";
  }
  if (status === 409) {
    return "active_task_exists";
  }
  if (status === 422) {
    return "task_rejected";
  }
  if (status === 0) {
    return "upstream_unavailable";
  }
  return "upstream_error";
}

export async function submitAnalysisFromWeb(input: {
  text?: unknown;
  readingGoal?: unknown;
}): Promise<WebAnalysisSubmitResult> {
  const text = normalizeText(input.text);

  if (!text) {
    return {
      ok: false,
      status: 400,
      code: "empty_text",
      message: "请先粘贴需要解析的英文内容。",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能提交真实解析，请使用真实登录会话后再试。"
          : "请先登录后再提交真实解析。",
    };
  }

  const readingGoal = normalizeReadingGoal(input.readingGoal);
  const payload: TaskSubmitRequestDto = {
    text,
    reading_goal: readingGoal,
    reading_variant: GOAL_DEFAULT_VARIANT[readingGoal],
    client_record_id: `web-${randomUUID()}`,
    source_type: "user_input",
    extended: readingGoal === "academic",
    wait_for_result: true,
    wait_timeout_seconds: DEFAULT_WAIT_TIMEOUT_SECONDS,
  };

  const upstreamResult = await submitUpstreamAnalysisTask(payload, session.sessionToken);

  if (!upstreamResult.ok) {
    return {
      ok: false,
      status: upstreamResult.status,
      code: upstreamErrorCode(upstreamResult.status),
      message: upstreamResult.message,
      taskId: readStringField(upstreamResult.payload, "task_id"),
      recordId: readCloudRecordId(upstreamResult.payload),
    };
  }

  const recordId = upstreamResult.data.cloud_record_id;

  if (!recordId) {
    return {
      ok: false,
      status: 502,
      code: "upstream_contract_mismatch",
      message: "解析任务已提交，但上游没有返回 cloud_record_id。",
      taskId: upstreamResult.data.task_id,
    };
  }

  return {
    ok: true,
    taskId: upstreamResult.data.task_id,
    recordId,
    status: upstreamResult.data.status,
    readerUrl: `/reader/${recordId}`,
    message:
      upstreamResult.data.status === "succeeded"
        ? "解析完成，正在打开 Reader。"
        : "解析任务已提交，正在打开 Reader。",
  };
}

export async function getAnalysisTaskStatusFromWeb(
  taskId: string,
): Promise<WebAnalysisTaskStatusResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message: "请先登录后再查询解析任务。",
    };
  }

  const upstreamResult = await getUpstreamAnalysisTaskStatus(taskId, session.sessionToken);

  if (!upstreamResult.ok) {
    return {
      ok: false,
      status: upstreamResult.status,
      code: upstreamErrorCode(upstreamResult.status),
      message: upstreamResult.message,
    };
  }

  const recordId = upstreamResult.data.cloud_record_id;

  if (!recordId) {
    return {
      ok: false,
      status: 502,
      code: "upstream_contract_mismatch",
      message: "任务状态已返回，但上游没有返回 cloud_record_id。",
    };
  }

  return {
    ok: true,
    taskId: upstreamResult.data.task_id,
    recordId,
    status: upstreamResult.data.status,
    readerUrl: `/reader/${recordId}`,
    failureCode: upstreamResult.data.failure_code,
    failureMessage: upstreamResult.data.failure_message,
  };
}
