/** @vitest-environment jsdom */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { CitationList } from "./CitationList";
import type { ReaderAskCitationDto } from "@/types/api/reader-ask";

afterEach(cleanup);

const citations: ReaderAskCitationDto[] = [
  { citation_id: "c1", kind: "anchor", label: "第3句", metadata_json: {} },
  { citation_id: "c2", kind: "vocabulary", label: "ubiquitous", metadata_json: {} },
  { citation_id: "c3", kind: "dictionary_entry", label: "test entry", source_article_title: "另一篇文章", metadata_json: {} },
];

describe("CitationList", () => {
  it("renders citation labels with indices", () => {
    render(<CitationList citations={citations} />);
    expect(screen.getByText(/第3句/)).toBeTruthy();
    expect(screen.getByText(/ubiquitous/)).toBeTruthy();
  });

  it("shows source article title when present", () => {
    render(<CitationList citations={citations} />);
    expect(screen.getByText("另一篇文章")).toBeTruthy();
  });

  it("returns null for empty citations", () => {
    const { container } = render(<CitationList citations={[]} />);
    expect(container.innerHTML).toBe("");
  });
});
