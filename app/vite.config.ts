import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base './' 让构建产物既能被 FastAPI 托管，也能被 Electron file:// 加载
export default defineConfig({
  plugins: [react()],
  base: "./",
  // 开发端口由 electron 启动时动态分配(spawn vite 时传 --port + --strictPort),
  // 这里不写死,免得和别的项目(Vite 默认也用 5173)撞口。直接 `vite` 跑才用默认。
  server: {
    host: "127.0.0.1",
  },
  build: {
    outDir: "dist",
    // 本地自用桌面应用，单包体积不敏感；调高阈值消除无害告警
    chunkSizeWarningLimit: 4000,
  },
});
