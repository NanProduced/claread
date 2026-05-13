# Claread Brand Assets

本目录存放 Claread 跨端品牌源资产和视觉参考素材。

这些文件是设计源资产，不是各端运行时代码的直接依赖。Web、小程序和未来 App 使用时，应按目标平台导出、压缩或复制到各自客户端目录。

## 目录

| 目录 | 用途 |
|------|------|
| `logos/` | Claread logo、横版 logo、正色 / 反白版本 |
| `icons/` | App icon、独立品牌图标和深色版本 |
| `design/` | 品牌探索、营销视觉和 UI 设计参考图 |

## 使用规则

- Web 端运行资产放到 `apps/web/public/brand/`。
- 小程序运行资产放到 `apps/miniprogram/src/assets/brand/`，并注意包体积。
- 本目录不应被客户端运行时代码直接 import。
- 大图优先作为设计参考，不直接进入运行包。
- 后续如有 SVG、Figma 导出、AI/PSD 等源文件，应保留在本目录或下级 `source/`，并谨慎控制文件体积。

## 命名规则

- 文件名使用小写 kebab-case。
- 文件名应表达用途、方向和颜色，例如：
  - `claread-primary-fullcolor.png`
  - `claread-primary-reversed.png`
  - `claread-horizontal-bilingual.png`
  - `claread-app-logo-dark.png`

## 当前资产

```text
design/
  claread-logo-analysis.png
  claread-logo-multi.png
  claread-marketing-1.png
icons/
  app-icon.png
  claread-app-logo-dark.png
  claread-icon-fullcolor.png
logos/
  claread-horizontal-bilingual.png
  claread-logo.png
  claread-primary-fullcolor.png
  claread-primary-reversed.png
```
