"use client";

import { useRef } from "react";

import { cn } from "@/lib/cn";

type OtpInputProps = {
	value: string;
	onChange: (code: string) => void;
	onComplete?: (code: string) => void;
	length?: number;
	disabled?: boolean;
	invalid?: boolean;
	describedBy?: string;
};

const CELL_CLASS =
	"size-11 rounded-md border border-input bg-transparent text-center font-mono text-base shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive";

/**
 * Controlled 6-cell one-time-code input. Pure UI: digits only, auto-advance,
 * backspace/arrow navigation and full-code paste; emits onComplete once every
 * cell is filled. No network, timers or storage.
 */
export function OtpInput({
	value,
	onChange,
	onComplete,
	length = 6,
	disabled = false,
	invalid = false,
	describedBy,
}: OtpInputProps) {
	const cellsRef = useRef<Array<HTMLInputElement | null>>([]);
	const digits = Array.from({ length }, (_, index) => value[index] ?? "");

	function focusCell(index: number) {
		const cell = cellsRef.current[index];
		if (cell) {
			cell.focus();
			cell.select();
		}
	}

	function commit(nextValue: string, nextFocus: number) {
		onChange(nextValue);
		if (nextValue.length === length) {
			onComplete?.(nextValue);
		} else {
			focusCell(Math.min(nextFocus, length - 1));
		}
	}

	function handleChange(index: number, raw: string) {
		const cleaned = raw.replace(/\D/g, "");

		if (!cleaned) {
			// Cell cleared (e.g. backspace on a filled cell): drop that digit.
			commit(value.slice(0, index) + value.slice(index + 1), index);
			return;
		}

		const nextValue = (
			value.slice(0, index) +
			cleaned +
			value.slice(index + cleaned.length)
		).slice(0, length);
		commit(nextValue, index + cleaned.length);
	}

	function handleKeyDown(index: number, event: React.KeyboardEvent<HTMLInputElement>) {
		if (event.key === "Backspace" && !digits[index] && index > 0) {
			event.preventDefault();
			commit(value.slice(0, index - 1) + value.slice(index), index - 1);
			return;
		}

		if (event.key === "ArrowLeft" && index > 0) {
			event.preventDefault();
			focusCell(index - 1);
		}

		if (event.key === "ArrowRight" && index < length - 1) {
			event.preventDefault();
			focusCell(index + 1);
		}
	}

	function handlePaste(event: React.ClipboardEvent<HTMLDivElement>) {
		const pasted = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, length);
		if (!pasted) {
			return;
		}

		event.preventDefault();
		commit(pasted, pasted.length);
	}

	return (
		<div
			role="group"
			aria-label="一次性验证码"
			className="flex gap-2"
			onPaste={handlePaste}
		>
			{digits.map((digit, index) => (
				<input
					key={index}
					ref={(element) => {
						cellsRef.current[index] = element;
					}}
					aria-invalid={invalid || undefined}
					aria-label={`第 ${index + 1} 位，共 ${length} 位`}
					aria-describedby={describedBy}
					autoComplete={index === 0 ? "one-time-code" : "off"}
					className={cn(CELL_CLASS)}
					disabled={disabled}
					inputMode="numeric"
					onChange={(event) => handleChange(index, event.target.value)}
					onFocus={(event) => event.target.select()}
					onKeyDown={(event) => handleKeyDown(index, event)}
					type="text"
					value={digit}
				/>
			))}
		</div>
	);
}
