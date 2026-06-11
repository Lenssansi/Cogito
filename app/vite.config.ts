import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base './' 让构建产物既能被 FastAPI 托管，也能被 Electron file:// 加载
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    // 本地自用桌面应用，单包体积不敏感；调高阈值消除无害告警
    chunkSizeWarningLimit: 4000,
  },
});
