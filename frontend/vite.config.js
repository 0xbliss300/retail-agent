import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 转发到本地 FastAPI，前端代码里只写相对路径。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
