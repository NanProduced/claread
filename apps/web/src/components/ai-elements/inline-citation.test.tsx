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
});
