# 舞萌谱面预览（猜铺面录制用）

基于 [Pimeng/Maimai-Chart-Preview](https://github.com/Pimeng/Maimai-Chart-Preview)（落雪查分器公开谱面预览独立页）。

## 猜铺面录制模式

路径：`/record?song=<id>&kind=dx|standard&diff=2-6&duration=25&start=-1&hispeed=6`

- **不加载音乐**、不播放正解音、无背景视频 / 曲绘背景
- 仅黑底谱面动画；Playwright 读取 `window.__GUESS_CHART__.state`：
  `loading` → `ready` → `playing` → `done` / `error`

## 构建

```bash
cd chart_preview
npm install
npm run build
```

产物输出到 `../static/chart_preview/`，由 bot 本地静态服务提供给 Playwright。
