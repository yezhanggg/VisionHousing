/**
 * Vite settings for the VisionHousing interface.
 *
 * - `npm run dev` serves web/ with instant reload and passes /api/... to the FastAPI back end
 *   on 127.0.0.1:8000 (start it with `make app-api` or `visionhousing serve`).
 * - `npm run build` writes the finished site to web/dist/. FastAPI serves that folder at `/`.
 * - `npm run build:static` writes the no-server version (VITE_DATA_MODE=static) for Vercel.
 * - Files in web/public/ are copied into dist/ as they are. The static-site data
 *   (web/public/data/, written by `visionhousing export-static`) arrives that way.
 *
 * - `VITE_BASEMAP=off` makes a build start with the optional map basemap switched off
 *   (the browser tests use it so they never reach the internet); readers can still turn it on.
 *
 * No plugins: the screens use htm templates in plain JavaScript, so nothing needs compiling.
 */
import { defineConfig } from "vite";

const API_TARGET = process.env.VISIONHOUSING_API || "http://127.0.0.1:8000";

export default defineConfig(({ mode }) => {
  // `--mode static` (npm run build:static, used by vercel.json) builds the no-server site
  // that reads web/public/data/. It sets the switch here so no environment file is needed.
  if (mode === "static") process.env.VITE_DATA_MODE ||= "static";
  return config;
});

const config = {
  root: ".",
  base: "/",
  publicDir: "public",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
    // MapLibre is one large library loaded on demand by the map; do not warn about it.
    chunkSizeWarningLimit: 1200,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: false },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
};
