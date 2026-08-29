/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  INTAKE_WAITING_PHASES,
  ReadIntakeWaitingStage,
} from "./ReadIntakeWaitingStage";

describe("ReadIntakeWaitingStage", () => {
  afterEach(() => {
    cleanup();
  });
  it("defines exactly four real waiting phases matching backend contract", () => {
    expect(INTAKE_WAITING_PHASES).toHaveLength(4);
    expect(INTAKE_WAITING_PHASES.map((p) => p.id)).toEqual([
      "upload",
      "extract",
      "check",
      "prepare",
    ]);
    expect(INTAKE_WAITING_PHASES.map((p) => p.label)).toEqual([
      "上传文件",
      "提取正文",
      "检查内容",
      "准备阅读",
    ]);
  });

  it("renders phase 1 (上传文件) with stay-on-page guidance and upload headline", () => {
    render(
      <ReadIntakeWaitingStage
        phase="upload"
        filename="report.pdf"
        formatLabel="PDF"
        fileSize={1024 * 1024}
        canLeave={false}
      />,
    );

    expect(screen.getByTestId("read-intake-waiting-stage")).toBeTruthy();
    expect(screen.getByTestId("waiting-stage-headline").textContent).toBe(
      "正在上传文件…",
    );
    expect(screen.queryByTestId("waiting-stage-subtitle")).toBeNull();
    expect(screen.getByTestId("waiting-stage-file-chip").textContent).toContain(
      "report.pdf",
    );
    expect(screen.getByTestId("waiting-stage-file-chip").textContent).toContain(
      "PDF",
    );

    // All 4 phase labels are visible in the progress rail
    expect(screen.getByText("上传文件")).toBeTruthy();
    expect(screen.getByText("提取正文")).toBeTruthy();
    expect(screen.getByText("检查内容")).toBeTruthy();
    expect(screen.getByText("准备阅读")).toBeTruthy();

    // In phase 1, upload is active/current, others are unstarted
    const activeStep = screen.getByTestId("waiting-phase-step-upload");
    expect(activeStep.getAttribute("data-state")).toBe("current");
    expect(
      screen
        .getByTestId("waiting-phase-step-extract")
        .getAttribute("data-state"),
    ).toBe("upcoming");
  });

  it("renders phase 2 (提取正文) with reassurance copy allowing page exit", () => {
    render(
      <ReadIntakeWaitingStage
        phase="extract"
        filename="paper.md"
        formatLabel="Markdown"
        canLeave={true}
      />,
    );

    expect(screen.getByTestId("waiting-stage-headline").textContent).toBe(
      "正在提取正文…",
    );
    expect(screen.getByTestId("waiting-stage-subtitle").textContent).toBe(
      "离开本页不会影响透读，完成后会保存到阅读记录",
    );

    // Upload step is completed, extract is current
    expect(
      screen.getByTestId("waiting-phase-step-upload").getAttribute("data-state"),
    ).toBe("completed");
    expect(
      screen
        .getByTestId("waiting-phase-step-extract")
        .getAttribute("data-state"),
    ).toBe("current");
    expect(
      screen.getByTestId("waiting-phase-step-check").getAttribute("data-state"),
    ).toBe("upcoming");
    expect(
      screen
        .getByTestId("waiting-phase-step-prepare")
        .getAttribute("data-state"),
    ).toBe("upcoming");
  });

  it("renders phase 3 (检查内容) with completed previous steps", () => {
    render(
      <ReadIntakeWaitingStage
        phase="check"
        filename="notes.txt"
        canLeave={true}
      />,
    );

    expect(screen.getByTestId("waiting-stage-headline").textContent).toBe(
      "正在检查内容与排版…",
    );
    expect(screen.getByTestId("waiting-stage-subtitle").textContent).toBe(
      "离开本页不会影响透读，完成后会保存到阅读记录",
    );

    expect(
      screen.getByTestId("waiting-phase-step-upload").getAttribute("data-state"),
    ).toBe("completed");
    expect(
      screen
        .getByTestId("waiting-phase-step-extract")
        .getAttribute("data-state"),
    ).toBe("completed");
    expect(
      screen.getByTestId("waiting-phase-step-check").getAttribute("data-state"),
    ).toBe("current");
    expect(
      screen
        .getByTestId("waiting-phase-step-prepare")
        .getAttribute("data-state"),
    ).toBe("upcoming");
  });

  it("renders phase 4 (准备阅读) with preparation headline", () => {
    render(
      <ReadIntakeWaitingStage
        phase="prepare"
        canLeave={true}
      />,
    );

    expect(screen.getByTestId("waiting-stage-headline").textContent).toBe(
      "正在准备阅读环境…",
    );
    expect(
      screen
        .getByTestId("waiting-phase-step-prepare")
        .getAttribute("data-state"),
    ).toBe("current");
  });

  it("maintains visual quietness without giant illustrations, fake percentages, or countdown timers", () => {
    const { container } = render(
      <ReadIntakeWaitingStage
        phase="extract"
        filename="article.pdf"
        canLeave={true}
      />,
    );

    // No giant images or illustrations
    expect(container.querySelector("img")).toBeNull();
    // No percent numbers or fake timers
    const text = container.textContent ?? "";
    expect(text).not.toContain("%");
    expect(text).not.toMatch(/\d{1,2}:\d{2}/);
    expect(text).not.toContain("剩余时间");
  });

  it("respects accessibility semantics and prefers-reduced-motion", () => {
    const { container } = render(
      <ReadIntakeWaitingStage
        phase="extract"
        canLeave={true}
      />,
    );

    const stage = screen.getByTestId("read-intake-waiting-stage");
    expect(stage.getAttribute("role")).toBe("status");
    expect(stage.getAttribute("aria-live")).toBe("polite");

    // Breathing dot has reduced-motion guard
    const animatedElements = container.querySelectorAll(".motion-safe\\:animate-pulse, .motion-safe\\:animate-ping");
    expect(animatedElements.length).toBeGreaterThan(0);
    animatedElements.forEach((el) => {
      expect(el.className).toContain("motion-reduce:animate-none");
    });
  });
});
