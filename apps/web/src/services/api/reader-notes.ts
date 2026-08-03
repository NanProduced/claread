import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  ReaderNoteCreateRequestDto,
  ReaderNoteResponseDto,
  ReaderNoteUpdateRequestDto,
} from "@/types/api/reader-notes";

export function createReaderNote(
  sessionToken: string,
  body: ReaderNoteCreateRequestDto,
): Promise<UpstreamResult<ReaderNoteResponseDto>> {
  return fastApiFetch<ReaderNoteResponseDto>("/reader-notes", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(body),
  });
}

export function updateReaderNote(
  sessionToken: string,
  noteId: string,
  body: ReaderNoteUpdateRequestDto,
): Promise<UpstreamResult<ReaderNoteResponseDto>> {
  return fastApiFetch<ReaderNoteResponseDto>(`/reader-notes/${encodeURIComponent(noteId)}`, {
    method: "PATCH",
    sessionToken,
    body: JSON.stringify(body),
  });
}

export function deleteReaderNote(
  sessionToken: string,
  noteId: string,
): Promise<UpstreamResult<{ ok: boolean }>> {
  return fastApiFetch<{ ok: boolean }>(`/reader-notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
    sessionToken,
  });
}
