import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies API/WS calls to a locally running Web Bridge so the
// console can be developed without rebuilding. The bridge default port is
// uvicorn's 8000; override with LIFEOS_BRIDGE_ORIGIN when it runs elsewhere.
const bridgeOrigin = process.env.LIFEOS_BRIDGE_ORIGIN || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/v1/health": { target: bridgeOrigin, changeOrigin: true },
      "/api/v1/devices": { target: bridgeOrigin, changeOrigin: true },
      "/api/v1/commands": { target: bridgeOrigin, changeOrigin: true },
      "/api/v1/usb-scan": { target: bridgeOrigin, changeOrigin: true },
      "/api/v1/usb-devices": { target: bridgeOrigin, changeOrigin: true },
      "/api/v1/events": { target: bridgeOrigin, ws: true, changeOrigin: true },
      "/api/v1/control": { target: bridgeOrigin, ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
