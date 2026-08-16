import type { ReaderAnalysisProgressDto } from "@/types/api/reader-plate";

/** Test-only Snapshot fixture. Not a production default. */
export function makeAnalysisProgressDto(): ReaderAnalysisProgressDto {
  return {
    mode: "automatic",
    plan_version: "reader_analysis_sections_v1",
    overall_status: "queued",
    active_phase: null,
    translation_status: "not_started",
    completed_section_count: 0,
    total_section_count: 0,
    active_section_id: null,
    needs_user_action: false,
    last_progress_at: null,
    sections: [],
  };
}
