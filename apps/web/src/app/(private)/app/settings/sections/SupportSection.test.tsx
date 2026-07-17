/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SupportSection } from "./SupportSection";

afterEach(cleanup);

vi.mock("../FeedbackForm", () => ({
  FeedbackForm: () => <div data-testid="feedback-form">FeedbackForm</div>,
}));

// MyFeedbackList is mocked to verify SupportSection does NOT render it
// directly (FeedbackForm renders it internally with refreshKey linkage).
vi.mock("../MyFeedbackList", () => ({
  MyFeedbackList: (props: { refreshKey?: number }) => (
    <div data-testid="my-feedback-list" data-refresh-key={props.refreshKey ?? 0}>
      MyFeedbackList
    </div>
  ),
}));

describe("SupportSection", () => {
  it("renders FeedbackForm", () => {
    render(<SupportSection />);
    expect(screen.getByTestId("feedback-form")).toBeTruthy();
  });

  it("does NOT render a standalone MyFeedbackList (FeedbackForm renders it internally)", () => {
    render(<SupportSection />);
    // SupportSection delegates to FeedbackForm, which already includes
    // MyFeedbackList with refreshKey linkage. A second standalone instance
    // would cause duplicate lists and break refresh-after-submit behavior.
    expect(screen.queryByTestId("my-feedback-list")).toBeNull();
  });
});
