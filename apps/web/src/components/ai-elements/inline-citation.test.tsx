/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationCarousel,
  InlineCitationCarouselContent,
  InlineCitationCarouselHeader,
  InlineCitationCarouselIndex,
  InlineCitationCarouselItem,
  InlineCitationCarouselNext,
  InlineCitationCarouselPrev,
  InlineCitationQuote,
  InlineCitationSource,
} from "./inline-citation";

describe("InlineCitation", () => {
  it("opens its preview on hover and remains available to click and keyboard users", () => {
    render(
      <InlineCitation>
        <InlineCitationCard>
          <InlineCitationCardTrigger>c1 +5</InlineCitationCardTrigger>
          <InlineCitationCardBody>Preview</InlineCitationCardBody>
        </InlineCitationCard>
      </InlineCitation>,
    );

    const trigger = screen.getByRole("button", { name: "c1 +5" });
    fireEvent.pointerEnter(trigger, { pointerType: "mouse" });

    expect(screen.getByText("Preview")).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });

  it("uses the citation-item count and conventional previous/next order", () => {
    render(
      <InlineCitationCarousel count={6}>
        <InlineCitationCarouselHeader>
          <InlineCitationCarouselPrev />
          <InlineCitationCarouselNext />
          <InlineCitationCarouselIndex />
        </InlineCitationCarouselHeader>
        <InlineCitationCarouselContent>
          {Array.from({ length: 6 }, (_, index) => (
            <InlineCitationCarouselItem key={index}>{`item-${index + 1}`}</InlineCitationCarouselItem>
          ))}
        </InlineCitationCarouselContent>
      </InlineCitationCarousel>,
    );

    expect(screen.getByText("1/6")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "下一条来源" }));
    expect(screen.getByText("2/6")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "上一条来源" }));
    expect(screen.getByText("1/6")).not.toBeNull();
  });

  it("shows one evidence title and expands a clamped quote accessibly", () => {
    render(
      <InlineCitation defaultOpen>
        <InlineCitationCard>
          <InlineCitationCardTrigger>[1]</InlineCitationCardTrigger>
          <InlineCitationCardBody>
            <InlineCitationSource>
              <InlineCitationQuote>
                This is a deliberately long evidence excerpt that needs a compact preview before the reader chooses to expand the complete source passage.
              </InlineCitationQuote>
            </InlineCitationSource>
          </InlineCitationCardBody>
        </InlineCitationCard>
      </InlineCitation>,
    );

    expect(screen.getAllByText("文章依据")).toHaveLength(1);
    expect(screen.queryByText("文章内依据")).toBeNull();
    const toggle = screen.getByRole("button", { name: "展开完整证据片段" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toBe("展开摘录");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(
      screen.getByRole("button", { name: "收起完整证据片段" }).textContent,
    ).toBe("收起摘录");
  });
});
