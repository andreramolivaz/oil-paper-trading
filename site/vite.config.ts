import { defineConfig } from "vite";

/**
 * GitHub Pages serves the site from /<repo>/ unless a custom domain is configured, so the base path is
 * taken from VITE_BASE (the deploy workflow sets it). `dev-sample/` is intentionally OUTSIDE `public/`
 * so the sample files can never reach the production build.
 */
export default defineConfig({
  base: process.env.VITE_BASE ?? "/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    fs: { allow: [".."] },
  },
});
