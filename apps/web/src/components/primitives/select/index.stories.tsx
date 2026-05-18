import type { Meta } from "@ladle/react"
import { Select } from "."

const items = [
  { label: "Warm Paper", value: "warm", description: "默认暖纸底色" },
  { label: "Clean White", value: "white" },
  { label: "Sage Green", value: "sage" },
]

export default {
  title: "Primitives/Select",
} satisfies Meta

export const Default = () => <Select label="背景纸色" items={items} defaultValue="warm" />

export const Disabled = () => <Select label="字号与行距" items={items} defaultValue="warm" disabled />
