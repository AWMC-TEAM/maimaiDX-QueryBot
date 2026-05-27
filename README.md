# nonebot-plugin-maimaidx

基于 [Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) 修改的 nonebot2 舞萌 DX 查分插件。

## 功能特性

- **成绩查询**：b50/b40、定数查询、谱面详情、个人成绩
- **难度筛选**：支持按难度（紫/13/13+等）筛选 b50
- **版本筛选**：支持按版本（镜代/爽代等）筛选 b50
- **历代版本 b50/b35**：使用指定版本的定数重新计算 rating
- **PC 数系统**：机台登录、曲目 PC 数统计、PC 数排行榜
- **查分器上传**：水鱼查分器、落雪查分器 b50 上传
- **倍率票/道具**：获取倍率票、查询票券、添加收藏品
- **定数变化图**：查询歌曲时自动绘制难度变化曲线
- **数据源切换**：支持水鱼 API 或本地 dxdata.json

## 安装

```bash
pip install nonebot-plugin-maimaidx
```

然后将本仓库文件覆盖到插件目录（开发版 → venv 版）。

## 文件放置

| 文件 | 位置 | 说明 |
|------|------|------|
| `GenSenMaruGothicTW-Regular.ttf` | `bot根目录/static/` | 字体文件 |
| `dxdata.json` | `bot根目录/` 或自定义 | 本地数据源（可选） |

## 配置

在 `.env` 或 `.env.prod` 中添加：

```env
# 查分器 Token（必须）
MAIMAIDXTOKEN=your_token_here

# SDGBTECHAPI 配置（PC数/上传/倍率票功能需要）
SDGBTECHAPI=http://127.0.0.1:5566
SDGBT_CLIENT_ID=your_client_id
SDGBT_REGION_ID=1
SDGBT_PLACE_ID=1403
SDGBT_REGION_NAME=北京
SDGBT_PLACE_NAME=默认机台

# 谱面印象 API
PMYX_API_BASE_URL=http://103.45.162.66:37913

# 数据源切换（可选，默认使用水鱼API）
MAIMAIDX_DATA_SOURCE=dxdata
MAIMAIDX_DXDATA_PATH=dxdata.json
```

## 命令列表

### 基础查询
| 命令 | 说明 |
|------|------|
| `b50` | 查询 b50 |
| `b40` | 查询 b40 |
| `id <歌曲id>` | 查询歌曲详情 |
| `<歌曲别名>是什么歌` | 通过别名查歌曲 |

### 难度/版本筛选
| 命令 | 说明 |
|------|------|
| `紫b50` / `13+b50` / `14.0b50` | 按难度筛选 b50 |
| `镜代b50` / `爽代b50` | 按版本筛选 b50 |
| `l镜代b50` / `l爽代b35` | 历代版本 b50/b35（使用版本定数重算） |
| `dx2026b35` | 等价于 `l彩代b35` |

### PC 数系统
| 命令 | 说明 |
|------|------|
| `更新pc数 <二维码>` | 机台登录并更新 PC 数 |
| `我的pc数` | 查看个人 PC 数统计 |
| `pc排行` | 查看 PC 数排行榜 |
| `pc数 <歌曲id>` | 查看指定歌曲 PC 数 |
| `pc50` / `pca50` | PC 数 b50 / 全难度 PC 数 b50 |

### 查分器上传
| 命令 | 说明 |
|------|------|
| `dfbind <token>` | 绑定水鱼查分器 |
| `lxbind <token>` | 绑定落雪查分器 |
| `上传水鱼 <二维码>` | 上传 b50 到水鱼查分器 |
| `上传落雪 <二维码>` | 上传 b50 到落雪查分器 |

### 倍率票/道具
| 命令 | 说明 |
|------|------|
| `拿票 <二维码>` | 获取倍率票（2x~6x） |
| `/tk2` ~ `/tk6` | 快捷获取对应倍率票 |
| `查票 <二维码>` | 查询票券状态 |
| `add <item_id> <kind> <stock> <二维码>` | 添加收藏品 |

### 其他
| 命令 | 说明 |
|------|------|
| `verify` | 白名单验证 |
| `定数 <定数>` | 查询指定定数曲目 |
| `分数线 <歌曲id> <难度>` | 计算分数线 |

## 开发

### 目录结构

```
nonebot_plugin_maimaidx/
├── command/          # 命令注册与处理
│   ├── mai_playcount.py    # PC数/SDGB/倍率票/道具
│   ├── mai_score.py        # b50/难度筛选/版本筛选
│   └── ...
├── libraries/        # 核心库
│   ├── maimaidx_best_50.py     # b50 绘图
│   ├── maimaidx_b50_pipeline.py # b50 生成管道
│   ├── maimaidx_sdgb_prober.py # SDGBTECHAPI 客户端
│   ├── maimaidx_playcount_db.py # PC数数据库
│   ├── maimaidx_version_alias.py # 版本代号映射
│   └── ...
├── data/             # 运行时数据
│   ├── playcount/    # PC数数据库
│   ├── whitelist/    # 白名单数据库
│   └── user_scores/  # 用户成绩缓存
└── config.py         # 配置定义
```

## 致谢

- 原项目：[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
- 数据源：[ diving-fish/maimaidx-prober](https://github.com/Diving-Fish/maimaidx-prober)
- 定数数据：dxdata.json 社区维护版本
