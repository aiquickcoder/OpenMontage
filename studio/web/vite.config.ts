import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: `python -m studio serve` on :4760, then `npm run dev`
export default defineConfig({
  plugins: [react()],
  server: { port: 5175, proxy: { "/api": { target: "http://127.0.0.1:4760", changeOrigin: false } } },
  build: { outDir: "dist", sourcemap: false },
});
