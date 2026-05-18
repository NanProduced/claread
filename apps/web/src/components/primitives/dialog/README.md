# Dialog

角色：阻断式确认、危险操作确认、轻量配置窗口。

推荐场景：删除阅读记录、退出登录确认、重要设置确认。

变体：
- `variant`: `default | quiet | danger`
- `size`: `sm | md | lg`

a11y：
- 默认要求标题语义
- 关闭路径包括 `Esc`、关闭按钮和受控关闭

底座：`@radix-ui/react-dialog`
