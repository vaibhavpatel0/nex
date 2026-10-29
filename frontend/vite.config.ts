import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the API and WebSocket to the FastAPI backend on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/v1/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", globals: true, include: ["tests/**/*.test.tsx", "tests/**/*.test.ts"] },
});
