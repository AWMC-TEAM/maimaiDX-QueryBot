#!/usr/bin/env bash
# 构建猜铺面用的谱面预览静态页 → static/chart_preview/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/chart_preview"
if [[ ! -d node_modules ]]; then
  npm install --legacy-peer-deps
fi
npm run build
echo "OK: $ROOT/static/chart_preview"
