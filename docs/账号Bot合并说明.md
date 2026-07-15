# 账号 Bot 合并说明

原 `koishi-plugin-maibot` 为 Koishi/TypeScript 插件，QueryBot 为
NoneBot/Python 插件，因此本次采用业务移植，而不是在一个进程中同时运行两套框架。

## 合并后的职责

- `config.py`：查询、账号、AWMC API、平台与缓存的统一配置模型。
- `.env.example`：部署时唯一需要参考的配置模板。
- `libraries/maimaidx_account_db.py`：舞萌账号、二维码及水鱼/落雪上传 Token。
- `libraries/maimaidx_sw_api.py`：team/public 两种 AWMC API 调用。
- `command/mai_account.py`：账号和上传指令。
- `libraries/maimaidx_break.py`：继续独立负责 BREAK，不迁入账号表。

## 用户指令

| 指令 | 说明 |
|---|---|
| `mai账号` | 查看合并后的账号帮助 |
| `mai绑定 <SGWCMAID...>` | 绑定并验证舞萌账号 |
| `mai解绑` | 解绑街机账号，保留上传 Token |
| `mai状态` | 查看账号与 Token 状态 |
| `mai绑定水鱼 <Token>` | 保存水鱼上传 Token |
| `mai绑定落雪 <导入Token>` | 保存落雪第三方导入 Token |
| `maiu` / `maiul` / `maiua` | 上传水鱼 / 落雪 / 同时上传 |
| `更新pc数` | 已绑定后可直接刷新，不必再次发送二维码 |
| `mai发票 <2-6>` / `mai查票` | 发放及查询票券（team 模式） |
| `mai地图` | 查询游玩地区（team 模式） |
| `maiping` / `maiqueue` | 健康检查 / 队列状态 |

`lxbind` 仍表示落雪 OAuth 查询绑定；`mai绑定落雪` 表示原 maibot 的落雪
导入 Token，两者用途不同。

## 兼容策略

- 旧 `SDGBTECHAPI` 仍有效；若同时设置 `AWMC_API_BASE_URL`，以后者为准。
- `team` 模式复用现有 `/awmc/api/v1/...` 服务。
- `public` 模式使用 Bearer Token；不支持的票券、PC、地图功能会明确提示。
- QueryBot 现有 BREAK、查分、猜歌、友人对战数据库均保持原路径，不做破坏性迁移。

## 未照搬的 Koishi 专属功能

以下功能依赖 Koishi authority、bind 插件或 Koishi 数据库语义，不能直接复制：

- 优先授权卡密、群组换绑和 Koishi authority 自动优先
- QQ Markdown Keyboard 的 Koishi 交互实现
- 保护/锁定、手工改分、清收藏品等内部维护指令

这些功能应按 QueryBot 的管理员权限与 BREAK 经济重新设计，不建议照搬旧实现。

## 旧数据

拿到 Koishi 数据库后，可使用超级管理员指令 `迁移Koishi 检查 koishi.db` 预检，
再用 `迁移Koishi 确认 koishi.db` 导入；也可运行
`scripts/migrate_maibot_accounts.py` 导入账号、二维码、上传 Token 与用户协议记录。
可以直接传入包含多个 Koishi 插件表的完整 SQLite 数据库：迁移器以只读方式打开源库，
只读取 `maibot_bindings`、可选的 `maibot_user_terms` 和用于身份解析的 `binding`，
其它插件表不会读写或删除。`binding(aid,pid)` 可自动把 Koishi 内部 ID 映射回 QQ；
无法唯一映射的记录需提供 `identity-map.json`。
