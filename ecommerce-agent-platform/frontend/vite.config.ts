import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the API and the event WebSocket to the shop backend on port 8100.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api/ws": { target: "ws://127.0.0.1:8100", ws: true },
      "/api": { target: "http://127.0.0.1:8100", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", globals: true, include: ["tests/**/*.test.tsx", "tests/**/*.test.ts"] },
});
