/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AssistantMessage } from "./AssistantMessage";

afterEach(cleanup);

function EmptyDisclosure() {
  return null;
}

describe("AssistantMessage", () => {
  it("does not leave width containers for disclosures that render null", () => {
    const { container } = render(
      <AssistantMessage
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

  it("renders the single process slot above the answer", () => {
    render(
      <AssistantMessage
        process={<div>ProcessOwner</div>}
        answer={<div>Answer</div>}
      />,
    );

    const processNode = screen.getByText("ProcessOwner");
    const answer = screen.getByText("Answer");
    // Document order: the one process disclosure sits above the answer.
    expect(processNode.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
