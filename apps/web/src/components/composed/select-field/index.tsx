"use client";

import type { SelectOption, SelectProps } from "@/components/primitives/select";
import { Select } from "@/components/primitives/select";

export interface SelectFieldProps
  extends Pick<SelectProps, "value" | "defaultValue" | "onValueChange" | "disabled" | "name"> {
  label: string;
  description: string;
  items: SelectOption[];
  placeholder?: string;
}

export function SelectField({
  label,
  description,
  items,
  value,
  defaultValue,
  onValueChange,
  disabled,
  name,
  placeholder,
}: SelectFieldProps) {
  return (
    <div className="grid gap-3 border-t border-hairline py-5 md:grid-cols-[minmax(0,1fr)_220px] md:items-center">
      <div>
        <h3 className="text-sm font-semibold text-ink">{label}</h3>
        <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
      </div>
      <Select
        items={items}
        value={value}
        defaultValue={defaultValue}
        onValueChange={onValueChange}
        disabled={disabled}
        name={name}
        placeholder={placeholder}
        aria-label={label}
      />
    </div>
  );
}
