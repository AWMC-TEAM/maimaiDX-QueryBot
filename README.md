# nonebot-plugin-maimaidx

基于 [Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) 修改的 nonebot2 舞萌 DX 查分插件，由 [AWMC TEAM](https://github.com/AWMC-TEAM) 维护。

## 功能特性

- **成绩查询**：b50 / b40、定数查询、谱面详情、个人成绩
- **难度 / 版本筛选**：按难度（紫 / 13+ 等）或版本（镜代 / 爽代等）筛选 b50
- **历代版本 b50 / b35**：使用指定版本定数重算 rating
- **定数表 / 完成表**：等级定数表、等级完成表、牌子完成表（晓极完成表等）
- **进度与推荐**：牌子进度、等级进度、吃分推荐、弱项处方单、目标 Rating 沙盘、B50 风险预警、周报 / 月报 / 日报
- **数据存储**：开启本地成绩快照，支持存档查询与进步报告
- **群功能**：我在群里有多菜、群 rating 排行、群单曲排行、友人对战（含段位 CP）、对战战绩 Head-to-Head
- **PC 数系统**：机台登录、曲目 PC 数统计、PC 数排行榜
- **查分器上传**：水鱼查分器、落雪查分器 b50 上传；支持切换数据源
- **统一账号**：原 maibot 的账号绑定、Token、上传、票券与状态功能已合并，无需单独运行 Koishi Bot
- **管理审计**：统一 REF_ID 请求链路、敏感信息脱敏、用户封禁与内置管理 WebUI
- **倍率票 / 道具**：获取倍率票、查询票券、添加收藏品
- **谱面标签 / 印象**：dxrating 谱面标签、谱面印象 API
- **数据源切换**：水鱼 API 或本地 `dxdata.json`

## 安装

```bash
pip install nonebot-plugin-maimaidx
```

开发版可将本仓库文件覆盖到 bot 的插件目录。另需安装 Playwright Chromium：

```bash
playwright install --with-deps chromium
```

## 静态资源

1. 下载并解压静态资源包，得到 `static` 文件夹（含 `mai/`、`font/` 等）。
2. 在 `.env` 中配置 `MAIMAIDXPATH` 指向该目录的**绝对路径**。

| 文件 / 目录 | 说明 |
|-------------|------|
| `static/` | 插件静态资源根目录（**必须**） |
| `static/font/` | 字体（`ResourceHanRoundedCN-Bold.ttf` 等） |
| `dxdata.json` | 本地曲库数据源（可选，见下方配置） |

首次部署后，若 `rating` / `plate` 目录为空，需**私聊 Bot（超级用户）**执行：

- `更新定数表`
- `更新完成表`

否则「定数表」「完成表」类指令可能无法使用。

## 配置

配置写入 bot 根目录的 `.env` 或 `.env.prod`。NoneBot 会将 `config.py` 中的字段名转为**大写环境变量**注入（例如 `maimaidxpath` → `MAIMAIDXPATH`）。

### 必填

```env
# 静态资源目录（绝对路径）
MAIMAIDXPATH=/path/to/static
```

### 查分器 Token（水鱼开发者 Token）

用于 dev 接口、完成表、友人对战、数据存储等。**支持配置多个 Token**，用逗号或空格分隔；请求失败（token 有误 / 被禁用）时自动切换下一个，全部失败才报错。

```env
# 单个
MAIMAIDXTOKEN=your_token_here

# 多个（推荐，提高并发与容错）
MAIMAIDXTOKEN=token_a,token_b,token_c
```

### sw-api（PC 数 / 上传 / 倍率票）

```env
# 统一 AWMC API 配置（team=自建 sw-api，public=公共网关）
AWMC_API_MODE=team
AWMC_API_BASE_URL=http://127.0.0.1:5001
AWMC_PUBLIC_GATEWAY_TOKEN=
SDGBT_CLIENT_ID=your_keychip
SDGBT_REGION_ID=1
SDGBT_PLACE_ID=1403
```

旧变量 `SDGBTECHAPI` 仍兼容。完整模板见仓库根目录 `.env.example`。

PC 数拉取接口：`POST /awmc/api/v1/user/music`（JSON body，含 `qrcode` + `keychip`）。

### 落雪查分器（可选）

```env
LXNS_DEV_TOKEN=your_lxns_dev_token
LX_CLIENT_ID=your_oauth_client_id
LX_CLIENT_SECRET=your_oauth_client_secret
# 留空 = 无回调模式（用户在落雪页面直接看到授权码）
LX_REDIRECT_URI=
```

### 谱面标签（dxrating，可选）

未配置时谱面详情不显示 dxrating 标签。

```env
MAIMAIDX_DXRATING_TOKEN=your_dxrating_token
# 可选：自定义 combined-tags 接口地址
# MAIMAIDX_DXRATING_COMBINED_TAGS_URL=https://derrakuma.dxrating.net/functions/v1/combined-tags
```

### 谱面印象 API（可选）

```env
PMYX_API_BASE_URL=http://103.45.162.66:37913
```

### 数据源切换（可选）

```env
# 留空 = 使用水鱼查分器 API（默认）
# 设为 dxdata = 使用本地 dxdata.json，无需拉取曲库网络接口
MAIMAIDX_DATA_SOURCE=dxdata
MAIMAIDX_DXDATA_PATH=dxdata.json
```

### 缓存与限流（可选）

```env
# 我有多菜 / 群 rating 等缓存时长（秒），默认 900（15 分钟）
MAIMAIDX_RATING_CACHE_SECONDS=900

# 曲库 / 谱面 / 别名启动缓存（秒），默认 3600；设为 0 则每次启动都拉网络
MAIMAIDX_MUSIC_CACHE_SECONDS=3600

# 友人对战冷却（秒），默认 180（3 分钟）；设为 0 关闭
MAIMAIDX_FRIEND_BATTLE_COOLDOWN_SECONDS=180
```

### 管理 WebUI（可选）

```env
MAIMAIDX_ADMIN_WEB_ENABLED=true
MAIMAIDX_ADMIN_WEB_TOKEN=至少24位高强度随机字符串
MAIMAIDX_ADMIN_WEB_HOST=127.0.0.1
MAIMAIDX_ADMIN_WEB_PORT=8099
MAIMAIDX_ADMIN_WEB_PATH=/maimaidx/admin
MAIMAIDX_ADMIN_WEB_PUBLIC_URL=https://bot.example.com
MAIMAIDX_MESSAGE_STATS_ENABLED=true
MAIMAIDX_COMPACT_MESSAGES=true
```

WebUI 默认独立监听 `127.0.0.1:8099`，可以直接用 Nginx/Caddy 反向代理；设
`MAIMAIDX_ADMIN_WEB_PORT=0` 时才挂载到 NoneBot FastAPI Driver 的共享端口。
API 强制使用 Bearer Token，页面不会返回二维码、水鱼/落雪 Token 等原文。
管理员可在 Bot 内发送 `管理面板` 查看地址。
完整部署与安全说明见 `docs/WebUI配置说明.md`。

`MAIMAIDX_COMPACT_MESSAGES=true` 默认合并猜歌开场/结算、群排行摘要、凭据撤回警告
与业务结果，并省略非必要的“处理中”消息，以降低平台消息发送频率。二维码补交、
猜歌阶段提示等需要用户继续交互的消息不会被省略。

### 代理与其它（可选）

```env
# 查分器 / 别名库走代理（默认 false）
MAIMAIDXPROBERPROXY=false
MAIMAIDXALIASPROXY=false

# 图片页脚 Bot 名称（默认取 nonebot nickname）
BOTNAME=maimai

# 自定义背景图路径（相对 static 或绝对路径）
# MAIMAIDX_HOW_WEAK_BG=mai/pic/custom_weak_bg.png
# MAIMAIDX_TAG_ANALYSIS_BG=mai/pic/custom_tag_bg.png
```

## 命令列表

### 基础查询

| 命令 | 说明 |
|------|------|
| `b50` / `b40` | 查询 b50 / b40 |
| `id <歌曲id>` | 查询歌曲详情 |
| `<歌曲别名>是什么歌` | 通过别名查歌曲 |
| `帮助maimaiDX` | 查看帮助 |

### 难度 / 版本筛选

| 命令 | 说明 |
|------|------|
| `紫b50` / `13+b50` / `14.0b50` | 按难度筛选 b50 |
| `镜代b50` / `爽代b50` | 按版本筛选 b50 |
| `l镜代b50` / `l爽代b35` | 历代版本 b50 / b35 |
| `dx2025b50` | 读取 2026-06-09 本地存档，PRiSM 定数重算，分 B35/B15 展示 2025 版 Rating |

### 定数表 / 完成表 / 进度

| 命令 | 说明 |
|------|------|
| `13+定数表` | 查看等级定数表 |
| `13+完成表` / `13+ap完成表` | 查看等级完成表 |
| `晓极完成表` | 查看牌子完成表（版本 + 极 / 将 / 舞舞等） |
| `晓极进度` | 牌子进度 |
| `13+sss进度` | 等级进度 |
| `更新定数表` / `更新完成表` | 生成静态表图（超级用户，私聊） |

### 数据存储与报告

| 命令 | 说明 |
|------|------|
| `开启数据存储` | 开启本地成绩快照 |
| `立即存储数据` | 手动拉取并存档 |
| `周报` / `月报` / `日报` | 进步报告 |
| `今日吃分推荐` | 个性化推分推荐 |
| `弱项处方` | 根据 B50 底力短板标签推荐练习曲目 |
| `目标rating 16000` | 推分沙盘：估算达到目标 Rating 的改动方案 |
| `b50风险` | B50 风险预警（需开启数据存储） |

### 群功能

| 命令 | 说明 |
|------|------|
| `我有多菜` / `我在群里有多菜` | rating 对比 |
| `友人对战` | 群友随机对战（可选 `友人对战 300` 收紧 rating 差） |
| `对战战绩@某人` | Head-to-Head 重叠曲目胜率对比图 |
| `底力分析` | 谱面标签底力分析 |

### PC 数系统

| 命令 | 说明 |
|------|------|
| `更新pc数 <二维码>` | 机台登录并更新 PC 数 |
| `我的pc数` | 查看个人 PC 数统计 |
| `pc排行` | 全部用户 PC 数排行榜 |
| `pc50` / `pca50` | B50 内按 PC 排序 |
| `游玩排行50` | 游玩最多的 50 首谱面 |

### 查分器上传

| 命令 | 说明 |
|------|------|
| `dfbind <token>` | 绑定水鱼查分器 |
| `lxbind` | 绑定落雪查分器 |
| `上传水鱼 <二维码>` | 上传 b50 到水鱼 |
| `上传落雪 <二维码>` | 上传 b50 到落雪 |
| `数据源 落雪` | 切换个人数据源 |

### 统一账号（原 maibot）

| 命令 | 说明 |
|------|------|
| `mai账号` | 查看账号功能帮助 |
| `mai绑定 <二维码>` / `mai解绑` | 绑定或解绑舞萌账号 |
| `mai状态` | 查看绑定、Rating 与上传 Token 状态 |
| `mai绑定水鱼 <token>` | 绑定水鱼上传 Token |
| `mai绑定落雪 <导入token>` | 绑定落雪第三方导入 Token |
| `maiu` / `maiul` / `maiua` | 上传水鱼 / 落雪 / 同时上传 |
| `mai发票 <2-6>` / `mai查票` | 票券操作（team 模式） |
| `mai地图` / `maiping` | 游玩地区 / API 健康检查 |

绑定后执行 `更新pc数` 会直接使用已保存账号，不再要求重复发送二维码。
落雪查询 OAuth 仍使用 `lxbind`；它与 `mai绑定落雪` 的上传 Token 用途不同。

直接发送 `SGWCMAID...` 时，Bot 会先尝试撤回敏感消息，再同步 PC，并按用户
已绑定的水鱼/落雪 Token 自动上传。账号与 BREAK 功能首次使用前需发送
`用户协议`，阅读链接并完整输入网页确认词。

### 倍率票 / 道具

| 命令 | 说明 |
|------|------|
| `拿票 <二维码>` | 获取倍率票（2x~6x） |
| `/tk2` ~ `/tk6` | 快捷获取对应倍率票 |
| `查票 <二维码>` | 查询票券状态 |

## 开发

### 目录结构

```
nonebot_plugin_maimaidx/
├── command/              # 命令注册与处理
│   ├── mai_score.py      # b50、数据存储、友人对战等
│   ├── mai_table.py      # 定数表 / 完成表 / 进度
│   ├── mai_playcount.py  # PC 数 / SDGB / 倍率票
│   └── ...
├── libraries/            # 核心库
│   ├── maimaidx_api_data.py      # 查分器 API（含 token 池）
│   ├── maimaidx_best_50.py       # b50 绘图
│   ├── maimaidx_friend_battle.py # 友人对战
│   ├── maimaidx_sw_api.py        # sw-api 客户端
│   ├── maimaidx_sdgb_prober.py   # sw-api 上传/拿票封装
│   └── ...
├── data/                 # 运行时数据（快照、段位 CP 等）
└── config.py             # 配置定义
```

完整环境变量以 `config.py` 中 `Config` 类为准。

## 致谢

- 原项目：[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
- 维护：[AWMC TEAM](https://github.com/AWMC-TEAM/maimaiDX-QueryBot)
- 数据源：[Diving-Fish/maimaidx-prober](https://github.com/Diving-Fish/maimaidx-prober)
- 定数数据：dxdata.json 社区维护版本
