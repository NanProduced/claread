import type { Meta } from "@ladle/react"
import { Slider } from "."

export default {
  title: "Primitives/Slider",
} satisfies Meta

export const Default = () => <Slider label="字号大小" description="Reader 字号仅需轻量调节。" defaultValue={28} min={16} max={40} />
