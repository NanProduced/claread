/** @vitest-environment jsdom */

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";
import type {
  ReaderAnalysisOverallStatus,
  ReaderAnalysisProgressDto,
  ReaderAnalysisSectionProgressDto,
} from "@/types/api/reader-plate";

import { ReaderAnalysisProgressControl } from "./ReaderAnalysisProgressControl";

function makeSection(
  overrides: Partial<ReaderAnalysisSectionProgressDto> = {},
): ReaderAnalysisSectionProgressDto {
  return {
    section_id: "ras1_a",
    order_index: 0,
    label: "第一部分",
    excerpt: "A scarce few can turn talent into impact.",
    start_unit_id: "u1",
    end_unit_id: "u2",
    status: "not_started",
    vocabulary_status: "not_started",
    grammar_status: "not_started",
    can_start: false,
    updated_at: null,
    failure_code: null,
    ...overrides,
  };
}

function makeProgress(
  overrides: Partial<ReaderAnalysisProgressDto> = {},
): ReaderAnalysisProgressDto {
  return {
    ...makeAnalysisProgressDto(),
    ...overrides,
  };
}

function makeLegalSections(
  specs: Array<Partial<ReaderAnalysisSectionProgressDto>>,
): ReaderAnalysisSectionProgressDto[] {
  return specs.map((spec, index) =>
    makeSection({
      section_id: `ras1_${String.fromCharCode(97 + index)}`,
      order_index: index,
      label: `第${index + 1}部分`,
      ...spec,
    }),
  );
}

function makeLegalSegmented(
  overrides: Partial<ReaderAnalysisProgressDto> = {},
  sectionSpecs?: Array<Partial<ReaderAnalysisSectionProgressDto>>,
): ReaderAnalysisProgressDto {
  const { sections: overrideSections, total_section_count, ...rest } = overrides;
  const sections =
    overrideSections ??
    makeLegalSections(
      sectionSpecs ?? [{ status: "completed", can_start: false }, { can_start: true }],
    );
  return makeProgress({
    mode: "segmented_on_demand",
    overall_status: "waiting_user",
    completed_section_count: 1,
    ...rest,
    sections,
    total_section_count: total_section_count ?? sections.length,
  });
}

function renderControl(
  progress: ReaderAnalysisProgressDto,
  options: {
    recordId?: string;
    onRequestSnapshotReload?: () => void | Promise<void>;
  } = {},
) {
  return render(
    <ReaderAnalysisProgressControl
      recordId={options.recordId ?? "rec_1"}
      progress={progress}
      onRequestSnapshotReload={options.onRequestSnapshotReload}
    />,
  );
}

async function openPopover() {
  await userEvent.click(screen.getByTestId("reader-analysis-progress-trigger"));
  return screen.findByTestId("reader-analysis-progress-popover");
}

function mockFetchOutcome(payload: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
}

beforeEach(() => {
  mockFetchOutcome({
    ok: true,
    outcome: "started",
    accepted_section_ids: ["ras1_a"],
    event_sequence: 2,
    reason_code: null,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ReaderAnalysisProgressControl collapsed labels", () => {
  it.each([
    [{ overall_status: "queued" as const, active_phase: "translation" as const }, "准备译文"],
    [{ overall_status: "queued" as const, active_phase: null }, "等待解析"],
    [{ overall_status: "processing" as const, active_phase: "translation" as const }, "准备译文"],
    [{ overall_status: "processing" as const, active_phase: "analysis" as const }, "解析中"],
    [{ overall_status: "waiting_user" as const, active_phase: null }, "可继续解析"],
    [{ overall_status: "completed" as const, active_phase: null }, "解析完成"],
    [{ overall_status: "partial" as const, active_phase: null }, "部分完成"],
    [{ overall_status: "failed" as const, active_phase: null }, "需要处理"],
    [{ overall_status: "paused_quota" as const, active_phase: null }, "解析已暂停"],
  ])("maps %j to %s", (fields, label) => {
    renderControl(makeProgress(fields));
    const trigger = screen.getByTestId("reader-analysis-progress-trigger");
    expect(trigger.getAttribute("aria-label")).toBe(label);
    expect(trigger.textContent).toContain(label);
    if (fields.overall_status === "processing") {
      expect(trigger.querySelector("svg.animate-spin")).not.toBeNull();
    } else {
      expect(trigger.querySelector("svg.animate-spin")).toBeNull();
    }
  });

  it("keeps an aria-label on the narrow trigger", () => {
    renderControl(makeProgress({ overall_status: "waiting_user" }));
    expect(screen.getByTestId("reader-analysis-progress-trigger").getAttribute("aria-label")).toBe(
      "可继续解析",
    );
  });
});

describe("ReaderAnalysisProgressControl modes", () => {
  it("does not show section actions in automatic mode", async () => {
    renderControl(
      makeProgress({
        mode: "automatic",
        overall_status: "processing",
        active_phase: "analysis",
        sections: [makeSection({ can_start: true })],
      }),
    );
    const popover = await openPopover();
    expect(popover.textContent).toContain("这篇文章会自动完成译文、词汇与语法解析。");
    expect(within(popover).queryByRole("button", { name: "解析这一部分" })).toBeNull();
    expect(within(popover).queryByRole("button", { name: "解析全部剩余部分" })).toBeNull();
  });

  it("shows ordered sections and real counts in segmented mode", async () => {
    renderControl(
      makeLegalSegmented(
        {
          overall_status: "waiting_user",
          completed_section_count: 1,
        },
        [
          { label: "第一部分", status: "completed", can_start: false },
          { label: "第二部分", can_start: true },
          { label: "第三部分", can_start: false },
          { label: "第四部分", can_start: false },
        ],
      ),
    );
    const popover = await openPopover();
    expect(popover.textContent).toContain("已完成 1 / 4 部分");
    const titles = within(popover)
      .getAllByText(/第.+部分/)
      .map((node) => node.textContent);
    expect(titles[0]).toContain("第一部分");
    expect(titles[1]).toContain("第二部分");
    expect(within(popover).getByRole("button", { name: "解析这一部分" })).toBeTruthy();
  });

  it("falls back to a 1-based part title when the backend label is empty", async () => {
    renderControl(
      makeLegalSegmented({ completed_section_count: 0, overall_status: "processing" }, [
        { label: "", order_index: 0, can_start: true },
      ]),
    );
    const popover = await openPopover();
    expect(within(popover).getByText("第 1 部分")).toBeTruthy();
  });

  it("does not show a start action when can_start is false", async () => {
    renderControl(
      makeLegalSegmented(
        {
          overall_status: "processing",
          completed_section_count: 0,
        },
        [{ can_start: false, failure_code: "internal_lane_timeout" }],
      ),
    );
    const popover = await openPopover();
    expect(within(popover).queryByRole("button", { name: "解析这一部分" })).toBeNull();
    expect(popover.textContent).not.toContain("internal_lane_timeout");
  });

  it("survives empty or inconsistent section data without dangerous actions", async () => {
    renderControl(
      makeProgress({
        mode: "segmented_on_demand",
        overall_status: "partial",
        completed_section_count: 3,
        total_section_count: 1,
        active_section_id: null,
        sections: [],
      }),
    );
    expect(screen.getByTestId("reader-analysis-progress-trigger").getAttribute("aria-label")).toBe(
      "部分完成",
    );
    const popover = await openPopover();
    expect(popover.textContent).toContain("解析详情暂时无法更新，请稍后重试。");
    expect(popover.textContent).not.toContain("3 / 1");
    expect(within(popover).queryByRole("button", { name: "解析这一部分" })).toBeNull();
    expect(within(popover).queryByRole("button", { name: "解析全部剩余部分" })).toBeNull();
  });
});

describe("ReaderAnalysisProgressControl requests", () => {
  it("posts a single-section body", async () => {
    renderControl(makeLegalSegmented());
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "解析这一部分" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/web/reader/records/rec_1/analysis-sections/requests",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ scope: "single", sectionId: "ras1_b" }),
      }),
    );
  });

  it("posts a remaining body", async () => {
    renderControl(
      makeLegalSegmented({}, [
        { status: "completed", can_start: false },
        { can_start: true },
        { can_start: true },
      ]),
    );
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "解析全部剩余部分" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/web/reader/records/rec_1/analysis-sections/requests",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ scope: "remaining", sectionId: null }),
      }),
    );
  });

  it.each([
    ["started", null, "已开始解析"],
    ["already_active", null, "正在解析中"],
    ["already_complete", null, "这部分已经完成"],
    ["paused_quota", null, "当前积分不足，解析已暂停"],
    [
      "rejected",
      "analysis_mode_not_segmented",
      "当前文章会自动完成解析，无需手动开始",
    ],
    ["rejected", "analysis_section_not_found", "文章内容已更新，请刷新后重试"],
    ["rejected", "analysis_section_not_runnable", "这一部分当前暂时无法开始"],
    ["rejected", "mystery_internal_code", "当前暂时无法开始解析，请刷新后重试"],
  ] as const)("shows outcome %s without raw codes", async (outcome, reason, label) => {
    mockFetchOutcome({
      ok: true,
      outcome,
      accepted_section_ids: [],
      event_sequence: null,
      reason_code: reason,
    });
    renderControl(
      makeLegalSegmented({}, [
        { status: "completed", can_start: false },
        { can_start: true, failure_code: "capability_failed_internal" },
      ]),
    );
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: /这一部分/ }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      label,
    );
    const popover = screen.getByTestId("reader-analysis-progress-popover");
    expect(popover.textContent).not.toContain("mystery_internal_code");
    expect(popover.textContent).not.toContain("capability_failed_internal");
    if (reason) {
      expect(popover.textContent).not.toContain(reason);
    }
  });

  it("does not submit twice while a request is in flight", async () => {
    let resolveFetch: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise<Response>((resolve) => {
            resolveFetch = resolve;
          }),
      ),
    );
    renderControl(makeLegalSegmented());
    await openPopover();
    const start = screen.getByRole("button", { name: "解析这一部分" });
    await userEvent.click(start);
    await userEvent.click(start);
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveFetch?.(
        new Response(
          JSON.stringify({
            ok: true,
            outcome: "started",
            accepted_section_ids: ["ras1_a"],
            event_sequence: 1,
            reason_code: null,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    });
  });

  it("reloads the snapshot after a successful outcome", async () => {
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    renderControl(makeLegalSegmented(), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "解析这一部分" }));
    await waitFor(() => {
      expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    });
  });
});

describe("ReaderAnalysisProgressControl auto-open", () => {
  const waitingUser = makeLegalSegmented({
    plan_version: "reader_analysis_sections_v1",
    overall_status: "waiting_user",
    completed_section_count: 1,
  });
  const processingTwo = makeLegalSegmented(
    {
      overall_status: "processing",
      completed_section_count: 0,
    },
    [{ can_start: false }, { can_start: false }],
  );

  it("does not auto-open when first mounted as waiting_user", () => {
    renderControl(waitingUser);
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
  });

  it("auto-opens once after entering waiting_user", () => {
    const { rerender } = renderControl(processingTwo);
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
    rerender(
      <ReaderAnalysisProgressControl recordId="rec_1" progress={waitingUser} />,
    );
    expect(screen.getByTestId("reader-analysis-progress-popover")).toBeTruthy();
  });

  it("does not auto-open again after the user closes it", async () => {
    const { rerender } = renderControl(processingTwo);
    rerender(
      <ReaderAnalysisProgressControl recordId="rec_1" progress={waitingUser} />,
    );
    expect(screen.getByTestId("reader-analysis-progress-popover")).toBeTruthy();
    await userEvent.click(screen.getByTestId("reader-analysis-progress-trigger"));
    await waitFor(() => {
      expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
    });
    rerender(
      <ReaderAnalysisProgressControl recordId="rec_1" progress={waitingUser} />,
    );
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
  });

  it("rebuilds the observation boundary when recordId or plan_version changes", () => {
    const { rerender } = renderControl(processingTwo);
    rerender(
      <ReaderAnalysisProgressControl recordId="rec_1" progress={waitingUser} />,
    );
    expect(screen.getByTestId("reader-analysis-progress-popover")).toBeTruthy();

    rerender(
      <ReaderAnalysisProgressControl
        recordId="rec_2"
        progress={{ ...waitingUser, plan_version: "reader_analysis_sections_v2" }}
      />,
    );
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();

    rerender(
      <ReaderAnalysisProgressControl
        recordId="rec_2"
        progress={{
          ...waitingUser,
          plan_version: "reader_analysis_sections_v2",
          overall_status: "processing" as ReaderAnalysisOverallStatus,
        }}
      />,
    );
    rerender(
      <ReaderAnalysisProgressControl
        recordId="rec_2"
        progress={{ ...waitingUser, plan_version: "reader_analysis_sections_v2" }}
      />,
    );
    expect(screen.getByTestId("reader-analysis-progress-popover")).toBeTruthy();
  });

  it("does not steal focus on programmatic open", () => {
    const { rerender } = renderControl(processingTwo);
    const outside = document.createElement("button");
    outside.textContent = "outside";
    document.body.appendChild(outside);
    outside.focus();
    rerender(
      <ReaderAnalysisProgressControl recordId="rec_1" progress={waitingUser} />,
    );
    const popover = screen.getByTestId("reader-analysis-progress-popover");
    expect(popover.contains(document.activeElement)).toBe(false);
    outside.remove();
  });

  it("does not auto-open when sections are missing at runtime", () => {
    const { rerender } = renderControl(processingTwo);
    rerender(
      <ReaderAnalysisProgressControl
        recordId="rec_1"
        progress={{
          ...waitingUser,
          sections: undefined as unknown as ReaderAnalysisProgressDto["sections"],
        }}
      />,
    );
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
    expect(screen.getByTestId("reader-analysis-progress-trigger")).toBeTruthy();
  });
});

describe("ReaderAnalysisProgressControl request error boundaries", () => {
  it("keeps the started outcome when snapshot reload rejects", async () => {
    const onRequestSnapshotReload = vi.fn().mockRejectedValue(new Error("reload failed"));
    renderControl(makeLegalSegmented(), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "解析这一部分" }));
    const feedback = await screen.findByTestId("reader-analysis-progress-feedback");
    expect(feedback.textContent).toBe("已开始解析，状态暂未刷新，请稍后再试。");
    expect(feedback.textContent).toContain("已开始解析");
    expect(feedback.textContent).not.toContain("无法开始解析");
    expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "解析这一部分" }).hasAttribute("disabled")).toBe(
      false,
    );
  });

  it("shows a safe failure when the transport request rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    renderControl(makeLegalSegmented());
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "解析这一部分" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "当前暂时无法开始解析，请刷新后重试。",
    );
  });

  it("does not reload the snapshot after a rejected outcome", async () => {
    const onRequestSnapshotReload = vi.fn();
    mockFetchOutcome({
      ok: true,
      outcome: "rejected",
      accepted_section_ids: [],
      event_sequence: null,
      reason_code: "analysis_mode_not_segmented",
    });
    renderControl(makeLegalSegmented(), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "解析这一部分" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "当前文章会自动完成解析，无需手动开始",
    );
    expect(onRequestSnapshotReload).not.toHaveBeenCalled();
  });
});

describe("ReaderAnalysisProgressControl fail-closed details", () => {
  async function expectDetailsClosed(progress: ReaderAnalysisProgressDto) {
    renderControl(progress);
    const popover = await openPopover();
    expect(popover.textContent).toContain("解析详情暂时无法更新，请稍后重试。");
    expect(popover.textContent).not.toContain(" / ");
    expect(within(popover).queryByRole("button", { name: "解析这一部分" })).toBeNull();
    expect(within(popover).queryByRole("button", { name: "重试这一部分" })).toBeNull();
    expect(within(popover).queryByRole("button", { name: "解析全部剩余部分" })).toBeNull();
  }

  it("hides counts and actions when completed exceeds total", async () => {
    await expectDetailsClosed(
      makeProgress({
        mode: "segmented_on_demand",
        overall_status: "partial",
        completed_section_count: 3,
        total_section_count: 1,
        sections: [makeSection({ can_start: true })],
      }),
    );
    expect(screen.getByTestId("reader-analysis-progress-popover").textContent).not.toContain(
      "3 / 1",
    );
  });

  it("hides actions when total does not match sections.length", async () => {
    await expectDetailsClosed(
      makeProgress({
        mode: "segmented_on_demand",
        overall_status: "waiting_user",
        completed_section_count: 1,
        total_section_count: 4,
        sections: makeLegalSections([{ can_start: false }, { can_start: true }]),
      }),
    );
  });

  it("hides actions when section_id is duplicated", async () => {
    await expectDetailsClosed(
      makeLegalSegmented({
        sections: makeLegalSections([
          { section_id: "ras1_dup", can_start: false },
          { section_id: "ras1_dup", can_start: true },
        ]),
      }),
    );
  });

  it("hides actions when order_index is duplicated", async () => {
    await expectDetailsClosed(
      makeLegalSegmented({
        sections: makeLegalSections([
          { order_index: 0, can_start: false },
          { order_index: 0, can_start: true },
        ]),
      }),
    );
  });

  it("hides actions when order_index is negative or not an integer", async () => {
    await expectDetailsClosed(
      makeLegalSegmented({
        sections: [
          makeSection({ section_id: "ras1_a", order_index: 0.5, can_start: true }),
        ],
        completed_section_count: 0,
        total_section_count: 1,
      }),
    );
  });

  it("hides actions when a capability status is unknown", async () => {
    await expectDetailsClosed(
      makeLegalSegmented({
        sections: [
          makeSection({
            status: "mystery" as ReaderAnalysisSectionProgressDto["status"],
            can_start: true,
          }),
        ],
        completed_section_count: 0,
        total_section_count: 1,
      }),
    );
  });

  it("hides actions when active_section_id is missing from sections", async () => {
    await expectDetailsClosed(
      makeLegalSegmented({
        active_section_id: "ras1_missing",
      }),
    );
  });

  it("still shows legal segmented actions", async () => {
    renderControl(makeLegalSegmented());
    const popover = await openPopover();
    expect(popover.textContent).toContain("已完成 1 / 2 部分");
    expect(within(popover).getByRole("button", { name: "解析这一部分" })).toBeTruthy();
  });

  it.each([
    ["null", [null]],
    ["string", ["section"]],
    ["number", [1]],
    ["array", [[]]],
    ["empty object", [{}]],
  ] as const)("fail-closes when a section element is %s", async (_label, sections) => {
    renderControl(
      makeProgress({
        mode: "segmented_on_demand",
        overall_status: "waiting_user",
        completed_section_count: 0,
        total_section_count: 1,
        sections: sections as unknown as ReaderAnalysisProgressDto["sections"],
      }),
    );
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
    expect(screen.getByTestId("reader-analysis-progress-trigger").getAttribute("aria-label")).toBe(
      "可继续解析",
    );
    const popover = await openPopover();
    expect(popover.textContent).toContain("解析详情暂时无法更新，请稍后重试。");
    expect(popover.textContent).not.toContain(" / ");
    expect(within(popover).queryByRole("button", { name: "解析这一部分" })).toBeNull();
    expect(within(popover).queryByRole("button", { name: "解析全部剩余部分" })).toBeNull();
  });

  it("fail-closes when two section ids match after trim", async () => {
    await expectDetailsClosed(
      makeLegalSegmented({
        sections: makeLegalSections([
          { section_id: "ras1_same", can_start: false },
          { section_id: " ras1_same ", can_start: true },
        ]),
      }),
    );
  });

  it("fail-closes when section_id has surrounding whitespace", async () => {
    await expectDetailsClosed(
      makeLegalSegmented(
        {
          completed_section_count: 0,
        },
        [{ section_id: " ras1_a ", can_start: true }],
      ),
    );
  });
});

describe("ReaderAnalysisProgressControl completed copy and focus", () => {
  it("keeps collapsed 解析完成 and shows whole-article completion in the popover", async () => {
    renderControl(
      makeProgress({
        mode: "automatic",
        overall_status: "completed",
        translation_status: "completed",
      }),
    );
    expect(screen.getByTestId("reader-analysis-progress-trigger").getAttribute("aria-label")).toBe(
      "解析完成",
    );
    const popover = await openPopover();
    expect(popover.textContent).toContain("译文、词汇与语法解析已完成。");
    expect(popover.textContent).not.toContain("译文已完成");
  });

  it("uses a semantic focus-visible ring instead of a permanent native outline", async () => {
    renderControl(
      makeProgress({
        mode: "automatic",
        overall_status: "completed",
      }),
    );
    const popover = await openPopover();
    expect(popover.className).toContain("outline-none");
    expect(popover.className).toContain("focus-visible:outline-solid");
    expect(popover.className).toContain("focus-visible:outline-2");
    expect(popover.className).toContain("focus-visible:outline-offset-2");
    expect(popover.className).toContain("focus-visible:outline-focus-ring/30");
  });
});

describe("ReaderAnalysisProgressControl manual recovery", () => {
  const failedAutomatic = {
    overall_status: "failed" as const,
    translation_status: "failed" as const,
  };

  function mockRecoveryResponse(payload: unknown, status = 200) {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
  }

  it("shows the friendly explanation and retry action for automatic failed", async () => {
    renderControl(makeProgress(failedAutomatic));
    const popover = await openPopover();
    expect(popover.textContent).toContain(
      "部分解析没有完成，但正文和已完成内容仍可阅读。重新尝试不会重复扣费。",
    );
    expect(within(popover).getByRole("button", { name: "重新尝试解析" })).toBeTruthy();
  });

  it.each([
    ["queued"],
    ["processing"],
    ["waiting_user"],
    ["completed"],
    ["partial"],
    ["paused_quota"],
  ] as const)("does not show the recovery action for %s", async (status) => {
    renderControl(
      makeProgress({
        overall_status: status,
        translation_status: "failed" as const,
      }),
    );
    const popover = await openPopover();
    expect(within(popover).queryByRole("button", { name: "重新尝试解析" })).toBeNull();
    expect(popover.textContent).not.toContain("重新尝试不会重复扣费");
  });

  it("shows record-level recovery for segmented translation failure", async () => {
    renderControl(
      makeLegalSegmented({
        overall_status: "failed",
        translation_status: "failed" as const,
      }),
    );
    const popover = await openPopover();
    expect(within(popover).getByRole("button", { name: "重新尝试解析" })).toBeTruthy();
  });

  it("keeps section retry and hides record recovery for segmented section-only failures", async () => {
    renderControl(
      makeLegalSegmented(
        {
          overall_status: "failed",
          translation_status: "completed" as const,
        },
        [
          { status: "failed", can_start: true },
          { can_start: false },
        ],
      ),
    );
    const popover = await openPopover();
    expect(within(popover).getByRole("button", { name: "重试这一部分" })).toBeTruthy();
    expect(within(popover).queryByRole("button", { name: "重新尝试解析" })).toBeNull();
  });

  it("posts to the recovery URL without a body", async () => {
    mockRecoveryResponse({ ok: true, outcome: "recovery_started" });
    renderControl(makeProgress(failedAutomatic));
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      "/api/web/reader/records/rec_1/recovery",
      expect.objectContaining({ method: "POST" }),
    );
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });

  it("does not submit twice while recovery is in flight", async () => {
    let resolveFetch: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise<Response>((resolve) => {
            resolveFetch = resolve;
          }),
      ),
    );
    renderControl(makeProgress(failedAutomatic));
    await openPopover();
    const retry = screen.getByRole("button", { name: "重新尝试解析" });
    await userEvent.click(retry);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "正在重新尝试…" }).hasAttribute("disabled")).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "正在重新尝试…" })).catch(() => undefined);
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveFetch?.(
        new Response(JSON.stringify({ ok: true, outcome: "recovery_started" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
  });

  it("shows started feedback and reloads the snapshot once for recovery_started", async () => {
    mockRecoveryResponse({ ok: true, outcome: "recovery_started" });
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    renderControl(makeProgress(failedAutomatic), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "已重新开始解析，你可以继续阅读。",
    );
    expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
  });

  it("shows the no-op feedback and reloads the snapshot for nothing_to_recover", async () => {
    mockRecoveryResponse({ ok: true, outcome: "nothing_to_recover" });
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    renderControl(makeProgress(failedAutomatic), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "当前没有需要重试的解析，已刷新最新状态。",
    );
    expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1);
  });

  it("keeps the success feedback when the snapshot reload rejects", async () => {
    mockRecoveryResponse({ ok: true, outcome: "recovery_started" });
    const onRequestSnapshotReload = vi.fn().mockRejectedValue(new Error("reload failed"));
    renderControl(makeProgress(failedAutomatic), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    const feedback = await screen.findByTestId("reader-analysis-progress-feedback");
    expect(feedback.textContent).toBe("已重新开始解析，你可以继续阅读。状态暂未刷新，请稍后再试。");
    expect(feedback.textContent).toContain("已重新开始解析");
    expect(feedback.textContent).not.toContain("服务暂时不可用");
  });

  it("treats HTTP 200 with an unknown outcome as unavailable without reload", async () => {
    mockRecoveryResponse({ ok: true, outcome: "recovery_completed" });
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    renderControl(makeProgress(failedAutomatic), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "服务暂时不可用，请稍后重试。正文和已完成内容不会丢失。",
    );
    expect(onRequestSnapshotReload).toHaveBeenCalledTimes(0);
  });

  it("treats a forged success body on HTTP 500 as unavailable without reload", async () => {
    mockRecoveryResponse({ ok: true, outcome: "recovery_started" }, 500);
    const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
    renderControl(makeProgress(failedAutomatic), { onRequestSnapshotReload });
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "服务暂时不可用，请稍后重试。正文和已完成内容不会丢失。",
    );
    expect(onRequestSnapshotReload).toHaveBeenCalledTimes(0);
  });

  it.each([
    [401, "登录状态已失效，请重新登录后再试。"],
    [404, "没有找到这条阅读记录，请返回资料库确认后再试。"],
    [409, "当前状态暂时无法恢复。正文和已完成内容仍会保留，请稍后刷新。"],
    [503, "服务暂时不可用，请稍后重试。正文和已完成内容不会丢失。"],
    [500, "服务暂时不可用，请稍后重试。正文和已完成内容不会丢失。"],
  ] as const)("maps status %i to fixed copy", async (status, label) => {
    mockRecoveryResponse(
      { ok: false, status, message: "internal diagnostic probe-x9" },
      status,
    );
    renderControl(makeProgress(failedAutomatic));
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    const feedback = await screen.findByTestId("reader-analysis-progress-feedback");
    expect(feedback.textContent).toContain(label);
    expect(feedback.textContent).not.toContain("internal diagnostic probe-x9");
  });

  it("maps network failure to the unavailable copy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    renderControl(makeProgress(failedAutomatic));
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    expect((await screen.findByTestId("reader-analysis-progress-feedback")).textContent).toContain(
      "服务暂时不可用，请稍后重试。正文和已完成内容不会丢失。",
    );
  });

  it("maps malformed JSON to the unavailable copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>gateway error</html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      ),
    );
    renderControl(makeProgress(failedAutomatic));
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    const feedback = await screen.findByTestId("reader-analysis-progress-feedback");
    expect(feedback.textContent).toContain("服务暂时不可用，请稍后重试。");
    expect(feedback.textContent).not.toContain("gateway error");
  });

  it("never renders sensitive response text into the DOM", async () => {
    mockRecoveryResponse(
      {
        ok: false,
        status: 503,
        message: "SELECT secret FROM credentials -- probe-7f3a",
      },
      503,
    );
    renderControl(makeProgress(failedAutomatic));
    await openPopover();
    await userEvent.click(screen.getByRole("button", { name: "重新尝试解析" }));
    await screen.findByTestId("reader-analysis-progress-feedback");
    const popover = screen.getByTestId("reader-analysis-progress-popover");
    expect(popover.textContent).not.toContain("probe-7f3a");
    expect(popover.textContent).not.toContain("SELECT secret");
  });

  it("does not auto-open the popover for failed records", () => {
    renderControl(makeProgress(failedAutomatic));
    expect(screen.queryByTestId("reader-analysis-progress-popover")).toBeNull();
  });
});
