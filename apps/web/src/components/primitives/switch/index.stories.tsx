import type { Meta } from "@ladle/react"
import { Switch } from "."

export default {
  title: "Primitives/Switch",
} satisfies Meta

export const Default = () => <Switch aria-label="切换译文显示" defaultChecked />
