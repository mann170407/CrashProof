import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev / preview: Vite proxies
//   /api        -> Windows backend  http://127.0.0.1:8765/api
//   /api-linux  -> Linux (WSL2)     http://127.0.0.1:8766/api
// Build: static files in dist/, served by ui/server.py on :8765 (browser then calls :8766 directly).
const sse: ProxyOptions["configure"] = (proxy) => {
  proxy.on("proxyRes", (res) => {
    if (String(res.headers["content-type"] || "").includes("text/event-stream")) {
      res.headers["cache-control"] = "no-cache";
      res.headers["x-accel-buffering"] = "no";
    }
  });
};

const proxy: Record<string, ProxyOptions> = {
  "/api-linux": {
    target: "http://127.0.0.1:8766",
    changeOrigin: true,
    rewrite: (p) => p.replace(/^\/api-linux/, "/api"),
    configure: sse,
  },
  "/api": { target: "http://127.0.0.1:8765", changeOrigin: true, configure: sse },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
