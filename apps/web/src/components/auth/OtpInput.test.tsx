/** @vitest-environment jsdom */
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OtpInput } from "./OtpInput";

afterEach(cleanup);

function Harness({
	onComplete,
	disabled = false,
	describedBy,
}: {
	onComplete?: (code: string) => void;
	disabled?: boolean;
	describedBy?: string;
}) {
	const [value, setValue] = useState("");
	return (
		<OtpInput
			value={value}
			onChange={setValue}
			onComplete={onComplete}
			disabled={disabled}
			describedBy={describedBy}
		/>
	);
}

describe("OtpInput", () => {
	it("renders six labelled digit cells inside a named group", () => {
		render(<Harness />);

		expect(screen.getByRole("group", { name: "一次性验证码" })).toBeTruthy();
		for (let index = 1; index <= 6; index += 1) {
			expect(screen.getByLabelText(`第 ${index} 位，共 6 位`)).toBeTruthy();
		}
	});

	it("auto-advances focus while typing digits", async () => {
		const user = userEvent.setup();
		render(<Harness />);

		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("12");

		expect(document.activeElement).toBe(screen.getByLabelText("第 3 位，共 6 位"));
		expect((screen.getByLabelText("第 1 位，共 6 位") as HTMLInputElement).value).toBe("1");
		expect((screen.getByLabelText("第 2 位，共 6 位") as HTMLInputElement).value).toBe("2");
	});

	it("ignores non-numeric input", async () => {
		const user = userEvent.setup();
		render(<Harness />);

		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("ab");

		expect((screen.getByLabelText("第 1 位，共 6 位") as HTMLInputElement).value).toBe("");
	});

	it("completes on a full six-digit paste", () => {
		const onComplete = vi.fn();
		render(<Harness onComplete={onComplete} />);

		fireEvent.paste(screen.getByLabelText("第 1 位，共 6 位"), {
			clipboardData: { getData: () => "483920" },
		});

		expect(onComplete).toHaveBeenCalledTimes(1);
		expect(onComplete).toHaveBeenCalledWith("483920");
		expect((screen.getByLabelText("第 6 位，共 6 位") as HTMLInputElement).value).toBe("0");
	});

	it("moves focus with backspace and arrow keys", async () => {
		const user = userEvent.setup();
		render(<Harness />);

		await user.click(screen.getByLabelText("第 1 位，共 6 位"));
		await user.keyboard("1");
		expect(document.activeElement).toBe(screen.getByLabelText("第 2 位，共 6 位"));

		await user.keyboard("{Backspace}");
		expect(document.activeElement).toBe(screen.getByLabelText("第 1 位，共 6 位"));

		await user.keyboard("{ArrowRight}");
		expect(document.activeElement).toBe(screen.getByLabelText("第 2 位，共 6 位"));
		await user.keyboard("{ArrowLeft}");
		expect(document.activeElement).toBe(screen.getByLabelText("第 1 位，共 6 位"));
	});

	it("disables every cell when disabled", () => {
		render(<Harness disabled />);

		for (let index = 1; index <= 6; index += 1) {
			expect((screen.getByLabelText(`第 ${index} 位，共 6 位`) as HTMLInputElement).disabled).toBe(
				true,
			);
		}
	});

	it("links an external description to every cell", () => {
		render(<Harness describedBy="email-auth-status" />);

		for (let index = 1; index <= 6; index += 1) {
			expect(
				screen.getByLabelText(`第 ${index} 位，共 6 位`).getAttribute("aria-describedby"),
			).toBe("email-auth-status");
		}
	});
});
