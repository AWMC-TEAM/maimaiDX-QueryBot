import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // 构建产物落到插件 static，供 bot Playwright 本地托管录制
  base: './',
  build: {
    outDir: path.resolve(__dirname, '../static/chart_preview'),
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
