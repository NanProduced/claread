/** @vitest-environment jsdom */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AssistantMessage } from "./AssistantMessage";

function EmptyDisclosure() {
  return null;
}

describe("AssistantMessage", () => {
  it("does not leave width containers for disclosures that render null", () => {
    const { container } = render(
      <AssistantMessage
        reasoning={<EmptyDisclosure />}
        process={<EmptyDisclosure />}
        answer={<div>Answer</div>}
      />,
    );

    expect(screen.getByText("Answer")).not.toBeNull();
    expect(
      Array.from(container.querySelectorAll("div")).filter((element) =>
        element.className.includes("max-w-[38rem]"),
      ),
    ).toHaveLength(1);
  });
});
