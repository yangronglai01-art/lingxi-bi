import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 本地开发：把 /api 代理到 FastAPI 后端（默认 8000）
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,

      },
    },
  },
  test: {
    environment: "node",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
