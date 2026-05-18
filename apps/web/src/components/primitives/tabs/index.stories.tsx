import type { Meta } from "@ladle/react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "."

export default {
  title: "Primitives/Tabs",
} satisfies Meta

export const Default = () => (
  <Tabs defaultValue="daily" className="max-w-md">
    <TabsList>
      <TabsTrigger value="daily">日常阅读</TabsTrigger>
      <TabsTrigger value="academic">学术摘要</TabsTrigger>
    </TabsList>
    <TabsContent value="daily" className="text-sm text-muted">
      适合功能页筛选和 Reader 模式切换。
    </TabsContent>
    <TabsContent value="academic" className="text-sm text-muted">
      激活态更接近编辑标签，而不是后台 pill。
    </TabsContent>
  </Tabs>
)
